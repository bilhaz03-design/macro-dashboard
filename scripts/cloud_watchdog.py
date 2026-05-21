#!/usr/bin/env python3
"""Watch the cloud scanner heartbeat and alert if it goes quiet."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))

import run_scan_notify  # noqa: E402
import supabase_io  # noqa: E402
from env_loader import load_default_env  # noqa: E402

STATE_KEY = "cloud-watchdog-state"


def now_stockholm() -> datetime:
    return datetime.now(ZoneInfo("Europe/Stockholm"))


def in_watch_window(dt: datetime) -> bool:
    local = dt.astimezone(ZoneInfo("Europe/Stockholm"))
    if local.weekday() >= 5:
        return False
    return time(9, 15) <= local.time() <= time(22, 45)


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_runs(config: supabase_io.SupabaseConfig) -> list[dict]:
    rows = supabase_io.request_json(
        config,
        "GET",
        "scan_runs",
        query={
            "select": "run_id,created_at,mode,status,exit_code,total_scanned,error_count",
            "order": "created_at.desc",
            "limit": "8",
        },
    )
    return rows if isinstance(rows, list) else []


def load_state(config: supabase_io.SupabaseConfig) -> dict:
    payload = supabase_io.download_json_artifact(config, STATE_KEY)
    return payload if isinstance(payload, dict) else {}


def save_state(config: supabase_io.SupabaseConfig, payload: dict) -> None:
    supabase_io.upload_json_artifact(
        config,
        artifact_key=STATE_KEY,
        kind="watchdog_state",
        payload=payload,
        source_path="scripts/cloud_watchdog.py",
        scan_date=datetime.now(timezone.utc).date().isoformat(),
    )


def should_alert(state: dict, now_utc: datetime, cooldown_minutes: int) -> bool:
    last_alert_at = parse_ts(state.get("last_alert_at"))
    if not last_alert_at:
        return True
    return now_utc - last_alert_at >= timedelta(minutes=cooldown_minutes)


def run_watchdog(args: argparse.Namespace) -> int:
    load_default_env()
    local_now = now_stockholm()
    if not args.force and not in_watch_window(local_now):
        print(f"[cloud_watchdog] outside watch window Europe/Stockholm: {local_now.isoformat(timespec='seconds')}")
        return 0

    config = supabase_io.config_from_env(required=True)
    assert config is not None
    now_utc = datetime.now(timezone.utc)
    rows = latest_runs(config)
    latest_ok = next((row for row in rows if row.get("status") == "OK"), None)
    latest_any = rows[0] if rows else None
    latest_ok_at = parse_ts(latest_ok.get("created_at")) if latest_ok else None
    age_minutes = None if latest_ok_at is None else int((now_utc - latest_ok_at).total_seconds() // 60)
    healthy = latest_ok_at is not None and age_minutes is not None and age_minutes <= args.max_age_minutes
    state = load_state(config)

    if healthy:
        print(f"[cloud_watchdog] healthy: latest OK run age={age_minutes}m")
        if state.get("status") == "alerting" and not args.no_notify:
            run_scan_notify.push_telegram(
                "Scanner recovered",
                "Cloud watchdog",
                f"Latest OK run is {age_minutes} minutes old ({latest_ok.get('mode')} / {latest_ok.get('run_id')}).",
            )
        save_state(config, {
            "status": "ok",
            "last_ok_at": latest_ok_at.isoformat(),
            "last_checked_at": now_utc.isoformat(),
            "last_alert_at": state.get("last_alert_at"),
        })
        return 0

    print(f"[cloud_watchdog] stale scanner: latest OK age={age_minutes}m latest_any={latest_any}")
    if not args.no_notify and should_alert(state, now_utc, args.cooldown_minutes):
        latest_text = "no scan_runs rows"
        if latest_any:
            latest_text = (
                f"latest row: {latest_any.get('mode')} {latest_any.get('status')} "
                f"{latest_any.get('created_at')} ({latest_any.get('run_id')})"
            )
        run_scan_notify.push_telegram(
            "Scanner watchdog alert",
            "Cloud runner may be silent",
            f"No fresh OK cloud run within {args.max_age_minutes} minutes; {latest_text}.",
        )
        state["last_alert_at"] = now_utc.isoformat()

    save_state(config, {
        **state,
        "status": "alerting",
        "last_checked_at": now_utc.isoformat(),
        "last_ok_at": latest_ok_at.isoformat() if latest_ok_at else None,
        "latest_any": latest_any,
        "max_age_minutes": args.max_age_minutes,
    })
    return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alert when the cloud scanner heartbeat is stale")
    parser.add_argument("--max-age-minutes", type=int, default=50)
    parser.add_argument("--cooldown-minutes", type=int, default=60)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    return parser.parse_args()


def main() -> int:
    return run_watchdog(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
