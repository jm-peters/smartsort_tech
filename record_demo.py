import argparse
from pathlib import Path

from build_short import record_web_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Record vertical web demos with Playwright.")
    parser.add_argument("urls", nargs="+", help="URLs to record.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("recordings/demo.webm"),
        help="Destination WebM file.",
    )
    args = parser.parse_args()
    record_web_demo(args.urls, args.output)


if __name__ == "__main__":
    main()
