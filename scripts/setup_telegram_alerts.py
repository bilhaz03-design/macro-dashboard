#!/usr/bin/env python3
"""Validate Telegram bot token and auto-discover chat id from getUpdates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_loader import load_default_env  # noqa: E402


ALERTS_ENV = Path.home() / ".config" / "swing-terminal" / "alerts.env"


def clipboard_text() -> str:
    try:
        return subprocess.check_output(["pbpaste"], text=True, timeout=5).strip()
    except Exception:
        return ""


def looks_like_bot_token(value: str | None) -> bool:
    if not value or "PASTE_" in value:
        return False
    left, sep, right = value.partition(":")
    return bool(sep and left.isdigit() and len(right) >= 30)


def telegram_call(token: str, method: str, payload: dict | None = None) -> dict:
    data = None
    if payload:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def write_alerts(values: dict[str, str]) -> None:
    ALERTS_ENV.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Local swing-terminal alert secrets.",
        "# Do not commit this file.",
        f"TELEGRAM_BOT_TOKEN={values.get('TELEGRAM_BOT_TOKEN', '')}",
        f"TELEGRAM_CHAT_ID={values.get('TELEGRAM_CHAT_ID', '')}",
        "",
    ]
    ALERTS_ENV.write_text("\n".join(lines), encoding="utf-8")
    ALERTS_ENV.chmod(0o600)


def main() -> int:
    load_default_env()
    values = read_env(ALERTS_ENV)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or values.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or values.get("TELEGRAM_CHAT_ID")

    if not looks_like_bot_token(token):
        clip = clipboard_text()
        if looks_like_bot_token(clip):
            token = clip

    if not looks_like_bot_token(token):
        print("TELEGRAM_BOT_TOKEN missing. Copy the BotFather token, then rerun this script.")
        return 2

    me = telegram_call(token, "getMe")
    if not me.get("ok"):
        print("Telegram getMe failed.")
        return 3
    bot_name = me.get("result", {}).get("username") or me.get("result", {}).get("first_name")

    if not chat_id or "PASTE_" in str(chat_id):
        updates = telegram_call(token, "getUpdates")
        for update in reversed(updates.get("result", [])):
            message = update.get("message") or update.get("channel_post") or {}
            chat = message.get("chat") or {}
            if chat.get("id") is not None:
                chat_id = str(chat["id"])
                break
        if not chat_id or "PASTE_" in str(chat_id):
            print(f"Bot @{bot_name} is valid, but chat id was not found. Send any message to the bot, then rerun this script.")
            return 4

    values["TELEGRAM_BOT_TOKEN"] = token
    values["TELEGRAM_CHAT_ID"] = str(chat_id)
    write_alerts(values)
    telegram_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "Swing-terminal Telegram alerts are connected.",
        "disable_web_page_preview": "true",
    })
    print(f"Telegram connected: bot=@{bot_name}, chat_id=present")
    print(f"Wrote {ALERTS_ENV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
