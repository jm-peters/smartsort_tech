import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from env_loader import load_project_env
from youtube_publisher import SCOPES


PROJECT_DIR = Path(__file__).resolve().parent
TOKEN_FILE = PROJECT_DIR / "token.json"


def main() -> None:
    load_project_env()
    client_secrets_file = Path(
        os.environ.get(
            "YOUTUBE_CLIENT_SECRETS", str(PROJECT_DIR / "client_secrets.json")
        )
    )
    if not client_secrets_file.is_absolute():
        client_secrets_file = PROJECT_DIR / client_secrets_file
    if not client_secrets_file.is_file():
        raise FileNotFoundError(
            f"OAuth client file not found: {client_secrets_file}"
        )
    try:
        client_config = json.loads(client_secrets_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"OAuth client file is not valid JSON: {client_secrets_file}"
        ) from exc
    client = client_config.get("installed") or client_config.get("web")
    if not isinstance(client, dict) or not client.get("client_id") or not client.get(
        "client_secret"
    ):
        raise ValueError(
            f"OAuth client file must contain an installed or web client: "
            f"{client_secrets_file}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_file), SCOPES
    )
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke this app's access in "
            "your Google Account and run auth_setup.py again."
        )

    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Success! OAuth token saved to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
