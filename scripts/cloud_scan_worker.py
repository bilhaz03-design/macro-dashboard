#!/usr/bin/env python3
"""Cloud runner for the swing terminal.

Run this from Render Cron, a small VPS, or GitHub Actions. The critical pattern:

1. Restore scanner memory from Supabase before running.
2. Run the existing Python scanner locally in this process environment.
3. Send Telegram/macOS-safe notifications.
4. Publish fresh state back to Supabase.

No broker order placement happens here.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(ROOT / "scripts"))

import supabase_io  # noqa: E402
from env_loader import load_default_env  # noqa: E402


RESTORED_ARTIFACTS = {
    "latest-signals": DATA_DIR / "latest-signals.json",
    "signal-journal": DATA_DIR / "signal-journal.json",
    "stock-signal-journal": DATA_DIR / "stock-signal-journal.json",
    "signal-notify-state": DATA_DIR / "signal-notify-state.json",
    "live-trades": DATA_DIR / "live-trades.json",
    "execution-map": DATA_DIR / "execution_map.json",
}

PUBLISHED_JSON_ARTIFACTS = {
    "latest-signals": ("latest_signals", DATA_DIR / "latest-signals.json"),
    "signal-journal": ("signal_journal", DATA_DIR / "signal-journal.json"),
    "stock-signal-journal": ("stock_signal_journal", DATA_DIR / "stock-signal-journal.json"),
    "signal-notify-state": ("notify_state", DATA_DIR / "signal-notify-state.json"),
    "live-trades": ("live_trades", DATA_DIR / "live-trades.json"),
    "execution-map": ("execution_map", DATA_DIR / "execution_map.json"),
    "stock-current-coverage": ("stock_current_coverage", DATA_DIR / "stock_framework_current_scan_coverage.json"),
}

PUBLISHED_TEXT_ARTIFACTS = {
    "scan-data-js": ("scan_data_js", ROOT / "dashboard" / "scan_data.js"),
    "stock-data-js": ("stock_data_js", ROOT / "dashboard" / "stock_data.js"),
}

FRESH_SCAN_DEDUPE_WINDOWS = {
    "etf": ("SWING_TERMINAL_DEDUPE_ETF_MINUTES", 10),
    "stocks": ("SWING_TERMINAL_DEDUPE_STOCKS_MINUTES", 45),
}

STOCK_COVERAGE_PATH = DATA_DIR / "stock_framework_current_scan_coverage.json"
STOCK_COVERAGE_FAIL_EXIT_CODE = 4
ETF_SIGNALS_PATH = DATA_DIR / "latest-signals.json"
ETF_COVERAGE_FAIL_EXIT_CODE = 5


def now_stockholm() -> datetime:
    return datetime.now(ZoneInfo("Europe/Stockholm"))


def in_stockholm_scan_window(dt: datetime) -> bool:
    local = dt.astimezone(ZoneInfo("Europe/Stockholm"))
    if local.weekday() >= 5:
        return False
    return time(9, 0) <= local.time() <= time(22, 15)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def iso_date_or_none(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


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


def parse_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def parse_ratio_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0 <= value <= 1 else default


def dedupe_window_minutes(mode: str) -> int | None:
    spec = FRESH_SCAN_DEDUPE_WINDOWS.get(mode)
    if not spec:
        return None
    env_name, default = spec
    return parse_positive_int_env(env_name, default)


def latest_runs(config: supabase_io.SupabaseConfig, *, limit: int = 24) -> list[dict]:
    rows = supabase_io.request_json(
        config,
        "GET",
        "scan_runs",
        query={
            "select": "run_id,created_at,mode,status,exit_code",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    return rows if isinstance(rows, list) else []


def mode_matches(row: dict, mode: str) -> bool:
    row_mode = str(row.get("mode") or "")
    return row_mode == mode or row_mode == "all"


def latest_mode_run(rows: list[dict], mode: str) -> dict | None:
    return next((row for row in rows if mode_matches(row, mode)), None)


def fresh_scan_skip_reason(
    config: supabase_io.SupabaseConfig,
    mode: str,
    *,
    now_utc: datetime | None = None,
) -> str | None:
    window_minutes = dedupe_window_minutes(mode)
    if window_minutes is None:
        return None

    row = latest_mode_run(latest_runs(config), mode)
    if not row or row.get("status") != "OK":
        return None
    created_at = parse_ts(row.get("created_at"))
    if not created_at:
        return None

    now_utc = now_utc or datetime.now(timezone.utc)
    age_minutes = int(max(0, (now_utc - created_at).total_seconds()) // 60)
    if age_minutes > window_minutes:
        return None

    run_id = row.get("run_id") or "unknown-run"
    return f"fresh {mode} OK run {age_minutes}m ago (window={window_minutes}m, run_id={run_id})"


def stock_coverage_quality_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "coverage payload missing or malformed"
    try:
        stocks = int(payload.get("stocks") or 0)
        ok = int(payload.get("ok") or 0)
        fail = int(payload.get("fail") or 0)
    except (TypeError, ValueError):
        return "coverage counts are not numeric"
    if stocks <= 0:
        return "coverage has no stock universe size"

    min_ok_ratio = parse_ratio_env("SWING_TERMINAL_STOCK_MIN_OK_RATIO", 0.95)
    max_fail_ratio = parse_ratio_env("SWING_TERMINAL_STOCK_MAX_FAIL_RATIO", 0.05)
    min_ok = math.ceil(stocks * min_ok_ratio)
    max_fail = math.floor(stocks * max_fail_ratio)
    if ok < min_ok:
        return f"ok={ok} below min_ok={min_ok} ({min_ok_ratio:.0%} of {stocks})"
    if fail > max_fail:
        return f"fail={fail} above max_fail={max_fail} ({max_fail_ratio:.0%} of {stocks})"
    return None


def validate_stock_coverage(path: Path = STOCK_COVERAGE_PATH) -> int:
    payload = load_json(path, None)
    error = stock_coverage_quality_error(payload)
    if error:
        print(f"[cloud_scan_worker] stock coverage FAIL: {error}", file=sys.stderr, flush=True)
        return STOCK_COVERAGE_FAIL_EXIT_CODE
    assert isinstance(payload, dict)
    print(
        "[cloud_scan_worker] stock coverage OK: "
        f"ok={payload.get('ok')} fail={payload.get('fail')} stocks={payload.get('stocks')} current={payload.get('current')}",
        flush=True,
    )
    return 0


def etf_coverage_quality_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "latest-signals payload missing or malformed"
    try:
        total = int(payload.get("total_scanned") or 0)
        errors = int(payload.get("err_count") or 0)
        skips = int(payload.get("skip_count") or 0)
    except (TypeError, ValueError):
        return "ETF coverage counts are not numeric"
    min_total = parse_positive_int_env("SWING_TERMINAL_ETF_MIN_SCANNED", 40)
    if total < min_total:
        return f"total_scanned={total} below min_total={min_total}"
    if total <= 0:
        return "ETF coverage has no scanned instruments"

    max_error_ratio = parse_ratio_env("SWING_TERMINAL_ETF_MAX_ERROR_RATIO", 0.10)
    max_skip_ratio = parse_ratio_env("SWING_TERMINAL_ETF_MAX_SKIP_RATIO", 0.20)
    max_errors = math.floor(total * max_error_ratio)
    max_skips = math.floor(total * max_skip_ratio)
    if errors > max_errors:
        return f"err_count={errors} above max_errors={max_errors} ({max_error_ratio:.0%} of {total})"
    if skips > max_skips:
        return f"skip_count={skips} above max_skips={max_skips} ({max_skip_ratio:.0%} of {total})"
    return None


def validate_etf_coverage(path: Path = ETF_SIGNALS_PATH) -> int:
    payload = load_json(path, None)
    error = etf_coverage_quality_error(payload)
    if error:
        print(f"[cloud_scan_worker] ETF coverage FAIL: {error}", file=sys.stderr, flush=True)
        return ETF_COVERAGE_FAIL_EXIT_CODE
    assert isinstance(payload, dict)
    print(
        "[cloud_scan_worker] ETF coverage OK: "
        f"total={payload.get('total_scanned')} errors={payload.get('err_count')} skips={payload.get('skip_count')} "
        f"signals={int(payload.get('cap_count', 0) or 0) + int(payload.get('pb_count', 0) or 0) + int(payload.get('pb126_count', 0) or 0)}",
        flush=True,
    )
    return 0


def failure_kind_for_exit_code(exit_code: int) -> str | None:
    if exit_code == 0:
        return None
    if exit_code == ETF_COVERAGE_FAIL_EXIT_CODE:
        return "etf_coverage_quality_gate"
    if exit_code == STOCK_COVERAGE_FAIL_EXIT_CODE:
        return "stock_coverage_quality_gate"
    return "scanner_command_failure"


def run_failure_payload(mode: str, exit_code: int, latest: Any, stock_coverage: Any) -> dict | None:
    kind = failure_kind_for_exit_code(exit_code)
    if not kind:
        return None

    payload = {
        "kind": kind,
        "exit_code": exit_code,
    }
    if mode in {"all", "etf"}:
        etf_error = etf_coverage_quality_error(latest)
        if etf_error:
            payload["etf_coverage_error"] = etf_error
    if mode in {"all", "stocks"}:
        stock_error = stock_coverage_quality_error(stock_coverage)
        if stock_error:
            payload["stock_coverage_error"] = stock_error
    return payload


def run(cmd: list[str]) -> int:
    print(f"[cloud_scan_worker] run: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "SWING_TERMINAL_ROOT": str(ROOT)})
    print(f"[cloud_scan_worker] exit={proc.returncode}: {' '.join(cmd)}", flush=True)
    return int(proc.returncode)


def restore_state(config: supabase_io.SupabaseConfig) -> list[str]:
    restored: list[str] = []
    for artifact_key, path in RESTORED_ARTIFACTS.items():
        try:
            if supabase_io.restore_json_artifact(config, artifact_key, path):
                restored.append(artifact_key)
        except supabase_io.SupabaseError as exc:
            print(f"[cloud_scan_worker] restore failed for {artifact_key}: {exc}", file=sys.stderr, flush=True)
    return restored


def event_rows_from_journal(source: str, journal: dict) -> list[dict]:
    rows = []
    for item in journal.get("signals", []) if isinstance(journal, dict) else []:
        signal_key = str(item.get("key") or "")
        if not signal_key:
            continue
        status = str(item.get("status") or "UNKNOWN")
        rows.append({
            "event_key": f"{source}|{signal_key}",
            "source": source,
            "signal_key": signal_key,
            "scan_date": iso_date_or_none(item.get("date")),
            "ticker": item.get("ticker"),
            "name": item.get("name"),
            "signal_type": item.get("type") or item.get("signal") or item.get("signal_key"),
            "status": status,
            "active": bool(item.get("active")),
            "first_seen_at": item.get("first_seen_at"),
            "last_seen_at": item.get("last_seen_at"),
            "last_checked_at": item.get("last_checked_at"),
            "payload": item,
            "updated_at": supabase_io.utc_now(),
        })
    return rows


def live_trade_rows(payload: dict) -> list[dict]:
    return [
        {
            "id": str(trade["id"]),
            "status": trade.get("status"),
            "signal_key": trade.get("signal_key"),
            "execution_ticker": trade.get("execution_ticker"),
            "payload": trade,
            "updated_at": supabase_io.utc_now(),
        }
        for trade in payload.get("trades", [])
        if isinstance(trade, dict) and trade.get("id")
    ] if isinstance(payload, dict) else []


def execution_map_rows(payload: dict) -> list[dict]:
    mappings = payload.get("mappings", {}) if isinstance(payload, dict) else {}
    return [
        {
            "signal_ticker": str(signal_ticker),
            "execution_ticker": mapping.get("execution_ticker"),
            "payload": mapping,
            "updated_at": supabase_io.utc_now(),
        }
        for signal_ticker, mapping in mappings.items()
        if isinstance(mapping, dict)
    ]


def journal_scan_date(journal: Any) -> str | None:
    if not isinstance(journal, dict):
        return None
    return iso_date_or_none(journal.get("scan_date") or journal.get("date"))


def latest_scan_date(latest: Any) -> str | None:
    if not isinstance(latest, dict):
        return None
    return iso_date_or_none(latest.get("date") or latest.get("scan_date"))


def infer_run_scan_date(mode: str, latest: dict, stock_journal: dict) -> str | None:
    latest_date = latest_scan_date(latest)
    stock_date = journal_scan_date(stock_journal)
    if mode == "stocks":
        return stock_date or latest_date
    if mode == "etf":
        return latest_date or stock_date
    return latest_date or stock_date


def artifact_scan_date(artifact_key: str, path: Path, fallback: str | None) -> str | None:
    payload = load_json(path, None)
    if artifact_key == "latest-signals":
        return latest_scan_date(payload) or fallback
    if artifact_key in {"signal-journal", "stock-signal-journal"}:
        return journal_scan_date(payload) or fallback
    if artifact_key == "stock-current-coverage" and isinstance(payload, dict):
        return iso_date_or_none(payload.get("generated_at")) or fallback
    return fallback


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def summed_counts(*values: Any) -> int | None:
    parsed = [value for value in (int_or_none(item) for item in values) if value is not None]
    return sum(parsed) if parsed else None


def run_total_scanned(mode: str, latest: Any, stock_coverage: Any) -> int | None:
    latest_total = latest.get("total_scanned") if isinstance(latest, dict) else None
    stock_total = stock_coverage.get("stocks") if isinstance(stock_coverage, dict) else None
    if mode == "stocks":
        return int_or_none(stock_total)
    if mode == "all":
        return summed_counts(latest_total, stock_total)
    return int_or_none(latest_total)


def run_error_count(mode: str, latest: Any, stock_coverage: Any) -> int | None:
    latest_errors = latest.get("err_count") if isinstance(latest, dict) else None
    stock_errors = stock_coverage.get("fail") if isinstance(stock_coverage, dict) else None
    if mode == "stocks":
        return int_or_none(stock_errors)
    if mode == "all":
        return summed_counts(latest_errors, stock_errors)
    return int_or_none(latest_errors)


def summarize_run(run_id: str, mode: str, status: str, exit_code: int) -> dict:
    latest = load_json(ETF_SIGNALS_PATH, {})
    stock_journal = load_json(DATA_DIR / "stock-signal-journal.json", {})
    stock_coverage = load_json(STOCK_COVERAGE_PATH, {})
    stock_signals = stock_journal.get("signals", []) if isinstance(stock_journal, dict) else []
    payload = {
        "latest": latest,
        "stock_signal_memory": {
            "scan_date": stock_journal.get("scan_date") if isinstance(stock_journal, dict) else None,
            "active": sum(1 for item in stock_signals if item.get("active")),
            "total": len(stock_signals),
        },
        "stock_current_coverage": {
            "generated_at": stock_coverage.get("generated_at") if isinstance(stock_coverage, dict) else None,
            "stocks": stock_coverage.get("stocks") if isinstance(stock_coverage, dict) else None,
            "ok": stock_coverage.get("ok") if isinstance(stock_coverage, dict) else None,
            "fail": stock_coverage.get("fail") if isinstance(stock_coverage, dict) else None,
            "current": stock_coverage.get("current") if isinstance(stock_coverage, dict) else None,
        },
    }
    failure = run_failure_payload(mode, exit_code, latest, stock_coverage)
    if failure:
        payload["failure"] = failure
    return {
        "run_id": run_id,
        "created_at": supabase_io.utc_now(),
        "scan_date": infer_run_scan_date(mode, latest, stock_journal),
        "runner": os.environ.get("RENDER_SERVICE_NAME") or os.environ.get("GITHUB_WORKFLOW") or "local",
        "mode": mode,
        "status": status,
        "exit_code": exit_code,
        "etf_signals": int(latest.get("cap_count", 0) or 0) + int(latest.get("pb_count", 0) or 0) + int(latest.get("pb126_count", 0) or 0),
        "stock_live_review": sum(1 for item in stock_signals if item.get("action") == "LIVE_REVIEW" and item.get("active")),
        "total_scanned": run_total_scanned(mode, latest, stock_coverage),
        "error_count": run_error_count(mode, latest, stock_coverage),
        "skip_count": latest.get("skip_count"),
        "payload": payload,
    }


def publish_state(config: supabase_io.SupabaseConfig, run_summary: dict) -> None:
    scan_date = run_summary.get("scan_date")
    for artifact_key, (kind, path) in PUBLISHED_JSON_ARTIFACTS.items():
        if path.exists():
            supabase_io.upload_json_file(
                config,
                artifact_key=artifact_key,
                kind=kind,
                path=path,
                scan_date=artifact_scan_date(artifact_key, path, scan_date),
            )
    for artifact_key, (kind, path) in PUBLISHED_TEXT_ARTIFACTS.items():
        if path.exists():
            supabase_io.upload_text_file(
                config,
                artifact_key=artifact_key,
                kind=kind,
                path=path,
                scan_date=scan_date,
            )

    etf_journal = load_json(DATA_DIR / "signal-journal.json", {})
    stock_journal = load_json(DATA_DIR / "stock-signal-journal.json", {})
    supabase_io.upsert_rows(
        config,
        "signal_events",
        event_rows_from_journal("etf", etf_journal) + event_rows_from_journal("stock", stock_journal),
        on_conflict="event_key",
    )

    live_trades = load_json(DATA_DIR / "live-trades.json", {})
    supabase_io.upsert_rows(config, "live_trades", live_trade_rows(live_trades), on_conflict="id")

    execution_map = load_json(DATA_DIR / "execution_map.json", {})
    supabase_io.upsert_rows(config, "execution_map", execution_map_rows(execution_map), on_conflict="signal_ticker")


def publish_run_summary(config: supabase_io.SupabaseConfig, run_summary: dict) -> None:
    supabase_io.upsert_rows(config, "scan_runs", [run_summary], on_conflict="run_id")


def publish_cloud_result(config: supabase_io.SupabaseConfig, run_summary: dict) -> str:
    if run_summary.get("status") == "OK" and int(run_summary.get("exit_code") or 0) == 0:
        publish_state(config, run_summary)
        publish_run_summary(config, run_summary)
        return "published state to Supabase"

    publish_run_summary(config, run_summary)
    return "recorded failed run without replacing scanner artifacts"


def notify_latest() -> None:
    try:
        import run_scan_notify  # noqa: WPS433

        run_scan_notify.notify_from_latest()
    except Exception as exc:
        print(f"[cloud_scan_worker] notify failed: {exc}", file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud runner for the swing terminal scanner")
    parser.add_argument("--mode", choices=["all", "etf", "stocks"], default="all")
    parser.add_argument("--portfolio", type=float, default=60_000)
    parser.add_argument("--date", default=None, help="Optional YYYY-MM-DD override for daily_scan.py")
    parser.add_argument("--daily-dry-run", action="store_true", help="Pass --dry-run to daily_scan.py")
    parser.add_argument("--no-upload", action="store_true", help="Do not publish to Supabase")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Telegram/macOS notifications")
    parser.add_argument("--no-restore", action="store_true", help="Do not restore state from Supabase before scan")
    parser.add_argument("--force", action="store_true", help="Ignore weekday/hour guard")
    parser.add_argument("--respect-market-hours", action="store_true", default=True)
    return parser.parse_args()


def main() -> int:
    load_default_env()
    args = parse_args()
    os.chdir(ROOT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    local_now = now_stockholm()
    runner_instance = os.environ.get("RENDER_INSTANCE_ID") or os.environ.get("GITHUB_RUN_ID") or "local"
    run_id = f"{local_now.strftime('%Y%m%dT%H%M%S')}-{runner_instance}"
    if args.respect_market_hours and not args.force and not in_stockholm_scan_window(local_now):
        print(f"[cloud_scan_worker] outside scan window Europe/Stockholm: {local_now.isoformat(timespec='seconds')}")
        return 0

    config = None if args.no_upload else supabase_io.config_from_env(required=True)
    if config and not args.no_restore:
        restored = restore_state(config)
        print(f"[cloud_scan_worker] restored: {', '.join(restored) if restored else 'none'}", flush=True)
    if config and not args.force:
        try:
            skip_reason = fresh_scan_skip_reason(config, args.mode)
        except supabase_io.SupabaseError as exc:
            skip_reason = None
            print(f"[cloud_scan_worker] fresh-scan dedupe check failed, continuing: {exc}", file=sys.stderr, flush=True)
        if skip_reason:
            print(f"[cloud_scan_worker] skip duplicate scheduled scan: {skip_reason}", flush=True)
            return 0

    exit_code = 0
    if args.mode in {"all", "etf"}:
        cmd = [sys.executable, str(ROOT / "scripts" / "daily_scan.py"), "--portfolio", str(args.portfolio)]
        if args.date:
            cmd += ["--date", args.date]
        if args.daily_dry_run:
            cmd.append("--dry-run")
        exit_code = max(exit_code, run(cmd))
        exit_code = max(exit_code, validate_etf_coverage())

    if args.mode in {"all", "stocks"} and not args.daily_dry_run:
        exit_code = max(exit_code, run([sys.executable, str(ROOT / "scripts" / "stock_current_scan.py")]))
        exit_code = max(exit_code, validate_stock_coverage())
        stock_inputs = [
            DATA_DIR / "stock_framework_robustness_summary.json",
            DATA_DIR / "stock_framework_walkforward_summary.json",
            DATA_DIR / "stock_framework_deep_current_signals.json",
        ]
        if all(path.exists() for path in stock_inputs):
            exit_code = max(exit_code, run([sys.executable, str(ROOT / "scripts" / "export_stock_terminal_data.py")]))
        else:
            print("[cloud_scan_worker] stock export skipped: missing stock framework JSON inputs", flush=True)

    if not args.no_notify and exit_code == 0:
        notify_latest()

    status = "OK" if exit_code == 0 else "FAIL"
    run_summary = summarize_run(run_id=run_id, mode=args.mode, status=status, exit_code=exit_code)
    if config:
        try:
            publish_message = publish_cloud_result(config, run_summary)
            print(f"[cloud_scan_worker] {publish_message}", flush=True)
        except supabase_io.SupabaseError as exc:
            print(f"[cloud_scan_worker] publish failed: {exc}", file=sys.stderr, flush=True)
            return max(exit_code, 3)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
