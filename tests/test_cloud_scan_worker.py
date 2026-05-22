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


def test_stock_coverage_quality_accepts_high_coverage(monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_STOCK_MIN_OK_RATIO", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_STOCK_MAX_FAIL_RATIO", raising=False)

    assert cloud_scan_worker.stock_coverage_quality_error({
        "stocks": 108,
        "ok": 103,
        "fail": 5,
        "current": 12,
    }) is None


def test_stock_coverage_quality_rejects_low_ok_count(monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_STOCK_MIN_OK_RATIO", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_STOCK_MAX_FAIL_RATIO", raising=False)

    error = cloud_scan_worker.stock_coverage_quality_error({
        "stocks": 108,
        "ok": 102,
        "fail": 6,
        "current": 12,
    })

    assert "below min_ok=103" in error


def test_stock_coverage_quality_respects_env_thresholds(monkeypatch):
    monkeypatch.setenv("SWING_TERMINAL_STOCK_MIN_OK_RATIO", "0.90")
    monkeypatch.setenv("SWING_TERMINAL_STOCK_MAX_FAIL_RATIO", "0.10")

    assert cloud_scan_worker.stock_coverage_quality_error({
        "stocks": 100,
        "ok": 90,
        "fail": 10,
    }) is None
    assert "above max_fail=10" in cloud_scan_worker.stock_coverage_quality_error({
        "stocks": 100,
        "ok": 90,
        "fail": 11,
    })


def test_validate_stock_coverage_returns_failure_for_bad_payload(tmp_path):
    path = tmp_path / "coverage.json"
    path.write_text('{"stocks": 108, "ok": 80, "fail": 28}', encoding="utf-8")

    assert cloud_scan_worker.validate_stock_coverage(path) == cloud_scan_worker.STOCK_COVERAGE_FAIL_EXIT_CODE


def test_etf_coverage_quality_accepts_normal_scan(monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_ETF_MIN_SCANNED", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_ETF_MAX_ERROR_RATIO", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_ETF_MAX_SKIP_RATIO", raising=False)

    assert cloud_scan_worker.etf_coverage_quality_error({
        "total_scanned": 44,
        "err_count": 4,
        "skip_count": 8,
        "cap_count": 0,
        "pb_count": 1,
        "pb126_count": 0,
    }) is None


def test_etf_coverage_quality_rejects_too_few_scanned(monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_ETF_MIN_SCANNED", raising=False)

    error = cloud_scan_worker.etf_coverage_quality_error({
        "total_scanned": 39,
        "err_count": 0,
        "skip_count": 0,
    })

    assert "below min_total=40" in error


def test_etf_coverage_quality_rejects_error_and_skip_spikes(monkeypatch):
    monkeypatch.setenv("SWING_TERMINAL_ETF_MIN_SCANNED", "20")
    monkeypatch.setenv("SWING_TERMINAL_ETF_MAX_ERROR_RATIO", "0.10")
    monkeypatch.setenv("SWING_TERMINAL_ETF_MAX_SKIP_RATIO", "0.20")

    assert "above max_errors=4" in cloud_scan_worker.etf_coverage_quality_error({
        "total_scanned": 44,
        "err_count": 5,
        "skip_count": 0,
    })
    assert "above max_skips=8" in cloud_scan_worker.etf_coverage_quality_error({
        "total_scanned": 44,
        "err_count": 0,
        "skip_count": 9,
    })


def test_validate_etf_coverage_returns_failure_for_bad_payload(tmp_path):
    path = tmp_path / "latest-signals.json"
    path.write_text('{"total_scanned": 44, "err_count": 20, "skip_count": 0}', encoding="utf-8")

    assert cloud_scan_worker.validate_etf_coverage(path) == cloud_scan_worker.ETF_COVERAGE_FAIL_EXIT_CODE


def test_publish_run_summary_writes_only_scan_runs(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    calls = []

    def fake_upsert(config, table, rows, *, on_conflict=None):
        calls.append((table, rows, on_conflict))
        return rows

    monkeypatch.setattr(cloud_scan_worker.supabase_io, "upsert_rows", fake_upsert)

    summary = {"run_id": "failed-quality-gate", "status": "FAIL", "exit_code": 5}
    cloud_scan_worker.publish_run_summary(config, summary)

    assert calls == [("scan_runs", [summary], "run_id")]


def test_publish_cloud_result_keeps_artifacts_on_failure(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    calls = []

    monkeypatch.setattr(cloud_scan_worker, "publish_state", lambda config, summary: calls.append("state"))
    monkeypatch.setattr(cloud_scan_worker, "publish_run_summary", lambda config, summary: calls.append("summary"))

    message = cloud_scan_worker.publish_cloud_result(
        config,
        {"run_id": "failed-quality-gate", "status": "FAIL", "exit_code": 5},
    )

    assert calls == ["summary"]
    assert "without replacing scanner artifacts" in message


def test_publish_cloud_result_publishes_artifacts_on_success(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    calls = []

    monkeypatch.setattr(cloud_scan_worker, "publish_state", lambda config, summary: calls.append("state"))
    monkeypatch.setattr(cloud_scan_worker, "publish_run_summary", lambda config, summary: calls.append("summary"))

    message = cloud_scan_worker.publish_cloud_result(
        config,
        {"run_id": "ok-run", "status": "OK", "exit_code": 0},
    )

    assert calls == ["state", "summary"]
    assert message == "published state to Supabase"
