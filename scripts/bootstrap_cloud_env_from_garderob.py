#!/usr/bin/env python3
"""Create local swing-terminal cloud env files from Garderob's Supabase config.

This does not invent or expose secrets. It copies the non-secret Supabase project
URL from Garderob and leaves placeholders for the private ingest token,
optional service-role fallback, and Telegram secrets.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "swing-terminal"
CLOUD_ENV = CONFIG_DIR / "cloud.env"
ALERTS_ENV = CONFIG_DIR / "alerts.env"
GARDEROB_SYNC = Path("/Users/bobbo/Desktop/Garderob/www/js/supabase-sync.js")


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("\"'")
    return out


def write_env(path: Path, values: dict[str, str], comments: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = parse_env(path)
    merged = {**values, **existing}
    lines = comments + [""]
    for key, value in merged.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def extract_garderob_supabase_url(path: Path = GARDEROB_SYNC) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"SUPABASE_URL\s*=\s*['\"]([^'\"]+)['\"]", text)
    if match:
        return match.group(1)
    match = re.search(r"https://[a-z0-9.-]+\.supabase\.co", text)
    return match.group(0) if match else None


def extract_garderob_supabase_key(path: Path = GARDEROB_SYNC) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"SUPABASE_KEY\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def key_state(value: str | None) -> str:
    if not value:
        return "missing"
    if "REPLACE" in value or "PASTE" in value:
        return "placeholder"
    return "present"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap swing-terminal local cloud env files")
    parser.add_argument("--garderob-sync", default=str(GARDEROB_SYNC))
    args = parser.parse_args()

    supabase_url = extract_garderob_supabase_url(Path(args.garderob_sync))
    supabase_key = extract_garderob_supabase_key(Path(args.garderob_sync))
    if not supabase_url:
        raise SystemExit("Could not find SUPABASE_URL in Garderob supabase-sync.js")

    write_env(
        CLOUD_ENV,
        {
            "SUPABASE_URL": supabase_url,
            "SUPABASE_PUBLISHABLE_KEY": supabase_key or "PASTE_SUPABASE_PUBLISHABLE_KEY_HERE",
            "SUPABASE_INGEST_TOKEN": "PASTE_SUPABASE_INGEST_TOKEN_HERE",
            "SUPABASE_SERVICE_ROLE_KEY": "PASTE_SUPABASE_SERVICE_ROLE_KEY_HERE",
            "SWING_TERMINAL_CLOUD_RUN": "0",
            "SWING_TERMINAL_DASHBOARD_URL": "",
        },
        [
            "# Local swing-terminal cloud secrets.",
            "# File permissions are 0600. Do not commit this file.",
            "# SUPABASE_URL was imported from Garderob.",
            "# Prefer SUPABASE_PUBLISHABLE_KEY + SUPABASE_INGEST_TOKEN; service-role is optional fallback.",
        ],
    )
    write_env(
        ALERTS_ENV,
        {
            "TELEGRAM_BOT_TOKEN": "PASTE_TELEGRAM_BOT_TOKEN_HERE",
            "TELEGRAM_CHAT_ID": "PASTE_TELEGRAM_CHAT_ID_HERE",
        },
        [
            "# Local swing-terminal alert secrets.",
            "# Create the bot via BotFather, message it once, then run scripts/setup_telegram_alerts.py.",
        ],
    )

    cloud = parse_env(CLOUD_ENV)
    alerts = parse_env(ALERTS_ENV)
    print(f"Wrote {CLOUD_ENV}")
    print(f"Wrote {ALERTS_ENV}")
    print(f"SUPABASE_URL={key_state(cloud.get('SUPABASE_URL'))}")
    print(f"SUPABASE_PUBLISHABLE_KEY={key_state(cloud.get('SUPABASE_PUBLISHABLE_KEY'))}")
    print(f"SUPABASE_INGEST_TOKEN={key_state(cloud.get('SUPABASE_INGEST_TOKEN'))}")
    print(f"SUPABASE_SERVICE_ROLE_KEY={key_state(cloud.get('SUPABASE_SERVICE_ROLE_KEY'))}")
    print(f"TELEGRAM_BOT_TOKEN={key_state(alerts.get('TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN'))}")
    print(f"TELEGRAM_CHAT_ID={key_state(alerts.get('TELEGRAM_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
