#!/usr/bin/env python3
"""Watch the cloud scanner heartbeat and alert if it goes quiet."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
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
ALLOWED_HEAL_MODES = {"etf", "stocks", "all"}


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


def mode_matches(row: dict, mode: str) -> bool:
    row_mode = str(row.get("mode") or "")
    return row_mode == mode or row_mode == "all"


def latest_ok_run(rows: list[dict], mode: str | None = None) -> dict | None:
    return next(
        (
            row for row in rows
            if row.get("status") == "OK" and (mode is None or mode_matches(row, mode))
        ),
        None,
    )


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


def should_dispatch_heal(state: dict, mode: str, now_utc: datetime, cooldown_minutes: int) -> bool:
    heal_state = state.get("last_heal_dispatch_at") if isinstance(state, dict) else {}
    last_dispatch_at = parse_ts(heal_state.get(mode)) if isinstance(heal_state, dict) else None
    if not last_dispatch_at:
        return True
    return now_utc - last_dispatch_at >= timedelta(minutes=cooldown_minutes)


def remember_heal_dispatch(state: dict, mode: str, now_utc: datetime) -> None:
    heal_state = state.get("last_heal_dispatch_at")
    if not isinstance(heal_state, dict):
        heal_state = {}
        state["last_heal_dispatch_at"] = heal_state
    heal_state[mode] = now_utc.isoformat()


def dispatch_github_scan(*, mode: str, repo: str, ref: str, token: str, no_notify: bool = False) -> dict:
    if mode not in ALLOWED_HEAL_MODES:
        raise ValueError(f"Unsupported heal mode: {mode}")
    if not token:
        raise RuntimeError("Missing GitHub token for self-heal dispatch")

    body = json.dumps({
        "ref": ref,
        "inputs": {
            "mode": mode,
            "force": "false",
            "no_notify": "true" if no_notify else "false",
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/swing-terminal-cloud.yml/dispatches",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "swing-terminal-cloud-watchdog",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            detail = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub dispatch failed: {exc.code} {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub dispatch failed: {exc.reason}") from exc

    if status != 204:
        raise RuntimeError(f"GitHub dispatch failed: {status} {detail[:300]}")
    return {"mode": mode, "workflow": "swing-terminal-cloud.yml", "http_status": status}


def parse_expected_mode_specs(values: list[str]) -> list[tuple[str, int]]:
    parsed = []
    for value in values:
        try:
            mode, max_age = value.split(":", 1)
            max_age_minutes = int(max_age)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Expected MODE:MAX_AGE_MINUTES, got {value!r}"
            ) from exc
        if mode not in {"etf", "stocks", "all"} or max_age_minutes <= 0:
            raise argparse.ArgumentTypeError(
                f"Expected mode etf/stocks/all and positive age, got {value!r}"
            )
        parsed.append((mode, max_age_minutes))
    return parsed


def evaluate_health(rows: list[dict], now_utc: datetime, expected_modes: list[tuple[str, int]]) -> list[dict]:
    checks = []
    for mode, max_age_minutes in expected_modes:
        latest_ok = latest_ok_run(rows, None if mode == "all" else mode)
        latest_ok_at = parse_ts(latest_ok.get("created_at")) if latest_ok else None
        age_minutes = None if latest_ok_at is None else int((now_utc - latest_ok_at).total_seconds() // 60)
        healthy = latest_ok_at is not None and age_minutes is not None and age_minutes <= max_age_minutes
        checks.append({
            "mode": mode,
            "max_age_minutes": max_age_minutes,
            "latest_ok": latest_ok,
            "latest_ok_at": latest_ok_at,
            "age_minutes": age_minutes,
            "healthy": healthy,
        })
    return checks


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
    expected_modes = parse_expected_mode_specs(args.expect_mode)
    if not expected_modes:
        expected_modes = [("all", args.max_age_minutes)]
    checks = evaluate_health(rows, now_utc, expected_modes)
    stale_checks = [check for check in checks if not check["healthy"]]
    latest_ok = latest_ok_run(rows)
    latest_any = rows[0] if rows else None
    latest_ok_at = parse_ts(latest_ok.get("created_at")) if latest_ok else None
    age_minutes = None if latest_ok_at is None else int((now_utc - latest_ok_at).total_seconds() // 60)
    state = load_state(config)

    if not stale_checks:
        summary = ", ".join(
            f"{check['mode']}={check['age_minutes']}m/{check['max_age_minutes']}m"
            for check in checks
        )
        print(f"[cloud_watchdog] healthy: {summary}")
        if state.get("status") == "alerting" and not args.no_notify:
            run_scan_notify.push_telegram(
                "Scanner recovered",
                "Cloud watchdog",
                f"Latest OK runs are fresh: {summary}.",
            )
        save_state(config, {
            "status": "ok",
            "last_ok_at": latest_ok_at.isoformat() if latest_ok_at else None,
            "last_checked_at": now_utc.isoformat(),
            "last_alert_at": state.get("last_alert_at"),
            "last_heal_dispatch_at": state.get("last_heal_dispatch_at"),
            "checks": [
                {
                    "mode": check["mode"],
                    "age_minutes": check["age_minutes"],
                    "max_age_minutes": check["max_age_minutes"],
                    "latest_ok": check["latest_ok"],
                }
                for check in checks
            ],
        })
        return 0

    stale_text = "; ".join(
        f"{check['mode']} age={check['age_minutes']}m max={check['max_age_minutes']}m"
        for check in stale_checks
    )
    print(f"[cloud_watchdog] stale scanner: {stale_text} latest_any={latest_any}")
    heal_results: list[dict] = []
    if args.dispatch_on_stale:
        for check in stale_checks:
            mode = check["mode"] if check["mode"] != "all" else args.heal_mode
            if not should_dispatch_heal(state, mode, now_utc, args.heal_cooldown_minutes):
                heal_results.append({"mode": mode, "status": "throttled"})
                continue
            try:
                result = dispatch_github_scan(
                    mode=mode,
                    repo=args.github_repo,
                    ref=args.github_ref,
                    token=os.environ.get(args.github_token_env, ""),
                    no_notify=args.no_notify,
                )
                remember_heal_dispatch(state, mode, now_utc)
                heal_results.append({**result, "status": "dispatched"})
            except (RuntimeError, ValueError) as exc:
                heal_results.append({"mode": mode, "status": "failed", "error": str(exc)})

    if heal_results:
        print(f"[cloud_watchdog] self-heal: {heal_results}")

    if not args.no_notify and should_alert(state, now_utc, args.cooldown_minutes):
        latest_text = "no scan_runs rows"
        if latest_any:
            latest_text = (
                f"latest row: {latest_any.get('mode')} {latest_any.get('status')} "
                f"{latest_any.get('created_at')} ({latest_any.get('run_id')})"
            )
        heal_text = ""
        if heal_results:
            heal_text = f" Self-heal: {heal_results}."
        run_scan_notify.push_telegram(
            "Scanner watchdog alert",
            "Cloud runner may be silent",
            f"Stale checks: {stale_text}; {latest_text}.{heal_text}",
        )
        state["last_alert_at"] = now_utc.isoformat()

    save_state(config, {
        **state,
        "status": "alerting",
        "last_checked_at": now_utc.isoformat(),
        "last_ok_at": latest_ok_at.isoformat() if latest_ok_at else None,
        "latest_any": latest_any,
        "max_age_minutes": args.max_age_minutes,
        "checks": [
            {
                "mode": check["mode"],
                "age_minutes": check["age_minutes"],
                "max_age_minutes": check["max_age_minutes"],
                "latest_ok": check["latest_ok"],
            }
            for check in checks
        ],
        "heal_results": heal_results,
    })
    if args.dispatch_on_stale and heal_results and all(result["status"] == "dispatched" for result in heal_results):
        return 0
    return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alert when the cloud scanner heartbeat is stale")
    parser.add_argument("--max-age-minutes", type=int, default=50)
    parser.add_argument("--cooldown-minutes", type=int, default=60)
    parser.add_argument(
        "--expect-mode",
        action="append",
        default=[],
        help="Mode-specific heartbeat expectation as MODE:MAX_AGE_MINUTES; can be repeated.",
    )
    parser.add_argument("--dispatch-on-stale", action="store_true")
    parser.add_argument("--heal-mode", choices=["etf", "stocks", "all"], default="etf")
    parser.add_argument("--heal-cooldown-minutes", type=int, default=45)
    parser.add_argument("--github-repo", default=os.environ.get("GITHUB_REPOSITORY", "bilhaz03-design/macro-dashboard"))
    parser.add_argument("--github-ref", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    return parser.parse_args()


def main() -> int:
    return run_watchdog(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
