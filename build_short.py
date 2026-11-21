import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import edge_tts
from env_loader import load_project_env

PROJECT_DIR = Path(__file__).resolve().parent
load_project_env(PROJECT_DIR)
QUEUE_FILE = PROJECT_DIR / "videos.json"
OUTPUT_DIR = PROJECT_DIR / "output"
TEMP_DIR = PROJECT_DIR / "temp"
BG_MUSIC = PROJECT_DIR / "bg_music.mp3"
VOICE = "en-US-AndrewNeural"
SPEECH_RATE = "+8%"


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_DIR / path


def require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise FileNotFoundError(
            f"'{name}' was not found on PATH. Install FFmpeg and make sure its bin "
            "directory is on PATH."
        )


def format_ass_time(ticks_100ns: int) -> str:
    total_centiseconds = max(0, ticks_100ns // 100_000)
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


async def generate_tts_with_dynamic_ass(
    text: str, voice: str, audio_out: Path, ass_out: Path
) -> None:
    audio_out.parent.mkdir(parents=True, exist_ok=True)
    ass_out.parent.mkdir(parents=True, exist_ok=True)
    audio_tmp = audio_out.with_name(f"{audio_out.stem}.partial{audio_out.suffix}")
    ass_tmp = ass_out.with_name(f"{ass_out.stem}.partial{ass_out.suffix}")
    words: list[dict[str, int | str]] = []

    try:
        communicate = edge_tts.Communicate(
            text, voice, rate=SPEECH_RATE, boundary="WordBoundary"
        )
        with audio_tmp.open("wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word = str(chunk["text"]).strip()
                    if word:
                        words.append(
                            {
                                "text": word,
                                "offset": int(chunk["offset"]),
                                "duration": int(chunk["duration"]),
                            }
                        )

        if not audio_tmp.is_file() or audio_tmp.stat().st_size == 0:
            raise RuntimeError("Edge TTS completed without producing audio.")
        if not words:
            raise RuntimeError("Edge TTS returned no word-boundary timestamps.")

        ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,40,40,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: list[str] = []
        group_size = 3
        for group_start in range(0, len(words), group_size):
            group = words[group_start : group_start + group_size]
            for active_index, active_word in enumerate(group):
                absolute_index = group_start + active_index
                start = int(active_word["offset"])
                if absolute_index + 1 < len(words):
                    end = int(words[absolute_index + 1]["offset"])
                else:
                    end = start + int(active_word["duration"])
                if end <= start:
                    continue

                displayed_words = []
                for index, word in enumerate(group):
                    escaped_word = escape_ass_text(str(word["text"]).upper())
                    if index == active_index:
                        displayed_words.append(
                            r"{\c&H0000FFFF&\fscx110\fscy110}"
                            + escaped_word
                            + r"{\rDefault}"
                        )
                    else:
                        displayed_words.append(escaped_word)
                events.append(
                    "Dialogue: 0,"
                    f"{format_ass_time(start)},{format_ass_time(end)},"
                    "Default,,0,0,0,,"
                    + " ".join(displayed_words)
                )

        if not events:
            raise RuntimeError("Edge TTS returned unusable word timings.")

        ass_tmp.write_text(ass_header + "\n".join(events) + "\n", encoding="utf-8")
        os.replace(audio_tmp, audio_out)
        os.replace(ass_tmp, ass_out)
    except Exception:
        audio_tmp.unlink(missing_ok=True)
        ass_tmp.unlink(missing_ok=True)
        raise


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
        raise ValueError(f"Audio has an invalid duration: {file_path}")
    return duration


def escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    for character in ("\\", "'", ":", ",", "[", "]"):
        value = value.replace(character, f"\\{character}")
    return f"ass=filename='{value}'"


def render_video(
    raw_vid: Path,
    voice_mp3: Path,
    ass_file: Path,
    out_mp4: Path,
    bg_music: Path | None = None,
) -> None:
    require_executable("ffmpeg")
    duration = get_duration(voice_mp3) + 0.4
    vf_chain = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,fps=30,{escape_filter_path(ass_file)}"
    )
    cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw_vid), "-i", str(voice_mp3)]

    if bg_music and bg_music.is_file():
        cmd.extend(["-stream_loop", "-1", "-i", str(bg_music)])
        filter_complex = (
            f"[0:v]{vf_chain}[v];"
            "[1:a]volume=1.3[vo];"
            "[2:a]volume=0.08[bgm];"
            "[vo][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        cmd.extend(["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"])
    else:
        cmd.extend(["-vf", vf_chain, "-map", "0:v", "-map", "1:a"])

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd.extend(
        [
            "-t",
            str(duration),
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
            str(out_mp4),
        ]
    )
    subprocess.run(cmd, check=True)


def record_web_demo(urls: list[str], raw_out_path: Path) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for URL demos. Install it with "
            "'pip install playwright' and run 'playwright install chromium'."
        ) from exc

    raw_out_path.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    recorded_path: Path | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1080, "height": 1920},
            device_scale_factor=1,
            record_video_dir=str(TEMP_DIR),
            record_video_size={"width": 1080, "height": 1920},
        )
        page = context.new_page()
        try:
            for url in urls:
                print(f"   [Playwright] Recording {url}...")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page_text = page.locator("body").inner_text(timeout=5_000).lower()
                if any(
                    marker in page_text
                    for marker in (
                        "unusual traffic",
                        "verify you are human",
                        "complete the security check",
                    )
                ):
                    print(
                        f"   [Playwright Warning] {url} returned an anti-bot page; "
                        "the recording may not show the intended site."
                    )
                page.wait_for_timeout(1_500)
                for _ in range(4):
                    page.mouse.wheel(0, 350)
                    page.wait_for_timeout(900)
            if page.video is None:
                raise RuntimeError("Playwright did not create a browser recording.")
        finally:
            try:
                context.close()
                if page.video is not None:
                    recorded_path = Path(page.video.path())
            finally:
                browser.close()

    if recorded_path is None or not recorded_path.is_file():
        raise FileNotFoundError("Playwright recording file was not created.")
    shutil.move(str(recorded_path), str(raw_out_path))
    return raw_out_path


