import argparse
from pathlib import Path

from youtube_publisher import publish_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule a rendered MP4 on YouTube.")
    parser.add_argument("video", type=Path)
    parser.add_argument("title")
    parser.add_argument("--description", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--publish-at", required=True)
    args = parser.parse_args()
    video_id = publish_video(
        {
            "title": args.title,
            "description": args.description,
            "tags": args.tag,
            "publish_at_utc": args.publish_at,
        },
        args.video,
    )
    print(f"Scheduled video: https://youtu.be/{video_id}")


if __name__ == "__main__":
    main()
