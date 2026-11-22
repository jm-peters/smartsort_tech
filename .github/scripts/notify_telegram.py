import json
import os
import urllib.error
import urllib.request


def send_telegram_alert() -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram alert skipped: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not configured.")
        return False

    failed = os.environ.get("PIPELINE_FAILED", "").strip().lower() == "true"
    run_url = os.environ.get("GITHUB_RUN_URL", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "smartsort_tech")
    status = "FAILED" if failed else "SUCCESS"
    text = f"SmartSort pipeline {status}: {repository}"
    if run_url:
        text += f"\n{run_url}"

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if 200 <= response.status < 300:
                print("Telegram pipeline alert sent.")
                return True
            print(f"Telegram alert returned HTTP {response.status}; pipeline continues.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Telegram alert failed ({exc}); pipeline continues.")
    return False


if __name__ == "__main__":
    send_telegram_alert()