def load_queue(queue_file: Path) -> list[dict[str, Any]]:
    with queue_file.open("r", encoding="utf-8") as file:
        queue = json.load(file)
    if not isinstance(queue, list) or any(not isinstance(item, dict) for item in queue):
        raise ValueError(f"{queue_file} must contain a JSON array of video objects.")
    return queue


def save_queue(queue_file: Path, queue: list[dict[str, Any]]) -> None:
    temp_file = queue_file.with_name(f"{queue_file.name}.tmp")
    temp_file.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp_file, queue_file)


def validate_video_id(video_id: Any) -> str:
    value = str(video_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Invalid video id {value!r}; use letters, numbers, '_' or '-'.")
    return value


def resolve_raw_video(item: dict[str, Any], video_id: str, item_temp: Path) -> Path:
    local_video = item.get("local_video")
    if local_video:
        path = project_path(local_video)
        if path.is_file():
            print(f"   [Source] Using manual recording: {path}")
            return path

    urls = item.get("urls")
    if urls:
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            raise ValueError(f"{video_id}: 'urls' must be a list of URL strings.")
        raw_video = item_temp / f"{video_id}_raw.webm"
        return record_web_demo(urls, raw_video)

    broll_queries = item.get("broll_queries")
    if broll_queries:
        if not isinstance(broll_queries, list) or not all(
            isinstance(query, str) for query in broll_queries
        ):
            raise ValueError(f"{video_id}: 'broll_queries' must be a list of strings.")
        from broll_fetcher import fetch_and_stitch_broll

        raw_video = item_temp / f"{video_id}_broll.mp4"
        return fetch_and_stitch_broll(broll_queries, raw_video, item_temp)

    images = item.get("images")
    if images:
        if not isinstance(images, list) or not all(isinstance(image, str) for image in images):
            raise ValueError(f"{video_id}: 'images' must be a list of image paths.")
        from broll_fetcher import images_to_ken_burns_video

        image_paths = [project_path(image) for image in images]
        raw_video = item_temp / f"{video_id}_ken_burns.mp4"
        return images_to_ken_burns_video(image_paths, raw_video, item_temp)

    raise ValueError(
        f"{video_id} needs an existing 'local_video', 'urls', 'broll_queries', or 'images'."
    )


def process_queue(queue_file: Path, publish: bool) -> None:
    queue = load_queue(queue_file)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    for item in queue:
        status = item.get("status", "pending")
        if status == "done" or (status != "pending" and not (publish and status == "rendered")):
            continue

        try:
            video_id = validate_video_id(item.get("id"))
            output_video = OUTPUT_DIR / f"{video_id}.mp4"
            rendered_path = item.get("rendered_video")
            if status == "rendered":
                candidate = project_path(rendered_path) if rendered_path else output_video
                if candidate.is_file():
                    output_video = candidate
                else:
                    item["status"] = "pending"
                    status = "pending"

            if status == "pending":
                script = item.get("script")
                if not isinstance(script, str) or not script.strip():
                    raise ValueError(f"{video_id} is missing a non-empty 'script'.")
                item_temp = TEMP_DIR / video_id
                item_temp.mkdir(parents=True, exist_ok=True)
                raw_video = resolve_raw_video(item, video_id, item_temp)
                voice_path = item_temp / f"{video_id}_voice.mp3"
                ass_path = item_temp / f"{video_id}_captions.ass"
                voice = item.get("voice", VOICE)

                print(f"[{video_id}] Generating voiceover and captions...")
                asyncio.run(generate_tts_with_dynamic_ass(script, voice, voice_path, ass_path))
                print(f"[{video_id}] Rendering vertical Short...")
                render_video(raw_video, voice_path, ass_path, output_video, BG_MUSIC)
                item["status"] = "rendered"
                item["rendered_video"] = output_video.relative_to(PROJECT_DIR).as_posix()
                save_queue(queue_file, queue)
                print(f"[{video_id}] Saved {output_video}")

            if publish:
                if item.get("youtube_video_id"):
                    item["status"] = "done"
                    save_queue(queue_file, queue)
                    print(f"[{video_id}] Already uploaded: {item['youtube_video_id']}")
                    continue

                from youtube_publisher import publish_video

                print(f"[{video_id}] Scheduling upload to YouTube...")
                youtube_video_id = publish_video(item, output_video)
                item["youtube_video_id"] = youtube_video_id
                item["status"] = "done"
                save_queue(queue_file, queue)
                print(f"[{video_id}] Scheduled as https://youtu.be/{youtube_video_id}")
        except Exception as exc:
            item["status"] = "error"
            item["error_msg"] = str(exc)
            save_queue(queue_file, queue)
            print(f"[{item.get('id', '<unknown>')}] Error: {exc}")


async def run_single_video() -> None:
    script_file = PROJECT_DIR / "script.txt"
    raw_video = PROJECT_DIR / "raw_screen.mp4"
    if not script_file.is_file():
        raise FileNotFoundError(f"Single-video mode needs {script_file}.")
    if not raw_video.is_file():
        raise FileNotFoundError(f"Single-video mode needs {raw_video}.")

    script_text = script_file.read_text(encoding="utf-8").strip()
    voice_path = PROJECT_DIR / "temp_voice.mp3"
    ass_path = PROJECT_DIR / "temp_subs.ass"
    output_video = PROJECT_DIR / "final_short.mp4"
    print("1/2 Generating voiceover and dynamic captions...")
    await generate_tts_with_dynamic_ass(script_text, VOICE, voice_path, ass_path)
    print("2/2 Rendering vertical Short with FFmpeg...")
    render_video(raw_video, voice_path, ass_path, output_video, BG_MUSIC)
    print(f"Done! Saved to {output_video}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate vertical Shorts from a video queue.")
    parser.add_argument(
        "--queue",
        type=Path,
        default=QUEUE_FILE,
        help="Queue JSON file (default: videos.json in this directory).",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Upload rendered videos and schedule them on YouTube.",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Use legacy script.txt and raw_screen.mp4 inputs instead of the queue.",
    )
    args = parser.parse_args()
    if args.single:
        if args.publish:
            parser.error("--publish is only available in queue mode.")
        asyncio.run(run_single_video())
        return
    queue_file = project_path(args.queue)
    if not queue_file.is_file():
        legacy_inputs_exist = (
            (PROJECT_DIR / "script.txt").is_file()
            and (PROJECT_DIR / "raw_screen.mp4").is_file()
        )
        if queue_file.resolve() == QUEUE_FILE and legacy_inputs_exist and not args.publish:
            asyncio.run(run_single_video())
            return
        raise FileNotFoundError(
            f"Queue file not found: {queue_file}. Add topics to ideas.txt and run "
            "'python idea_generator.py' first, or use --single."
        )
    process_queue(queue_file, args.publish)


if __name__ == "__main__":
    main()
