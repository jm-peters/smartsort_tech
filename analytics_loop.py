import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from env_loader import load_project_env
from gemini_client import MODEL, create_gemini_client
from idea_generator import PROJECT_DIR, generate_queue_from_ideas
from youtube_publisher import (
    load_credentials,
)

load_project_env(PROJECT_DIR)


class TopicList(BaseModel):
    winning_angles: list[str] = Field(min_length=5, max_length=5)


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
    winners: list[dict[str, Any]], count: int = 5
) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY before generating follow-up topics.")
    if count != 5:
        raise ValueError("The feedback loop currently generates exactly five topics.")

    client = create_gemini_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=(
            "Create exactly five distinct, fact-checkable YouTube Shorts topic ideas "
            "that extend the winning audience interests below. Do not repeat titles, "
            "make unsupported claims, or provide scripts; return concise topic ideas.\n"
            + json.dumps(winners[:3], ensure_ascii=False)
        ),
        config={
            "response_mime_type": "application/json",
            "response_schema": TopicList,
        },
    )
    if response.parsed is not None:
        topics = TopicList.model_validate(response.parsed).winning_angles
    elif response.text:
        topics = TopicList.model_validate_json(response.text).winning_angles
    else:
        raise RuntimeError("Gemini returned an empty topic response.")
    topics = [topic.strip() for topic in topics]
    if len(set(topic.casefold() for topic in topics)) != 5 or any(not topic for topic in topics):
        raise ValueError("Gemini must return five non-empty, distinct topic ideas.")
    return topics


def run_feedback_loop(days_back: int = 14) -> list[str]:
    print(f"Fetching YouTube Analytics for the last {days_back} days...")
    winners = fetch_top_performing_titles(days_back)
    if winners:
        topics = generate_follow_up_topics(winners[:3])
        print("Using top-performing topics to generate follow-ups.")
    else:
        topics = [
            "Practical ways to use free AI tools for everyday tasks",
            "Useful browser features that save time",
            "Simple phone settings that improve daily use",
            "Free productivity tools for organizing work",
            "Easy digital habits that reduce online clutter",
        ]
        print("No analytics rows yet; using starter topics.")
    generate_queue_from_ideas(topics, "videos.json")
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
