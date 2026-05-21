#!/usr/bin/env python3
"""Launchd-safe runner for daily_scan.py + signal notifications.

The old launchd path called a shell script directly from Desktop, which macOS
can reject with "Operation not permitted" under TCC/provenance rules. This
runner is executed by the project venv Python, matching dashboard_server.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from env_loader import load_default_env
except Exception:
    load_default_env = None
if load_default_env is not None:
    load_default_env()
SCAN = ROOT / "scripts" / "daily_scan.py"
JSON_PATH = ROOT / "data" / "latest-signals.json"
ALERT = ROOT / "data" / "SCAN_ALERT.txt"
NOTIFY_STATE = ROOT / "data" / "signal-notify-state.json"
STOCK_JOURNAL_PATH = ROOT / "data" / "stock-signal-journal.json"
_DEFAULT_DASHBOARD_URL = "" if os.environ.get("SWING_TERMINAL_CLOUD_RUN") == "1" else "http://127.0.0.1:8000/terminal.html"
DASHBOARD_URL = os.environ.get("SWING_TERMINAL_DASHBOARD_URL", _DEFAULT_DASHBOARD_URL)


def push_telegram(title: str, subtitle: str, msg: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and (token.startswith("PASTE_") or token.endswith("_HERE")):
        token = ""
    if chat_id and (chat_id.startswith("PASTE_") or chat_id.endswith("_HERE")):
        chat_id = ""
    if not token or not chat_id:
        return
    text = "\n".join(part for part in [title, subtitle, msg, DASHBOARD_URL] if part)
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=10,
        ).read()
    except Exception as exc:
        print(f"[run_scan_notify.py] telegram failed: {exc}", file=sys.stderr, flush=True)


def push(title: str, subtitle: str, msg: str, sound: str = "default") -> None:
    push_telegram(title, subtitle, msg)
    notifier = shutil.which("terminal-notifier")
    if notifier:
        try:
            subprocess.run(
                [
                    notifier,
                    "-title",
                    title,
                    "-subtitle",
                    subtitle,
                    "-message",
                    msg,
                    "-open",
                    DASHBOARD_URL,
                    "-sound",
                    sound,
                    "-group",
                    "trading-scanner",
                ],
                check=False,
            )
            return
        except Exception:
            pass
    if not Path("/usr/bin/osascript").exists():
        print(f"[run_scan_notify.py] notify: {title} | {subtitle} | {msg}", flush=True)
        return
    safe_msg = msg.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    safe_sub = subtitle.replace('"', '\\"')
    subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            f'display notification "{safe_msg}" with title "{safe_title}" subtitle "{safe_sub}" sound name "{sound}"',
        ],
        check=False,
    )


def load_notify_state() -> set[str]:
    if not NOTIFY_STATE.exists():
        return set()
    try:
        data = json.loads(NOTIFY_STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {str(item) for item in data.get("sent_events", [])}


def save_notify_state(sent_events: set[str]) -> None:
    NOTIFY_STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sent_events": sorted(sent_events)[-500:]}
    NOTIFY_STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notify_stock_journal(sent_events: set[str]) -> bool:
    if not STOCK_JOURNAL_PATH.exists():
        return False
    try:
        payload = json.loads(STOCK_JOURNAL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    wrote_state = False
    signals = payload.get("signals", []) if isinstance(payload, dict) else []
    for item in signals:
        if item.get("action") != "LIVE_REVIEW":
            continue
        key = item.get("key")
        active_id = f"{key}|stock_active|{item.get('first_seen_at')}"
        faded_id = f"{key}|stock_faded|{item.get('last_seen_at')}"
        if item.get("active"):
            if active_id in sent_events:
                continue
            push(
                "New stock scanner signal",
                f"{item.get('ticker')} — {item.get('name')}",
                f"{item.get('signal')} | Entry: {item.get('entry')} | Quality: {item.get('quality_score')} | Tier: {item.get('tier')}",
                "Ping",
            )
            sent_events.add(active_id)
            wrote_state = True
        elif active_id in sent_events and faded_id not in sent_events:
            push(
                "Stock signal faded",
                f"{item.get('ticker')} — {item.get('name')}",
                f"{item.get('signal')} | Seen: {item.get('first_seen_at')} to {item.get('last_seen_at')}",
            )
            sent_events.add(faded_id)
            wrote_state = True
    return wrote_state


def event_id(event: dict, status: str) -> str:
    stamp = event.get("first_seen_at") if status == "new" else event.get("last_seen_at")
    return f"{event.get('key')}|{status}|{stamp}"


def notify_from_latest() -> int:
    if ALERT.exists():
        last_line = ALERT.read_text(encoding="utf-8", errors="replace").splitlines()[-1:]
        push("Scanner FAILED", "Data integrity / fetch alert", last_line[0] if last_line else "SCAN_ALERT.txt exists", "Basso")
        return 0

    if not JSON_PATH.exists():
        push("Trading Scanner", "Saknar data", "latest-signals.json saknas.", "Basso")
        return 1

    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    cap = int(data.get("cap_count", 0) or 0)
    pb = int(data.get("pb_count", 0) or 0)
    pb126 = int(data.get("pb126_count", 0) or 0)
    total = cap + pb + pb126
    scan_date = data.get("date", "")
    scanned = data.get("total_scanned", 0)
    memory = data.get("signal_memory") or {}
    new_events = memory.get("new_this_run") or []
    faded_events = memory.get("faded_this_run") or []
    closed_events = memory.get("closed_this_run") or []
    sent_events = load_notify_state()
    stock_wrote_state = notify_stock_journal(sent_events)

    if new_events or faded_events or closed_events:
        wrote_state = False
        for event in new_events:
            eid = event_id(event, "new")
            if eid in sent_events:
                continue
            label = event.get("label", "SIGNAL")
            entry = event.get("last_entry", event.get("first_entry"))
            cur = event.get("currency", "")
            push(
                f"New scanner signal: {label}",
                f"{event.get('ticker')} — {event.get('name')}",
                f"Entry: {entry} {cur} | First seen: {event.get('first_seen_at')} | Status: active",
                "Ping",
            )
            sent_events.add(eid)
            wrote_state = True
        for event in faded_events:
            eid = event_id(event, "faded")
            if eid in sent_events:
                continue
            label = event.get("label", "SIGNAL")
            entry = event.get("last_entry", event.get("first_entry"))
            cur = event.get("currency", "")
            push(
                f"Signal faded: {label}",
                f"{event.get('ticker')} — {event.get('name')}",
                f"Seen: {event.get('first_seen_at')} to {event.get('last_seen_at')} | Last entry: {entry} {cur}",
            )
            sent_events.add(eid)
            wrote_state = True
        for event in closed_events:
            status = event.get("status", "CLOSE_CHECK")
            eid = event_id(event, str(status).lower())
            if eid in sent_events:
                continue
            label = event.get("label", "SIGNAL")
            push(
                f"Signal close check: {label}",
                f"{event.get('ticker')} — {event.get('name')}",
                f"{status} | First seen: {event.get('first_seen_at')} | Last active: {event.get('last_seen_at')}",
                "Ping" if status == "CLOSE_CONFIRMED" else "default",
            )
            sent_events.add(eid)
            wrote_state = True
        if wrote_state or stock_wrote_state:
            save_notify_state(sent_events)
        return 0

    if stock_wrote_state:
        save_notify_state(sent_events)

    if total == 0:
        if os.environ.get("FORCE_QUIET_NOTIFY") != "1" and os.environ.get("SWING_TERMINAL_CLOUD_RUN") != "1":
            push(
                "Trading Scanner",
                f"Inga signaler ({scan_date})",
                f"{scanned} tickers scannade, 0 capitulation, 0 pullback, 0 PB126.",
            )
        return 0

    wrote_state = False
    for sig in data.get("signals", []):
        eid = f"{scan_date}|{sig.get('ticker')}|{sig.get('type')}|legacy_active"
        if eid in sent_events:
            continue
        sig_type = sig.get("type")
        if sig_type == "capitulation":
            icon = "🔴"
            label = "Capitulation"
            sound = "Ping"
        elif sig_type == "pullback-sma126":
            icon = "🟢"
            label = "PB126"
            sound = "Ping"
        else:
            icon = "🟡"
            label = "Pullback"
            sound = "default"

        parts = [f"Entry: {sig.get('entry')} {sig.get('currency', '')}", "Stop: none (§5e.2)"]
        if sig.get("units") is not None:
            parts.append(f"{sig['units']}u")
        if sig.get("risk_pct") is not None:
            parts.append(f"Smärt: {sig['risk_pct']}%")
        push(
            f"{icon} {label}: {sig.get('region', '')}",
            f"{sig.get('ticker')} — {sig.get('name')}",
            "  |  ".join(parts),
            sound,
        )
        sent_events.add(eid)
        wrote_state = True
    if wrote_state:
        save_notify_state(sent_events)
    return 0


def main() -> int:
    os.chdir(ROOT)
    print("[run_scan_notify.py] START", flush=True)
    proc = subprocess.run([sys.executable, str(SCAN), *sys.argv[1:]], cwd=ROOT)
    print(f"[run_scan_notify.py] daily_scan exit={proc.returncode}", flush=True)
    try:
        notify_rc = notify_from_latest()
        print(f"[run_scan_notify.py] notify exit={notify_rc}", flush=True)
    except Exception as exc:
        print(f"[run_scan_notify.py] notify failed: {exc}", file=sys.stderr, flush=True)
    print(f"[run_scan_notify.py] END exit={proc.returncode}", flush=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
