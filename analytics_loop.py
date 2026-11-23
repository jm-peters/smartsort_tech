import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from env_loader import load_project_env
from gemini_client import generate_with_retry
from idea_generator import PROJECT_DIR, generate_queue_from_ideas
from youtube_publisher import (
    load_credentials,
)

load_project_env(PROJECT_DIR)


class TopicList(BaseModel):
    winning_angles: list[str] = Field(min_length=5, max_length=5)


ANALYTICS_SYSTEM_INSTRUCTION = """
You are a YouTube Shorts growth strategist and viral packaging expert.
Your job is to analyze retention and view data from a tech, AI, and productivity channel and engineer 5 follow-up video concepts with high swipe-through retention and subscriber conversion.
"""

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")


STARTER_TOPICS = [
    "Here are 5 brutal YouTube monetization and growth tips nobody tells small channels",
    "Stop paying for Photoshop and Office when these 3 free websites do the exact same thing",
    "How to check if any suspicious link or website is a scam in 5 seconds before clicking",
    "How a single missing hyphen in code destroyed a 125 million dollar NASA rocket",
    "5 hidden Windows and Chrome keyboard shortcuts that make you look like a pro",
]


def build_analytics_topic_prompt(
    winners: list[dict[str, Any]], existing_titles: list[str]
) -> str:
    return f"""
Analyze these top-performing YouTube Shorts from my channel:
{json.dumps(winners, indent=2)}

ALREADY PUBLISHED OR QUEUED TITLES (DO NOT REPEAT):
{json.dumps(existing_titles[-30:], indent=2)}

TASK:
Generate 5 high-virality YouTube Short concepts for next week by selecting and rotating across these 6 proven formats (choose 5 distinct formats per week):

1. RAPID-FIRE 5 TIPS ("Here are 5 tips for..."): Actionable tips on YouTube growth, monetization, productivity, focus, or digital side-hustles.
2. PAID VS. FREE SHOWDOWN: Compare an expensive subscription app against a 100% free browser or open-source alternative.
3. CYBER SAFETY & SCAM ALERT: A privacy setting, fake-link check, or account-protection hack everyone needs.
4. AI PROMPT UPGRADE: A "Weak Prompt vs. Pro Prompt" before-and-after demonstration on a free AI tool.
5. TECH MICRO-STORY / MYSTERY: A gripping true tech story, engineering blunder, or hidden history explainer.
6. HIDDEN DEVICE SHORTCUT: A built-in Windows, Mac, Chrome, Android, or iPhone feature requiring zero downloads.

RULES:
- Skew slightly toward themes similar to my top winners above while maintaining format variety.
- Frame every topic with a strong curiosity gap rather than a dry category label.
- Ensure URLs suggested are public and login-free; for story or tips topics, leave urls empty so stock B-roll is used.
"""


def normalize_topic(topic: str) -> str:
    return " ".join(topic.casefold().split())


def filter_duplicate_topics(
    topics: list[str], existing_titles: list[str]
) -> list[str]:
    existing = {normalize_topic(title) for title in existing_titles[-25:] if title.strip()}
    unique: list[str] = []
    for topic in topics:
        normalized = normalize_topic(topic)
        if not normalized or normalized in existing:
            continue
        if normalized in {normalize_topic(item) for item in unique}:
            continue
        unique.append(topic.strip())
    return unique


def fetch_top_performing_titles(days_back: int = 14) -> list[dict[str, Any]]:
    if days_back < 1:
        raise ValueError("days_back must be at least one.")
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "YouTube Analytics dependencies are missing. Install requirements.txt."
        ) from exc

    credentials = load_credentials()
    youtube = build("youtube", "v3", credentials=credentials)
    analytics = build("youtubeAnalytics", "v2", credentials=credentials)
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)
    report = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,averageViewPercentage",
        dimensions="video",
        sort="-views",
        maxResults=50,
    ).execute()

    rows = report.get("rows", [])
    if not rows:
        return []

    ranked: list[dict[str, Any]] = []
    ids = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        video_id, views, average_view_percentage = row[:3]
        try:
            views_value = float(views)
            retention_value = float(average_view_percentage)
        except (TypeError, ValueError):
            continue
        ids.append(str(video_id))
        ranked.append(
            {
                "video_id": str(video_id),
                "views": views_value,
                "retention_pct": retention_value,
                "score": views_value * retention_value,
            }
        )
    if not ranked:
        return []

    videos_response = youtube.videos().list(
        part="snippet", id=",".join(ids[:50])
    ).execute()
    titles = {
        item["id"]: item.get("snippet", {}).get("title", item["id"])
        for item in videos_response.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for item in ranked:
        item["title"] = titles.get(item["video_id"], item["video_id"])
        item["views"] = int(item["views"])
        item["retention_pct"] = round(item["retention_pct"], 1)
        item["score"] = round(item["score"], 1)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def generate_follow_up_topics(
    winners: list[dict[str, Any]],
    count: int = 5,
    existing_titles: list[str] | None = None,
) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY before generating follow-up topics.")
    if count != 5:
        raise ValueError("The feedback loop currently generates exactly five topics.")

    try:
        prompt = build_analytics_topic_prompt(
            winners[:3],
            existing_titles or [],
        )
        response = generate_with_retry(
            prompt,
            TopicList,
            system_instruction=ANALYTICS_SYSTEM_INSTRUCTION,
            temperature=0.9,
        )
        if response.parsed is not None:
            topics = TopicList.model_validate(response.parsed).winning_angles
        elif response.text:
            topics = TopicList.model_validate_json(response.text).winning_angles
        else:
            raise RuntimeError("Gemini returned an empty topic response.")
        topics = [topic.strip() for topic in topics]
        topics = filter_duplicate_topics(topics, existing_titles or [])
        if len(topics) != 5:
            raise ValueError("Gemini must return five non-empty, distinct topic ideas.")
        return topics
    except Exception as exc:
        print(f"   [Gemini Fallback] Could not generate follow-up topics: {exc}")
        return STARTER_TOPICS.copy()


def run_feedback_loop(days_back: int = 14) -> list[str]:
    print(f"Fetching YouTube Analytics for the last {days_back} days...")
    try:
        winners = fetch_top_performing_titles(days_back)
    except Exception as exc:
        winners = []
        print(f"   [Analytics Fallback] Could not read YouTube Analytics: {exc}")
    if winners:
        existing_queue: list[Any] = []
        try:
            queue_path = PROJECT_DIR / "videos.json"
            if queue_path.exists():
                with queue_path.open("r", encoding="utf-8") as queue_file:
                    existing_queue = json.load(queue_file)
                    if not isinstance(existing_queue, list):
                        existing_queue = []
        except (OSError, json.JSONDecodeError):
            existing_queue = []
        existing_titles = [
            str(item.get("title", ""))
            for item in existing_queue
            if isinstance(item, dict)
        ]
        topics = generate_follow_up_topics(winners[:3], existing_titles=existing_titles)
        print("Using top-performing topics to generate follow-ups.")
    else:
        topics = STARTER_TOPICS.copy()
        print("No analytics data available; using starter topics.")
    try:
        generate_queue_from_ideas(topics, "videos.json")
    except Exception as exc:
        print(f"   [Queue Fallback] Could not generate the queue batch: {exc}")
    return topics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate new Shorts topics based on YouTube performance."
    )
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    run_feedback_loop(args.days)


if __name__ == "__main__":
    main()
