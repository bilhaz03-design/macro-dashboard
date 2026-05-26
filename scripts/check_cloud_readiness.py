#!/usr/bin/env python3
"""Non-secret readiness check for swing-terminal cloud + Telegram setup."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import supabase_io  # noqa: E402
from env_loader import load_default_env  # noqa: E402


def usable(value: str | None) -> bool:
    return bool(value and not value.startswith("PASTE_") and not value.endswith("_HERE"))


def status(ok: bool) -> str:
    return "OK" if ok else "MISSING"


def telegram_get_me(token: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            return False, "getMe failed"
        username = data.get("result", {}).get("username") or data.get("result", {}).get("first_name") or "bot"
        return True, f"@{username}"
    except Exception as exc:
        return False, type(exc).__name__


def telegram_chat_check(token: str, chat_id: str) -> tuple[bool, str]:
    try:
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": "Swing-terminal readiness check.",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return bool(data.get("ok")), "sendMessage"
    except Exception as exc:
        return False, type(exc).__name__


def supabase_check() -> tuple[bool, str]:
    try:
        config = supabase_io.config_from_env(required=True)
        supabase_io.select_rows(config, "scanner_artifacts", select="artifact_key", limit=1)
        return True, "REST reachable"
    except Exception as exc:
        return False, str(exc).splitlines()[0][:160]


def main() -> int:
    load_default_env()
    cloud_env = Path.home() / ".config" / "swing-terminal" / "cloud.env"
    alerts_env = Path.home() / ".config" / "swing-terminal" / "alerts.env"
    migrations_dir = ROOT / "supabase" / "migrations"
    cloud_migrations = list(migrations_dir.glob("*swing_terminal_cloud_state.sql"))
    ingest_migrations = list(migrations_dir.glob("*swing_terminal_ingest_rls.sql"))

    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    ingest_token = os.environ.get("SUPABASE_INGEST_TOKEN")
    ingest_pair_ok = usable(publishable_key) and usable(ingest_token)
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    checks: list[tuple[str, bool, str]] = []
    running_in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    if not running_in_actions:
        checks.extend([
            ("cloud.env", cloud_env.exists(), str(cloud_env)),
            ("alerts.env", alerts_env.exists(), str(alerts_env)),
        ])
    checks.extend([
        (
            "cloud state migration",
            bool(cloud_migrations),
            str(cloud_migrations[0]) if cloud_migrations else "missing *swing_terminal_cloud_state.sql",
        ),
        (
            "ingest RLS migration",
            bool(ingest_migrations),
            str(ingest_migrations[0]) if ingest_migrations else "missing *swing_terminal_ingest_rls.sql",
        ),
        ("SUPABASE_URL", usable(supabase_url), "present" if usable(supabase_url) else "missing"),
        ("SUPABASE_PUBLISHABLE_KEY", usable(publishable_key), "present" if usable(publishable_key) else "missing"),
        ("SUPABASE_INGEST_TOKEN", usable(ingest_token), "present" if usable(ingest_token) else "missing"),
        (
            "SUPABASE_SERVICE_ROLE_KEY",
            usable(service_key) or ingest_pair_ok,
            "present (optional)" if usable(service_key) else "missing (ok: ingest token works)" if ingest_pair_ok else "missing",
        ),
        ("TELEGRAM_BOT_TOKEN", usable(telegram_token), "present" if usable(telegram_token) else "missing"),
        ("TELEGRAM_CHAT_ID", usable(telegram_chat_id), "present" if usable(telegram_chat_id) else "missing"),
    ])

    if usable(service_key) or (usable(publishable_key) and usable(ingest_token)):
        ok, detail = supabase_check()
        checks.append(("Supabase REST", ok, detail))
    if usable(telegram_token):
        ok, detail = telegram_get_me(telegram_token)
        checks.append(("Telegram bot", ok, detail))
        if ok and usable(telegram_chat_id):
            chat_ok, chat_detail = telegram_chat_check(telegram_token, telegram_chat_id)
            checks.append(("Telegram send", chat_ok, chat_detail))

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{name:<{width}}  {status(ok):<8}  {detail}")

    all_ok = all(ok for _, ok, _ in checks)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
