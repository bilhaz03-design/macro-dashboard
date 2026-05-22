import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cloud_scan_worker


def test_stockholm_scan_window_guards_weekends_and_hours():
    tz = ZoneInfo("Europe/Stockholm")

    assert cloud_scan_worker.in_stockholm_scan_window(datetime(2026, 5, 21, 9, 0, tzinfo=tz)) is True
    assert cloud_scan_worker.in_stockholm_scan_window(datetime(2026, 5, 21, 22, 15, tzinfo=tz)) is True
    assert cloud_scan_worker.in_stockholm_scan_window(datetime(2026, 5, 21, 8, 59, tzinfo=tz)) is False
    assert cloud_scan_worker.in_stockholm_scan_window(datetime(2026, 5, 23, 12, 0, tzinfo=tz)) is False


def test_event_rows_from_journal_flattens_etf_signal():
    rows = cloud_scan_worker.event_rows_from_journal("etf", {
        "signals": [{
            "key": "2026-05-21|FLXC.DE|pb126",
            "date": "2026-05-21",
            "ticker": "FLXC.DE",
            "name": "Franklin FTSE China",
            "type": "pullback-sma126",
            "status": "FADED_INTRADAY",
            "active": False,
            "first_seen_at": "2026-05-21T11:16:22",
            "last_seen_at": "2026-05-21T14:43:21",
        }],
    })

    assert rows[0]["event_key"] == "etf|2026-05-21|FLXC.DE|pb126"
    assert rows[0]["scan_date"] == "2026-05-21"
    assert rows[0]["signal_type"] == "pullback-sma126"
    assert rows[0]["status"] == "FADED_INTRADAY"
    assert rows[0]["active"] is False


def test_live_trade_rows_and_execution_map_rows():
    trades = cloud_scan_worker.live_trade_rows({
        "trades": [{
            "id": "2026-05-21-icga-china",
            "status": "USER_REPORTED_BOUGHT_FILL_UNCONFIRMED",
            "signal_key": "2026-05-21|FLXC.DE|pb126",
            "execution_ticker": "ICGA",
        }],
    })
    mappings = cloud_scan_worker.execution_map_rows({
        "mappings": {
            "FLXC.DE": {
                "execution_ticker": "ICGA",
                "broker": "Nordnet",
            },
        },
    })

    assert trades[0]["id"] == "2026-05-21-icga-china"
    assert trades[0]["execution_ticker"] == "ICGA"
    assert mappings[0]["signal_ticker"] == "FLXC.DE"
    assert mappings[0]["execution_ticker"] == "ICGA"


def test_infer_run_scan_date_prefers_mode_specific_state():
    latest = {"date": "2026-05-20"}
    stock_journal = {"scan_date": "2026-05-21"}

    assert cloud_scan_worker.infer_run_scan_date("etf", latest, stock_journal) == "2026-05-20"
    assert cloud_scan_worker.infer_run_scan_date("stocks", latest, stock_journal) == "2026-05-21"
    assert cloud_scan_worker.infer_run_scan_date("all", latest, stock_journal) == "2026-05-20"


def test_artifact_scan_date_uses_payload_date(tmp_path):
    latest_path = tmp_path / "latest-signals.json"
    latest_path.write_text('{"date": "2026-05-20"}')
    stock_path = tmp_path / "stock-signal-journal.json"
    stock_path.write_text('{"scan_date": "2026-05-21", "signals": []}')
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text('{"generated_at": "2026-05-22T08:05:00", "ok": 108}')

    assert cloud_scan_worker.artifact_scan_date("latest-signals", latest_path, "2026-05-23") == "2026-05-20"
    assert cloud_scan_worker.artifact_scan_date("stock-signal-journal", stock_path, "2026-05-23") == "2026-05-21"
    assert cloud_scan_worker.artifact_scan_date("stock-current-coverage", coverage_path, "2026-05-23") == "2026-05-22"


def test_fresh_scan_skip_reason_skips_recent_ok_mode(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    monkeypatch.setattr(
        cloud_scan_worker,
        "latest_runs",
        lambda config: [{
            "run_id": "fresh-etf",
            "created_at": "2026-05-22T08:55:00+00:00",
            "mode": "etf",
            "status": "OK",
        }],
    )

    reason = cloud_scan_worker.fresh_scan_skip_reason(
        config,
        "etf",
        now_utc=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
    )

    assert "fresh etf OK run 5m ago" in reason


def test_fresh_scan_skip_reason_respects_mode_and_failures(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    monkeypatch.setattr(
        cloud_scan_worker,
        "latest_runs",
        lambda config: [
            {
                "run_id": "fresh-failed-etf",
                "created_at": "2026-05-22T08:59:00+00:00",
                "mode": "etf",
                "status": "FAIL",
            },
            {
                "run_id": "fresh-stocks",
                "created_at": "2026-05-22T08:58:00+00:00",
                "mode": "stocks",
                "status": "OK",
            },
        ],
    )

    assert cloud_scan_worker.fresh_scan_skip_reason(
        config,
        "etf",
        now_utc=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
    ) is None
    assert "fresh stocks OK run 2m ago" in cloud_scan_worker.fresh_scan_skip_reason(
        config,
        "stocks",
        now_utc=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
    )


def test_fresh_scan_skip_reason_leaves_all_mode_unblocked(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    monkeypatch.setattr(
        cloud_scan_worker,
        "latest_runs",
        lambda config: [{
            "run_id": "fresh-all",
            "created_at": "2026-05-22T08:59:00+00:00",
            "mode": "all",
            "status": "OK",
        }],
    )

    assert cloud_scan_worker.fresh_scan_skip_reason(
        config,
        "all",
        now_utc=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
    ) is None
