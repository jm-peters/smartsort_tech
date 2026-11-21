import argparse
import json
import os
import re
import tempfile
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from env_loader import load_project_env
from gemini_client import MODEL, create_gemini_client

PROJECT_DIR = Path(__file__).resolve().parent
load_project_env(PROJECT_DIR)


class ShortSpec(BaseModel):
    title: str
    description: str
    tags: list[str]
    urls: list[str] = Field(max_length=3)
    broll_queries: list[str] = Field(min_length=3, max_length=3)
    script: str


def read_ideas(ideas_file: Path) -> list[str]:
    if not ideas_file.is_file():
        raise FileNotFoundError(f"Ideas file not found: {ideas_file}")
    return [
        line.strip()
        for line in ideas_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def load_queue(queue_file: Path) -> list[dict[str, Any]]:
    if not queue_file.exists():
        return []
    with queue_file.open("r", encoding="utf-8") as file:
        queue = json.load(file)
    if not isinstance(queue, list) or any(not isinstance(item, dict) for item in queue):
        raise ValueError(f"{queue_file} must contain a JSON array of video objects.")
    return queue


def next_id(queue: list[dict[str, Any]]) -> int:
    numbers = []
    for item in queue:
        match = re.fullmatch(r"short_(\d+)", str(item.get("id", "")))
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def validate_spec(spec: ShortSpec, topic: str) -> None:
    if len(spec.title) >= 60 or not spec.title.endswith("#Shorts"):
        raise ValueError(f"Gemini returned an invalid title for {topic!r}: {spec.title!r}")
    word_count = len(spec.script.split())
    if not 75 <= word_count <= 90:
        raise ValueError(
            f"Gemini returned a {word_count}-word script for {topic!r}; expected 75-90."
        )
    if any(not query.strip() or len(query.split()) > 5 for query in spec.broll_queries):
        raise ValueError(
            f"Gemini returned an empty or overly long B-roll query for {topic!r}."
        )


def write_queue(queue_file: Path, queue: list[dict[str, Any]]) -> None:
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=queue_file.parent,
        prefix=f".{queue_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        temp_path = Path(file.name)
        json.dump(queue, file, indent=2, ensure_ascii=False)
        file.write("\n")
    os.replace(temp_path, queue_file)


def generate_queue_from_ideas(
    ideas: list[str], queue_file: str | Path = "videos.json"
) -> None:
    if not ideas:
        raise ValueError("No topics were found in ideas.txt.")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set the GEMINI_API_KEY environment variable before generating ideas.")

    queue_file = Path(queue_file)
    if not queue_file.is_absolute():
        queue_file = PROJECT_DIR / queue_file

    queue = load_queue(queue_file)
    start_index = next_id(queue)
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    latest_scheduled_date = None
    for existing_item in queue:
        scheduled = existing_item.get("publish_at_utc")
        if not scheduled:
            continue
        try:
            scheduled_time = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid publish_at_utc in existing queue entry "
                f"{existing_item.get('id', '<unknown>')!r}: {scheduled!r}"
            ) from exc
        if scheduled_time.tzinfo is None:
            raise ValueError(
                f"publish_at_utc must include a timezone in queue entry "
                f"{existing_item.get('id', '<unknown>')!r}."
            )
        scheduled_date = scheduled_time.astimezone(timezone.utc).date()
        if latest_scheduled_date is None or scheduled_date > latest_scheduled_date:
            latest_scheduled_date = scheduled_date

    start_date = max(
        tomorrow,
        latest_scheduled_date + timedelta(days=1) if latest_scheduled_date else tomorrow,
    )
    publish_start = datetime.combine(
        start_date, datetime_time(hour=15), tzinfo=timezone.utc
    )
    client = create_gemini_client()
    generated: list[dict[str, Any]] = []

    for index, topic in enumerate(ideas):
        prompt = f"""
Write a fast-paced 35-second YouTube Short script about this topic: {topic!r}.
Requirements:
- Hook viewers in the first 3 seconds; do not say "Hey guys".
- The script must contain 80 to 85 words.
- If the topic involves websites or tools, include 1 to 3 real public URLs in urls;
  otherwise return an empty urls list.
- Always include exactly 3 short visual search phrases (up to 5 words each) in
  broll_queries that match scenes in the script.
- The title must be under 60 characters and end with #Shorts.
- Keep claims accurate; do not invent URLs, product features, or statistics.
"""
        spec: ShortSpec | None = None
        last_validation_error: ValueError | None = None
        for attempt in range(3):
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ShortSpec,
                },
            )
            if response.parsed is not None:
                candidate = ShortSpec.model_validate(response.parsed)
            elif response.text:
                candidate = ShortSpec.model_validate_json(response.text)
            else:
                raise RuntimeError(
                    f"Gemini returned an empty response for topic {topic!r}."
                )
            try:
                validate_spec(candidate, topic)
            except ValueError as exc:
                last_validation_error = exc
                if attempt == 2:
                    raise
                prompt += (
                    "\nYour previous response failed validation: "
                    f"{exc}. Return a corrected response, especially an 80-85 word script."
                )
                continue
            spec = candidate
            break
        if spec is None:
            raise RuntimeError(
                f"Gemini could not produce a valid spec for {topic!r}: "
                f"{last_validation_error}"
            )
        publish_time = (publish_start + timedelta(days=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "id": f"short_{start_index + index:02d}",
            "status": "pending",
            "title": spec.title,
            "description": spec.description,
            "tags": spec.tags,
            "publish_at_utc": publish_time,
            "voice": "en-US-AndrewNeural",
            "urls": spec.urls,
            "broll_queries": spec.broll_queries,
            "script": spec.script,
        }
        generated.append(entry)
        print(f"Generated {entry['id']}: {entry['title']}")

    write_queue(queue_file, queue + generated)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Shorts metadata and scripts with Gemini.")
    parser.add_argument(
        "--ideas",
        type=Path,
        default=PROJECT_DIR / "ideas.txt",
        help="One topic per line (default: ideas.txt).",
    )
    parser.add_argument(
        "--queue",
        type=Path,
        default=PROJECT_DIR / "videos.json",
        help="Queue JSON to append to (default: videos.json).",
    )
    args = parser.parse_args()
    ideas_file = args.ideas if args.ideas.is_absolute() else PROJECT_DIR / args.ideas
    queue_file = args.queue if args.queue.is_absolute() else PROJECT_DIR / args.queue
    generate_queue_from_ideas(read_ideas(ideas_file), queue_file)


if __name__ == "__main__":
    main()
