import os
import time
from typing import Any
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import APIError

from env_loader import load_project_env

PROJECT_DIR = Path(__file__).resolve().parent
load_project_env(PROJECT_DIR)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


def create_gemini_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY before using Gemini features.")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta", timeout=60_000),
    )


def generate_with_retry(
    prompt: str,
    schema: type[Any],
    retries: int = 4,
    client: genai.Client | None = None,
) -> Any:
    client = client or create_gemini_client()
    for attempt in range(retries):
        model_name = (
            FALLBACK_MODELS[min(attempt // 2, len(FALLBACK_MODELS) - 1)]
        )
        try:
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
        except APIError as exc:
            code = getattr(exc, "code", None)
            transient = code in (429, 500, 502, 503, 504)
            if not transient or attempt == retries - 1:
                raise
            wait_sec = 5 * (attempt + 1)
            print(
                f"   [Gemini {code or 'API'}] {model_name} busy, "
                f"retrying in {wait_sec}s..."
            )
            time.sleep(wait_sec)
    raise RuntimeError("Gemini generation exhausted all retry attempts.")
