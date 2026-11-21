import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests
from env_loader import load_project_env

load_project_env()

PIXABAY_API_URL = "https://pixabay.com/api/videos/"
PROJECT_DIR = Path(__file__).resolve().parent
FALLBACK_DIR = PROJECT_DIR / "assets" / "fallback_broll"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "'ffmpeg' was not found on PATH. Install FFmpeg and make sure its bin "
            "directory is on PATH."
        )


def download_clip(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    with requests.get(url, stream=True, timeout=(15, 45)) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"Pixabay clip exceeds the {MAX_DOWNLOAD_BYTES}-byte limit.")
                output.write(chunk)
    if downloaded == 0:
        raise ValueError("Pixabay returned an empty video file.")


def normalize_clip(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-t",
            "4.5",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def search_pixabay(query: str, api_key: str) -> list[dict[str, Any]]:
    response = requests.get(
        PIXABAY_API_URL,
        params={
            "key": api_key,
            "q": query,
            "video_type": "film",
            "per_page": 5,
            "safesearch": "true",
        },
        timeout=(15, 30),
    )
    if not response.ok:
        raise ValueError(f"Pixabay video search failed with HTTP {response.status_code}.")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Pixabay returned an unexpected video-search response.")
    hits = payload.get("hits", [])
    if not isinstance(hits, list):
        raise ValueError("Pixabay returned an unexpected video-search response.")
    return [hit for hit in hits if isinstance(hit, dict)]


def append_concat_file(concat_file: Path, clips: list[Path]) -> None:
    lines = []
    for clip in clips:
        path = clip.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{path}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def images_to_ken_burns_video(
    image_paths: list[Path],
    out_mp4: Path,
    temp_dir: Path,
    clip_seconds: float = 4.5,
) -> Path:
    require_ffmpeg()
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    fps = 30
    total_frames = int(clip_seconds * fps)
    clips: list[Path] = []

    for index, image in enumerate([path for path in image_paths[:5] if path.is_file()]):
        clip = temp_dir / f"ken_burns_{index}.mp4"
        vf = (
            "scale=2160:3840:force_original_aspect_ratio=increase,"
            "crop=2160:3840,"
            f"zoompan=z='min(zoom+0.0012,1.15)':d={total_frames}:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s=1080x1920:fps={fps}"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(image),
                "-vf", vf, "-t", str(clip_seconds),
                "-c:v", "libx264", "-preset", "fast", "-crf", "21",
                "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        clips.append(clip)

    if not clips:
        raise ValueError("No usable image files were found for the Ken Burns fallback.")

    concat_file = temp_dir / "ken_burns_concat.txt"
    append_concat_file(concat_file, clips)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(out_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return out_mp4


def fetch_and_stitch_broll(
    queries: list[str], out_mp4: Path, temp_dir: Path
) -> Path:
    require_ffmpeg()
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    normalized_clips: list[Path] = []
    api_key = os.environ.get("PIXABAY_API_KEY")

    if api_key:
        for index, query in enumerate(queries[:4]):
            try:
                print(f"   [Pixabay] Searching B-roll for: {query!r}...")
                hits = search_pixabay(query, api_key)
                if not hits:
                    print(f"   [Pixabay] No results for {query!r}.")
                    continue
                selected = random.choice(hits[:3])
                videos = selected.get("videos", {})
                if not isinstance(videos, dict):
                    print(f"   [Pixabay] No downloadable clip for {query!r}.")
                    continue
                video = videos.get("medium") or videos.get("large") or videos.get("small")
                video_url = video.get("url") if isinstance(video, dict) else None
                if not video_url:
                    print(f"   [Pixabay] No downloadable clip for {query!r}.")
                    continue

                raw_clip = temp_dir / f"pixabay_raw_{index}.mp4"
                normalized = temp_dir / f"pixabay_norm_{index}.mp4"
                download_clip(video_url, raw_clip)
                normalize_clip(raw_clip, normalized)
                normalized_clips.append(normalized)
            except (
                requests.RequestException,
                ValueError,
                KeyError,
                TypeError,
                subprocess.CalledProcessError,
            ) as exc:
                print(f"   [Pixabay Warning] Query {query!r} failed: {exc}")

    if not normalized_clips and FALLBACK_DIR.is_dir():
        print("   [Fallback] Using local clips from assets/fallback_broll/.")
        local_clips = sorted(FALLBACK_DIR.glob("*.mp4"))
        random.shuffle(local_clips)
        for index, source in enumerate(local_clips[:4]):
            normalized = temp_dir / f"fallback_norm_{index}.mp4"
            try:
                normalize_clip(source, normalized)
                normalized_clips.append(normalized)
            except subprocess.CalledProcessError as exc:
                print(f"   [Fallback Warning] Could not process {source}: {exc}")

    if not normalized_clips:
        print("   [Fallback] Creating an abstract vertical background clip.")
        generated = temp_dir / "generated_fallback.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x14213d:s=1080x1920:r=30:d=13.5",
                "-vf",
                "vignette=PI/5,noise=alls=8:allf=t",
                "-t",
                "13.5",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(generated),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        normalized_clips.append(generated)

    concat_file = temp_dir / "concat_list.txt"
    append_concat_file(concat_file, normalized_clips)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(out_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return out_mp4
