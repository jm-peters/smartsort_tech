import json
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[2]


def build_facebook_captions(items: list[dict[str, Any]]) -> str:
    completed = [item for item in items if item.get("status") == "done"][-5:]
    sections = []
    for item in completed:
        title = str(item.get("title", "")).replace("#Shorts", "").strip()
        caption = item.get("fb_caption") or title
        first_comment = item.get("fb_first_comment") or ", ".join(
            str(url) for url in item.get("urls", [])
        )
        sections.append(
            f"=== {item.get('id', 'video')}.mp4 ===\n"
            f"CAPTION:\n{caption}\n\n"
            f"FIRST COMMENT:\n{first_comment}"
        )
    return "\n\n" + ("-" * 40 + "\n\n").join(sections) + ("\n" if sections else "")


def export_facebook_pack(
    queue_file: Path = PROJECT_DIR / "videos.json",
    output_dir: Path = PROJECT_DIR / "output",
) -> Path:
    with queue_file.open("r", encoding="utf-8") as file:
        items = json.load(file)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"{queue_file} must contain a JSON array of video objects.")

    output_dir.mkdir(parents=True, exist_ok=True)
    captions_file = output_dir / "facebook_captions.txt"
    captions_file.write_text(build_facebook_captions(items), encoding="utf-8")
    return captions_file


if __name__ == "__main__":
    print(f"Exported Facebook captions to {export_facebook_pack()}")
