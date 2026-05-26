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
            "action": "TRADE",
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


def test_live_review_stock_notification_is_labeled_as_review(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-22",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 0,
        "total_scanned": 44,
        "signal_memory": {},
        "signals": [],
    }))
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({
        "signals": [{
            "key": "2026-05-22|KEYS|MLPB50 STRICT",
            "ticker": "KEYS",
            "name": "Keysight Technologies",
            "signal": "MLPB50 STRICT",
            "entry": 346.56,
            "quality_score": 100,
            "tier": "TIER1",
            "action": "LIVE_REVIEW",
            "active": True,
            "first_seen_at": "2026-05-25T09:00:00",
            "current_gate": "TRADE_REVIEW_MANUAL_PENDING",
            "prime_tier": "A_PLUS_TRADE_CANDIDATE",
            "qt_label": "QT_SUPPORT",
            "qt_phase": "REPAIRING",
            "qt_wait_label": "LOW_WAIT_VALUE",
            "qt_confirmation": "FRAMEWORK_VALID_ENOUGH",
            "earnings_date": "2026-08-18",
        }],
    }))
    state_path = tmp_path / "signal-notify-state.json"

    sent = []
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", tmp_path / "SCAN_ALERT.txt")
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args))

    assert run_scan_notify.notify_from_latest() == 0

    assert sent[0][0] == "New stock review signal"
    assert "Action: Review - manual checks required" in sent[0][2]
    assert "Gate: TRADE_REVIEW_MANUAL_PENDING" in sent[0][2]
    assert "Prime: A_PLUS_TRADE_CANDIDATE" in sent[0][2]
    assert "QT: QT_SUPPORT / REPAIRING / LOW_WAIT_VALUE / FRAMEWORK_VALID_ENOUGH" in sent[0][2]


def test_test_alert_stock_notification_is_clearly_marked(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-26",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 0,
        "total_scanned": 0,
        "signal_memory": {},
        "signals": [],
    }))
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({
        "signals": [{
            "key": "TEST_SIGNAL_ALERT|unit",
            "ticker": "TEST",
            "name": "Controlled Telegram path test",
            "signal": "TEST SIGNAL — no trade",
            "entry": "n/a",
            "quality_score": "n/a",
            "tier": "TEST_ONLY",
            "action": "LIVE_REVIEW",
            "active": True,
            "first_seen_at": "2026-05-26T20:00:00+00:00",
            "current_gate": "TEST_ONLY_NO_TRADE",
            "prime_tier": "TEST_PIPELINE_ONLY",
            "test_alert": True,
        }],
    }))
    state_path = tmp_path / "signal-notify-state.json"

    sent = []
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", tmp_path / "SCAN_ALERT.txt")
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args) or True)

    assert run_scan_notify.notify_from_latest() == 0

    assert sent[0][0] == "TEST — New stock review signal"
    assert "TEST ALERT — no trade; notification pipeline proof only" in sent[0][2]
    assert "TEST SIGNAL — no trade" in sent[0][2]
    assert "Gate: TEST_ONLY_NO_TRADE" in sent[0][2]


def test_notify_test_signal_alert_dry_run_uses_temp_state(capsys):
    assert run_scan_notify.notify_test_signal_alert(dry_run=True, source="unit") == 0

    out = capsys.readouterr().out
    assert "test-alert dry-run" in out
    assert "TEST — New stock review signal" in out
    assert "TEST ALERT — no trade" in out


