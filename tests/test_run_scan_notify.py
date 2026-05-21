import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import run_scan_notify


def test_stock_notification_state_is_saved_when_etf_events_are_already_deduped(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-21",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 0,
        "total_scanned": 44,
        "signal_memory": {
            "new_this_run": [{
                "key": "2026-05-21|FLXC.DE|pb126",
                "first_seen_at": "2026-05-21T11:00:00",
                "ticker": "FLXC.DE",
            }],
            "faded_this_run": [],
            "closed_this_run": [],
        },
        "signals": [],
    }))
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({
        "signals": [{
            "key": "2026-05-21|BABA|PB126",
            "ticker": "BABA",
            "name": "Alibaba ADR",
            "signal": "PB126",
            "entry": 88.5,
            "quality_score": 96,
            "tier": "TIER1",
            "action": "LIVE_REVIEW",
            "active": True,
            "first_seen_at": "2026-05-21T15:00:00",
        }],
    }))
    state_path = tmp_path / "signal-notify-state.json"
    state_path.write_text(json.dumps({
        "sent_events": ["2026-05-21|FLXC.DE|pb126|new|2026-05-21T11:00:00"],
    }))

    sent = []
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", tmp_path / "SCAN_ALERT.txt")
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args))

    assert run_scan_notify.notify_from_latest() == 0

    saved = json.loads(state_path.read_text())["sent_events"]
    assert len(sent) == 1
    assert "2026-05-21|BABA|PB126|stock_active|2026-05-21T15:00:00" in saved


def test_legacy_signal_fallback_is_deduped(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-21",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 1,
        "total_scanned": 44,
        "signal_memory": {},
        "signals": [{
            "ticker": "FLXC.DE",
            "name": "Franklin FTSE China",
            "type": "pullback-sma126",
            "entry": 4.89,
            "currency": "EUR",
            "region": "China",
        }],
    }))
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({"signals": []}))
    state_path = tmp_path / "signal-notify-state.json"

    sent = []
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", tmp_path / "SCAN_ALERT.txt")
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args))

    assert run_scan_notify.notify_from_latest() == 0
    assert run_scan_notify.notify_from_latest() == 0

    saved = json.loads(state_path.read_text())["sent_events"]
    assert len(sent) == 1
    assert "2026-05-21|FLXC.DE|pullback-sma126|legacy_active" in saved
