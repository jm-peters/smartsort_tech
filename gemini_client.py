import os
from pathlib import Path

from google import genai
from google.genai import types

from env_loader import load_project_env

PROJECT_DIR = Path(__file__).resolve().parent
load_project_env(PROJECT_DIR)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")


def create_gemini_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY before using Gemini features.")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta", timeout=60_000),
    )
