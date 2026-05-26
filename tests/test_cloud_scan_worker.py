import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
    mlpb_path = tmp_path / "mlpb-current-gate.json"
    mlpb_path.write_text('{"generated_at": "2026-05-24T23:25:41", "candidates": []}')

    assert cloud_scan_worker.artifact_scan_date("latest-signals", latest_path, "2026-05-23") == "2026-05-20"
    assert cloud_scan_worker.artifact_scan_date("stock-signal-journal", stock_path, "2026-05-23") == "2026-05-21"
    assert cloud_scan_worker.artifact_scan_date("stock-current-coverage", coverage_path, "2026-05-23") == "2026-05-22"
    assert cloud_scan_worker.artifact_scan_date("mlpb-current-gate", mlpb_path, "2026-05-23") == "2026-05-24"


def test_should_refresh_mlpb_events_for_missing_and_stale_files(tmp_path, monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_MLPB_REFRESH_HOURS", raising=False)
    missing_path = tmp_path / "missing.json"

    refresh, reason = cloud_scan_worker.should_refresh_mlpb_events(
        missing_path,
        now_utc=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )
    assert refresh is True
    assert "missing" in reason

    stale_path = tmp_path / "events.json"
    stale_path.write_text('{"events": [{"visual_grade": "CLEAN", "qt_label": "QT_SUPPORT", "qt_phase": "REPAIRING", "qt_wait_label": "LOW_WAIT_VALUE"}]}', encoding="utf-8")
    old_ts = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc).timestamp()
    os.utime(stale_path, (old_ts, old_ts))

    refresh, reason = cloud_scan_worker.should_refresh_mlpb_events(
        stale_path,
        now_utc=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )
    assert refresh is True
    assert "stale" in reason


def test_should_refresh_mlpb_events_for_stale_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("SWING_TERMINAL_MLPB_REFRESH_HOURS", "18")
    path = tmp_path / "events.json"
    path.write_text('{"events": [{"ticker": "KEYS"}]}', encoding="utf-8")
    modified_ts = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (modified_ts, modified_ts))

    refresh, reason = cloud_scan_worker.should_refresh_mlpb_events(
        path,
        now_utc=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert refresh is True
    assert "missing Prime fields" in reason


def test_should_use_cached_mlpb_events_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SWING_TERMINAL_MLPB_REFRESH_HOURS", "18")
    path = tmp_path / "events.json"
    path.write_text('{"events": [{"visual_grade": "CLEAN", "qt_label": "QT_SUPPORT", "qt_phase": "REPAIRING", "qt_wait_label": "LOW_WAIT_VALUE"}]}', encoding="utf-8")
    modified_ts = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (modified_ts, modified_ts))

    refresh, reason = cloud_scan_worker.should_refresh_mlpb_events(
        path,
        now_utc=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert refresh is False
    assert "fresh" in reason


def test_run_mlpb_stock_gate_skips_when_optional_scripts_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cloud_scan_worker, "MLPB_GATE_SCRIPT", tmp_path / "missing_gate.py")
    monkeypatch.delenv("SWING_TERMINAL_REQUIRE_MLPB_GATE", raising=False)
    monkeypatch.setattr(cloud_scan_worker, "run", lambda cmd: (_ for _ in ()).throw(AssertionError("run should not be called")))

    assert cloud_scan_worker.run_mlpb_stock_gate() == 0
    assert "optional MLPB gate skipped" in capsys.readouterr().out


