import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_loader import load_project_env

load_project_env()

YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YT_ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    YOUTUBE_READONLY_SCOPE,
    YT_ANALYTICS_READONLY_SCOPE,
]
PROJECT_DIR = Path(__file__).resolve().parent


def load_credentials(scopes: list[str] | None = None) -> Any:
    requested_scopes = scopes or SCOPES
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "YouTube publishing dependencies are missing. Install requirements.txt."
        ) from exc

    client_file = Path(
        os.environ.get("YOUTUBE_CLIENT_SECRETS", str(PROJECT_DIR / "client_secrets.json"))
    )
    token_file = Path(os.environ.get("YOUTUBE_TOKEN_FILE", str(PROJECT_DIR / "token.json")))
    if not client_file.is_absolute():
        client_file = PROJECT_DIR / client_file
    if not token_file.is_absolute():
        token_file = PROJECT_DIR / token_file

    token_data: dict[str, Any] = {}
    credentials = None
    if token_file.exists():
        try:
            token_data = json.loads(token_file.read_text(encoding="utf-8"))
            if not isinstance(token_data, dict):
                token_data = {}
            credentials = Credentials.from_authorized_user_file(str(token_file))
        except Exception as exc:
            print(f"   [YouTube Warning] Invalid saved OAuth token: {exc}")
    granted_scopes = set(token_data.get("scopes", [])) if credentials else set()
    has_requested_scopes = credentials is not None and set(requested_scopes).issubset(
        granted_scopes
    )
    if has_requested_scopes and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not has_requested_scopes or not credentials or not credentials.valid:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise RuntimeError(
                "The saved YouTube token does not include the requested OAuth scopes. "
                "Reauthorize locally with the updated application and replace the "
                "YOUTUBE_TOKEN_JSON GitHub secret."
            )
        if client_file.is_file():
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_file), requested_scopes
            )
        else:
            client_id = os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("Client_id")
            client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or os.environ.get(
                "Client_secret"
            )
            if not client_id or not client_secret:
                raise FileNotFoundError(
                    f"YouTube OAuth client file not found: {client_file}. Provide a "
                    "Desktop OAuth client JSON or set YOUTUBE_CLIENT_ID and "
                    "YOUTUBE_CLIENT_SECRET."
                )
            flow = InstalledAppFlow.from_client_config(
                {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                },
                requested_scopes,
            )
        credentials = flow.run_local_server(port=0, prompt="consent")

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def parse_publish_time(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Queue entry needs a UTC 'publish_at_utc' timestamp.")
    try:
        publish_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid publish_at_utc value: {value!r}") from exc
    if publish_at.tzinfo is None:
        raise ValueError("'publish_at_utc' must include a timezone.")
    publish_at = publish_at.astimezone(timezone.utc)
    if publish_at <= datetime.now(timezone.utc):
        raise ValueError(f"Scheduled publish time must be in the future: {value}")
    return publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_video(item: dict[str, Any], video_path: Path) -> str:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "YouTube publishing dependencies are missing. Install requirements.txt."
        ) from exc

    title = item.get("title")
    description = item.get("description", "")
    tags = item.get("tags", [])
    if not isinstance(title, str) or not title.strip():
        raise ValueError("YouTube upload needs a non-empty title.")
    if not isinstance(description, str) or not isinstance(tags, list):
        raise ValueError("Queue description must be text and tags must be a list.")
    publish_at = parse_publish_time(item.get("publish_at_utc"))
    credentials = load_credentials()
    youtube = build("youtube", "v3", credentials=credentials)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "28",
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at,
            },
        },
        media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True),
    )

    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress is not None:
            print(f"   [YouTube] Uploaded {int(progress.progress() * 100)}%.")
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube upload completed without returning a video id.")
    return str(video_id)
