import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_short import PROJECT_DIR, project_path, require_executable

QUEUE_FILE = PROJECT_DIR / "videos.json"
OUTPUT_DIR = PROJECT_DIR / "output"
TEMP_DIR = PROJECT_DIR / "temp_comp"
TRANSITION_SECONDS = 0.6


def get_duration(file_path: Path) -> float:
    require_executable("ffprobe")
    result = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ],
        text=True,
    )
    duration = float(result.strip())
    if duration <= 0:
        raise ValueError(f"Video has an invalid duration: {file_path}")
    return duration


def format_chapter_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes:02d}:{seconds_part:02d}"


def load_queue(queue_file: Path) -> list[dict[str, Any]]:
    with queue_file.open("r", encoding="utf-8") as file:
        queue = json.load(file)
    if not isinstance(queue, list) or any(not isinstance(item, dict) for item in queue):
        raise ValueError(f"{queue_file} must contain a JSON array of video objects.")
    return queue


def save_queue(queue_file: Path, queue: list[dict[str, Any]]) -> None:
    temp_file = queue_file.with_name(f"{queue_file.name}.tmp")
    temp_file.write_text(
        json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temp_file.replace(queue_file)


def select_candidates(
    queue: list[dict[str, Any]], output_dir: Path, limit: int = 6
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in queue
        if item.get("status") == "done"
        and not item.get("compiled")
        and isinstance(item.get("id"), str)
        and (output_dir / f"{item['id']}.mp4").is_file()
    ]
    return candidates[-limit:]


def make_filter_graph(
    durations: list[float], transition_seconds: float
) -> tuple[str, str, float, list[float]]:
    if len(durations) < 1:
        raise ValueError("At least one clip duration is required.")
    if any(duration <= transition_seconds for duration in durations[:-1]):
        raise ValueError("Each clip before the last must exceed the crossfade duration.")

    graph: list[str] = []
    starts: list[float] = []
    for index, duration in enumerate(durations):
        graph.extend(
            [
                f"[{index}:v]split=2[bg{index}][fg{index}]",
                f"[bg{index}]scale=1920:1080:force_original_aspect_ratio=increase,"
                f"crop=1920:1080,boxblur=25:5[blur{index}]",
                f"[fg{index}]scale=-2:1080[front{index}]",
                f"[blur{index}][front{index}]overlay=(W-w)/2:(H-h)/2,"
                f"setsar=1,fps=30,format=yuv420p,settb=AVTB,"
                f"setpts=PTS-STARTPTS,trim=duration={duration:.6f}[v{index}]",
                f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS,"
                f"atrim=duration={duration:.6f}[a{index}]",
            ]
        )

    video_label = "v0"
    audio_label = "a0"
    elapsed = durations[0]
    for index in range(1, len(durations)):
        starts.append(elapsed - transition_seconds)
        next_video = f"vx{index}"
        next_audio = f"ax{index}"
        graph.append(
            f"[{video_label}][v{index}]xfade=transition=fade:"
            f"duration={transition_seconds:.3f}:"
            f"offset={elapsed - transition_seconds:.6f}[{next_video}]"
        )
        graph.append(
            f"[{audio_label}][a{index}]acrossfade=d={transition_seconds:.3f}:"
            f"c1=tri:c2=tri[{next_audio}]"
        )
        video_label = next_video
        audio_label = next_audio
        elapsed += durations[index] - transition_seconds

    return ";".join(graph), video_label, elapsed, starts


def build_saturday_compilation(
    queue_file: Path = QUEUE_FILE,
    output_dir: Path = OUTPUT_DIR,
    temp_dir: Path = TEMP_DIR,
    publish_at_utc: str | None = None,
    upload_enabled: bool = False,
) -> Path | None:
    require_executable("ffmpeg")
    require_executable("ffprobe")
    queue = load_queue(queue_file)
    candidates = select_candidates(queue, output_dir)
    if len(candidates) < 3:
        print("Not enough new Shorts for a compilation (need at least 3).")
        return None

    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = [output_dir / f"{item['id']}.mp4" for item in candidates]
    durations = [get_duration(source) for source in sources]
    graph, video_label, total_duration, chapter_starts = make_filter_graph(
        durations, TRANSITION_SECONDS
    )
    command = ["ffmpeg", "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            f"[{video_label}]",
            "-map",
            f"[{audio_label_for(len(sources))}]",
            "-t",
            f"{total_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        ]
    )
    compilation = output_dir / f"weekly_compilation_{candidates[-1]['id']}.mp4"
    command.append(str(compilation))
    subprocess.run(command, check=True)

    chapters = ["00:00 Start"]
    for item, start in zip(candidates[1:], chapter_starts):
        title = str(item.get("title", item["id"]))
        title = title.replace("#Shorts", "").replace("#shorts", "").strip()
        chapters.append(f"{format_chapter_time(start)} {title}")

    clean_titles = [
        str(item.get("title", item["id"]))
        .replace("#Shorts", "")
        .replace("#shorts", "")
        .strip()
        for item in candidates
    ]
    title = f"Weekly Shorts: {clean_titles[0]} and more"
    title = title[:100]
    description = (
        "A weekly collection of our latest Shorts.\n\n"
        "CHAPTERS\n"
        + "\n".join(chapters)
        + "\n\n#Shorts #technology #productivity"
    )

    if upload_enabled:
        if publish_at_utc is None:
            raise ValueError("Set publish_at_utc when uploading the weekly compilation.")
        from youtube_publisher import publish_video

        video_id = publish_video(
            {
                "title": title,
                "description": description,
                "tags": ["weekly compilation", "technology", "productivity"],
                "publish_at_utc": publish_at_utc,
            },
            compilation,
        )
        print(f"Scheduled weekly compilation: https://youtu.be/{video_id}")
        for item in candidates:
            item["compiled"] = True
        save_queue(queue_file, queue)

    print(f"Saved weekly compilation to {compilation}")
    return compilation


def audio_label_for(count: int) -> str:
    return "a0" if count == 1 else f"ax{count - 1}"


def get_next_saturday_utc(hour_utc: int = 15) -> str:
    if not 0 <= hour_utc <= 23:
        raise ValueError("hour_utc must be between 0 and 23.")
    now = datetime.now(timezone.utc)
    days_until_saturday = (5 - now.weekday()) % 7
    target_saturday = (now + timedelta(days=days_until_saturday)).replace(
        hour=hour_utc, minute=0, second=0, microsecond=0
    )
    if target_saturday <= now:
        days_until_saturday = 7
        target_saturday = (now + timedelta(days=days_until_saturday)).replace(
            hour=hour_utc, minute=0, second=0, microsecond=0
        )
    return target_saturday.strftime("%Y-%m-%dT%H:%M:%SZ")


def next_saturday_utc(hour: int = 15) -> str:
    return get_next_saturday_utc(hour_utc=hour)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a 16:9 weekly compilation from completed Shorts."
    )
    parser.add_argument("--queue", type=Path, default=QUEUE_FILE)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-at", help="Future UTC ISO-8601 upload time.")
    args = parser.parse_args()
    queue_file = project_path(args.queue)
    publish_at = args.publish_at or get_next_saturday_utc(hour_utc=15)
    print(f"Targeting Saturday slot: {publish_at}")
    build_saturday_compilation(
        queue_file=queue_file,
        upload_enabled=args.publish,
        publish_at_utc=publish_at,
    )


if __name__ == "__main__":
    main()