def test_run_mlpb_stock_gate_skips_missing_events_in_cloud_without_failing(monkeypatch, tmp_path, capsys):
    gate = tmp_path / "mlpb_current_trade_gate.py"
    gate.write_text("print('gate')\n", encoding="utf-8")

    monkeypatch.setattr(cloud_scan_worker, "MLPB_GATE_SCRIPT", gate)
    monkeypatch.setattr(cloud_scan_worker, "MLPB_EVENTS_PATH", tmp_path / "missing_events.json")
    monkeypatch.setenv("SWING_TERMINAL_CLOUD_RUN", "1")
    monkeypatch.delenv("SWING_TERMINAL_RUN_MLPB_RESEARCH", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_REQUIRE_MLPB_GATE", raising=False)
    monkeypatch.setattr(cloud_scan_worker, "run", lambda cmd: (_ for _ in ()).throw(AssertionError("run should not be called")))

    assert cloud_scan_worker.run_mlpb_stock_gate() == 0
    captured = capsys.readouterr()
    assert "MLPB event refresh skipped" in captured.out
    assert "MLPB gate skipped: missing final event set" in captured.err


def test_run_mlpb_stock_gate_can_require_cached_events(monkeypatch, tmp_path):
    gate = tmp_path / "mlpb_current_trade_gate.py"
    gate.write_text("print('gate')\n", encoding="utf-8")

    monkeypatch.setattr(cloud_scan_worker, "MLPB_GATE_SCRIPT", gate)
    monkeypatch.setattr(cloud_scan_worker, "MLPB_EVENTS_PATH", tmp_path / "missing_events.json")
    monkeypatch.setenv("SWING_TERMINAL_CLOUD_RUN", "1")
    monkeypatch.setenv("SWING_TERMINAL_REQUIRE_MLPB_GATE", "1")
    monkeypatch.setattr(cloud_scan_worker, "run", lambda cmd: 0)

    assert cloud_scan_worker.run_mlpb_stock_gate() == 1


def test_run_mlpb_stock_gate_runs_current_gate_when_cached_events_exist(monkeypatch, tmp_path):
    gate = tmp_path / "mlpb_current_trade_gate.py"
    gate.write_text("print('gate')\n", encoding="utf-8")
    events = tmp_path / "mlpb_final_falsification_events.json"
    events.write_text(
        '{"events": [{"visual_grade": "CLEAN", "qt_label": "QT_SUPPORT", "qt_phase": "REPAIRING", "qt_wait_label": "LOW_WAIT_VALUE"}]}',
        encoding="utf-8",
    )
    now_ts = datetime.now(timezone.utc).timestamp()
    os.utime(events, (now_ts, now_ts))
    calls = []

    monkeypatch.setattr(cloud_scan_worker, "MLPB_GATE_SCRIPT", gate)
    monkeypatch.setattr(cloud_scan_worker, "MLPB_EVENTS_PATH", events)
    monkeypatch.setenv("SWING_TERMINAL_CLOUD_RUN", "1")
    monkeypatch.delenv("SWING_TERMINAL_RUN_MLPB_RESEARCH", raising=False)
    monkeypatch.setattr(cloud_scan_worker, "run", lambda cmd: calls.append(cmd) or 0)

    assert cloud_scan_worker.run_mlpb_stock_gate() == 0
    assert calls == [[sys.executable, str(gate)]]


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


def test_daily_dry_run_safety_requires_no_upload_and_no_notify():
    args = SimpleNamespace(daily_dry_run=True, no_upload=False, no_notify=False)

    error = cloud_scan_worker.dry_run_safety_error(args)

    assert "--no-upload" in error
    assert "--no-notify" in error


def test_daily_dry_run_safety_allows_no_mutation_smoke():
    args = SimpleNamespace(daily_dry_run=True, no_upload=True, no_notify=True)

    assert cloud_scan_worker.dry_run_safety_error(args) is None


def test_summarize_run_records_quality_failure_reason(monkeypatch):
    monkeypatch.delenv("SWING_TERMINAL_ETF_MIN_SCANNED", raising=False)
    monkeypatch.delenv("SWING_TERMINAL_ETF_MAX_ERROR_RATIO", raising=False)

    def fake_load_json(path, default=None):
        if path == cloud_scan_worker.ETF_SIGNALS_PATH:
            return {
                "date": "2026-05-22",
                "total_scanned": 44,
                "err_count": 20,
                "skip_count": 0,
            }
        if path == cloud_scan_worker.STOCK_COVERAGE_PATH:
            return {"stocks": 108, "ok": 108, "fail": 0, "current": 90}
        return {"scan_date": "2026-05-22", "signals": []}

    monkeypatch.setattr(cloud_scan_worker, "load_json", fake_load_json)

    summary = cloud_scan_worker.summarize_run(
        run_id="failed-etf-gate",
        mode="etf",
        status="FAIL",
        exit_code=cloud_scan_worker.ETF_COVERAGE_FAIL_EXIT_CODE,
    )

    failure = summary["payload"]["failure"]
    assert failure["kind"] == "etf_coverage_quality_gate"
    assert failure["etf_coverage_error"] == "err_count=20 above max_errors=4 (10% of 44)"


def test_summarize_run_counts_all_mode_etfs_and_stocks(monkeypatch):
    def fake_load_json(path, default=None):
        if path == cloud_scan_worker.ETF_SIGNALS_PATH:
            return {
                "date": "2026-05-22",
                "total_scanned": 44,
                "err_count": 1,
                "skip_count": 0,
            }
        if path == cloud_scan_worker.STOCK_COVERAGE_PATH:
            return {"stocks": 108, "ok": 106, "fail": 2, "current": 90}
        return {"scan_date": "2026-05-22", "signals": []}

    monkeypatch.setattr(cloud_scan_worker, "load_json", fake_load_json)

    summary = cloud_scan_worker.summarize_run(
        run_id="all-run",
        mode="all",
        status="OK",
        exit_code=0,
    )

    assert summary["total_scanned"] == 152
    assert summary["error_count"] == 3
    assert summary["payload"]["stock_current_coverage"]["stocks"] == 108


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


def test_publish_cloud_result_records_test_alert_without_replacing_artifacts(monkeypatch):
    config = cloud_scan_worker.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    calls = []

    monkeypatch.setattr(cloud_scan_worker, "publish_state", lambda config, summary: calls.append("state"))
    monkeypatch.setattr(cloud_scan_worker, "publish_run_summary", lambda config, summary: calls.append("summary"))

    message = cloud_scan_worker.publish_cloud_result(
        config,
        {"run_id": "test-alert", "mode": "test-alert", "status": "OK", "exit_code": 0},
    )

    assert calls == ["summary"]
    assert "test-alert" in message


def test_publish_keys_are_mode_specific():
    assert "latest-signals" in cloud_scan_worker.publish_keys_for_mode("etf")
    assert "scan-data-js" in cloud_scan_worker.publish_keys_for_mode("etf")
    assert "stock-current-coverage" not in cloud_scan_worker.publish_keys_for_mode("etf")
    assert "mlpb-final-events" not in cloud_scan_worker.publish_keys_for_mode("etf")

    assert "stock-current-coverage" in cloud_scan_worker.publish_keys_for_mode("stocks")
    assert "stock-data-js" in cloud_scan_worker.publish_keys_for_mode("stocks")
    assert "latest-signals" not in cloud_scan_worker.publish_keys_for_mode("stocks")
    assert cloud_scan_worker.publish_keys_for_mode("test-alert") == set()


def test_test_alert_summary_does_not_reuse_stale_scan_counts():
    assert cloud_scan_worker.run_total_scanned("test-alert", {"total_scanned": 44}, {"stocks": 108}) is None
    assert cloud_scan_worker.run_error_count("test-alert", {"err_count": 0}, {"fail": 0}) is None


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