def test_wait_signal_stock_thesis_notification_uses_lifecycle(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-22",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 0,
        "total_scanned": 44,
        "signal_memory": {},
        "signals": [],
    }))
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({
        "signals": [{
            "key": "2026-05-22|KEYS|MLPB50 QUALITY",
            "ticker": "KEYS",
            "name": "Keysight Technologies",
            "signal": "MLPB50 QUALITY",
            "entry": 146.56,
            "quality_score": 94,
            "tier": "NO_TRADE",
            "action": "WAIT_SIGNAL",
            "active": True,
            "first_seen_at": "2026-05-25T09:00:00",
            "lifecycle": {"state": "UPGRADED", "label": "Improving"},
            "thesis": {
                "stance": "Alive, but wait for confirmation",
                "subjective_probability": 59,
                "wait_for": ["Needs stronger follow-through before capital"],
                "buy_if": ["Price confirms and signal stays alive into close."],
            },
        }],
    }))
    state_path = tmp_path / "signal-notify-state.json"

    sent = []
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", tmp_path / "SCAN_ALERT.txt")
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args))

    assert run_scan_notify.notify_from_latest() == 0

    assert sent[0][0] == "Stock thesis improving"
    assert "Thesis: Alive, but wait for confirmation (59%)" in sent[0][2]
    assert "Lifecycle: Improving" in sent[0][2]
    assert "Buy if: Price confirms and signal stays alive into close." in sent[0][2]


def test_notification_health_reports_cloud_readiness(monkeypatch):
    monkeypatch.setattr(run_scan_notify.shutil, "which", lambda _: None)
    monkeypatch.setattr(run_scan_notify.Path, "exists", lambda self: False)

    health = run_scan_notify.notification_health({
        "TELEGRAM_BOT_TOKEN": "123456:abc",
        "TELEGRAM_CHAT_ID": "7607840802",
    })

    assert health["telegram_ready"] is True
    assert health["cloud_ready"] is True
    assert health["channel"] == "telegram"


def test_notification_health_ignores_placeholder_secrets(monkeypatch):
    monkeypatch.setattr(run_scan_notify.shutil, "which", lambda _: None)
    monkeypatch.setattr(run_scan_notify.Path, "exists", lambda self: False)

    health = run_scan_notify.notification_health({
        "TELEGRAM_BOT_TOKEN": "PASTE_TELEGRAM_BOT_TOKEN_HERE",
        "TELEGRAM_CHAT_ID": "PASTE_TELEGRAM_CHAT_ID_HERE",
    })

    assert health["telegram_ready"] is False
    assert health["cloud_ready"] is False
    assert health["channel"] == "log"


def test_data_integrity_alert_is_warning_not_failure():
    title, subtitle, message, sound = run_scan_notify.classify_alert(
        "[2026-05-26T07:52:55] DATA_INTEGRITY — 4/44 tickers exkluderade ({'STALE_DATA': 4}). Rapport: data/daily-scan-2026-05-26.txt"
    )

    assert title == "Scanner degraded"
    assert subtitle == "Data integrity warning"
    assert "4/44" in message
    assert sound == "default"


def test_obsolete_alert_is_ignored_when_latest_json_is_newer(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text(json.dumps({
        "date": "2026-05-26",
        "cap_count": 0,
        "pb_count": 0,
        "pb126_count": 0,
        "total_scanned": 44,
        "signal_memory": {},
        "signals": [],
    }))
    alert_path = tmp_path / "SCAN_ALERT.txt"
    alert_path.write_text("[2026-05-25T18:02:46] FETCH_FAILURE — 44/44 tickers misslyckades. Rapport: data/daily-scan-2026-05-25.txt\n")
    state_path = tmp_path / "signal-notify-state.json"
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text(json.dumps({"signals": []}))

    sent = []
    monkeypatch.setenv("FORCE_QUIET_NOTIFY", "1")
    monkeypatch.setattr(run_scan_notify, "JSON_PATH", latest_path)
    monkeypatch.setattr(run_scan_notify, "ALERT", alert_path)
    monkeypatch.setattr(run_scan_notify, "NOTIFY_STATE", state_path)
    monkeypatch.setattr(run_scan_notify, "STOCK_JOURNAL_PATH", stock_path)
    monkeypatch.setattr(run_scan_notify, "push", lambda *args, **kwargs: sent.append(args))

    assert run_scan_notify.notify_from_latest() == 0

    assert sent == []
    assert not alert_path.exists()


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
