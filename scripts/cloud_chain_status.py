#!/usr/bin/env python3
"""End-to-end status for the free swing-terminal cloud chain."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))

import cloud_scan_worker  # noqa: E402
import cloud_watchdog  # noqa: E402
import run_scan_notify  # noqa: E402
import supabase_io  # noqa: E402
from env_loader import load_default_env  # noqa: E402


DEFAULT_EXPECTATIONS = [("etf", 50), ("stocks", 130)]
WORKFLOWS = (
    "Swing Terminal Cloud Scanner",
    "Swing Terminal Test Alert",
    "Swing Terminal Cloud Watchdog",
    "Swing Terminal CI",
    "Swing Terminal Safety",
    "Deploy Cloudflare Backup Clock",
)


def usable_secret(value: str | None) -> bool:
    return bool(value and not value.startswith("PASTE_") and not value.endswith("_HERE"))


def age_minutes(value: Any, now_utc: datetime) -> int | None:
    parsed = cloud_watchdog.parse_ts(value)
    if not parsed:
        return None
    return int((now_utc - parsed).total_seconds() // 60)


def health_summary(checks: list[dict]) -> list[dict]:
    summarized = []
    for check in checks:
        latest_ok = check.get("latest_ok") or {}
        summarized.append({
            "mode": check["mode"],
            "healthy": check["healthy"],
            "age_minutes": check["age_minutes"],
            "max_age_minutes": check["max_age_minutes"],
            "run_id": latest_ok.get("run_id"),
            "created_at": latest_ok.get("created_at"),
            "total_scanned": latest_ok.get("total_scanned"),
            "error_count": latest_ok.get("error_count"),
        })
    return summarized


def load_supabase_status() -> dict:
    config = supabase_io.config_from_env(required=True)
    assert config is not None
    now_utc = datetime.now(timezone.utc)
    runs = cloud_watchdog.latest_runs(config)
    checks = cloud_watchdog.evaluate_health(runs, now_utc, DEFAULT_EXPECTATIONS)
    watchdog_state = supabase_io.download_json_artifact(config, cloud_watchdog.STATE_KEY)
    cloudflare_state = supabase_io.download_json_artifact(config, "cloudflare-backup-clock-state")
    return {
        "ok": True,
        "runs": runs,
        "latest_issue": load_latest_issue(config, runs),
        "checks": health_summary(checks),
        "watchdog_state": watchdog_state if isinstance(watchdog_state, dict) else {},
        "cloudflare_state": cloudflare_state if isinstance(cloudflare_state, dict) else {},
    }


def load_latest_issue(config: supabase_io.SupabaseConfig, runs: list[dict]) -> dict | None:
    latest_run = next(iter(runs), None)
    if not latest_run or latest_run.get("status") == "OK":
        return None
    run_id = latest_run.get("run_id")
    if not run_id:
        return latest_run
    rows = supabase_io.request_json(
        config,
        "GET",
        "scan_runs",
        query={
            "select": "run_id,created_at,mode,status,exit_code,payload",
            "run_id": f"eq.{run_id}",
            "limit": "1",
        },
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return latest_run


def workflow_runs(repo: str, workflow: str, limit: int) -> dict:
    if not shutil.which("gh"):
        return {"ok": False, "detail": "gh CLI missing", "runs": []}

    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow,
        "--limit",
        str(limit),
        "--json",
        "databaseId,status,conclusion,createdAt,displayTitle,event,url,headBranch",
    ]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": type(exc).__name__, "runs": []}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return {"ok": False, "detail": detail[0][:180] if detail else "gh run list failed", "runs": []}
    try:
        runs = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return {"ok": False, "detail": "gh returned non-JSON output", "runs": []}
    return {"ok": True, "detail": "ok", "runs": runs}


def load_github_status(repo: str, limit: int) -> dict:
    return {
        workflow: workflow_runs(repo, workflow, limit)
        for workflow in WORKFLOWS
    }


def env_status() -> dict:
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    return {
        "SUPABASE_URL": usable_secret(os.environ.get("SUPABASE_URL")),
        "SUPABASE_PUBLISHABLE_KEY": usable_secret(publishable_key),
        "SUPABASE_SERVICE_ROLE_KEY": usable_secret(service_key),
        "SUPABASE_INGEST_TOKEN": usable_secret(os.environ.get("SUPABASE_INGEST_TOKEN")),
        "TELEGRAM_BOT_TOKEN": usable_secret(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "TELEGRAM_CHAT_ID": usable_secret(os.environ.get("TELEGRAM_CHAT_ID")),
        "CLOUDFLARE_API_TOKEN": usable_secret(os.environ.get("CLOUDFLARE_API_TOKEN")),
        "CLOUDFLARE_ACCOUNT_ID": usable_secret(os.environ.get("CLOUDFLARE_ACCOUNT_ID")),
    }


def build_status(repo: str, github_limit: int) -> dict:
    load_default_env()
    local_now = datetime.now(ZoneInfo("Europe/Stockholm"))
    status: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "local_time": local_now.isoformat(timespec="seconds"),
        "scan_window_open": cloud_scan_worker.in_stockholm_scan_window(local_now),
        "watchdog_window_open": cloud_watchdog.in_watch_window(local_now),
        "watch_window_open": cloud_watchdog.in_watch_window(local_now),
        "env": env_status(),
        "notification": run_scan_notify.notification_health(),
    }
    try:
        status["supabase"] = load_supabase_status()
    except Exception as exc:
        status["supabase"] = {"ok": False, "error": str(exc).splitlines()[0][:220]}
    status["github"] = load_github_status(repo, github_limit)
    return status


def has_action_required(status: dict) -> bool:
    if not status.get("supabase", {}).get("ok"):
        return True
    if status.get("supabase", {}).get("latest_issue"):
        return True
    latest_run = next(iter(status.get("supabase", {}).get("runs") or []), {})
    if latest_run.get("status") and latest_run.get("status") != "OK":
        return True
    watchdog_window_open = status.get("watchdog_window_open", status.get("watch_window_open"))
    if watchdog_window_open:
        checks = status.get("supabase", {}).get("checks", [])
        if any(not check.get("healthy") for check in checks):
            return True
    for workflow, payload in status.get("github", {}).items():
        if not payload.get("ok"):
            continue
        latest = (payload.get("runs") or [{}])[0]
        if workflow != "Deploy Cloudflare Backup Clock" and latest.get("conclusion") == "failure":
            return True
    return False


def format_bool(value: bool) -> str:
    return "OK" if value else "MISSING"


def format_minutes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value < 60:
        return f"{value}m"
    return f"{value // 60}h{value % 60:02d}m"


def latest_failure_text(row: dict) -> str:
    payload = row.get("payload") if isinstance(row, dict) else {}
    failure = payload.get("failure") if isinstance(payload, dict) else {}
    if not isinstance(failure, dict):
        failure = {}
    detail = failure.get("etf_coverage_error") or failure.get("stock_coverage_error")
    reason = failure.get("kind") or "unknown_reason"
    if detail:
        return f"{reason}: {detail}"
    return str(reason)


def print_text(status: dict) -> None:
    print("Swing-terminal cloud chain")
    print(f"Checked: {status['local_time']} Europe/Stockholm")
    print(f"Scanner window: {'open' if status.get('scan_window_open') else 'closed'}")
    print(f"Watchdog window: {'open' if status.get('watchdog_window_open', status.get('watch_window_open')) else 'closed'}")
    print()

    print("Secrets/config")
    for name, ok in status["env"].items():
        note = ""
        if name.startswith("CLOUDFLARE") and not ok:
            note = " (only needed for backup Worker deploy)"
        print(f"  {name:<26} {format_bool(ok)}{note}")
    notify = status.get("notification") or {}
    print(
        f"  {'NOTIFY_CLOUD_READY':<26} {format_bool(bool(notify.get('cloud_ready')))} "
        f"(channel={notify.get('channel', 'unknown')})"
    )
    print()

    supabase = status.get("supabase", {})
    print("Supabase heartbeat")
    if not supabase.get("ok"):
        print(f"  ERROR {supabase.get('error', 'unknown error')}")
    else:
        for check in supabase.get("checks", []):
            label = "fresh" if check["healthy"] else "stale"
            print(
                f"  {check['mode']:<6} {label:<5} "
                f"age={format_minutes(check['age_minutes'])}/{format_minutes(check['max_age_minutes'])} "
                f"run={check.get('run_id') or '-'} scanned={check.get('total_scanned')}"
            )
        watchdog = supabase.get("watchdog_state") or {}
        print(f"  watchdog_state={watchdog.get('status', 'unknown')} last_checked={watchdog.get('last_checked_at', '-')}")
        cloudflare = supabase.get("cloudflare_state") or {}
        cf_detail = cloudflare.get("last_checked_at") or "not deployed / no state yet"
        print(f"  cloudflare_backup={cf_detail}")
        latest_run = supabase.get("latest_issue") or {}
        if latest_run:
            print(
                f"  latest_run={latest_run.get('mode', '-')} {latest_run.get('status')} "
                f"exit={latest_run.get('exit_code')} run={latest_run.get('run_id', '-')}"
            )
            print(f"  latest_failure={latest_failure_text(latest_run)}")
    print()

    print("GitHub Actions")
    for workflow, payload in status.get("github", {}).items():
        if not payload.get("ok"):
            print(f"  {workflow}: unavailable ({payload.get('detail', 'unknown')})")
            continue
        latest = (payload.get("runs") or [{}])[0]
        conclusion = latest.get("conclusion") or latest.get("status") or "unknown"
        created = latest.get("createdAt") or "-"
        url = latest.get("url") or ""
        print(f"  {workflow}: {conclusion} at {created} {url}")

    action = "YES" if has_action_required(status) else "no"
    print()
    print(f"Action required: {action}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the full swing-terminal cloud chain")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "bilhaz03-design/macro-dashboard"))
    parser.add_argument("--github-limit", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 2 if the live chain needs action")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = build_status(repo=args.repo, github_limit=args.github_limit)
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print_text(status)
    if args.strict and has_action_required(status):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
