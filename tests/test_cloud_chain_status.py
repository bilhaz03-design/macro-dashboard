import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cloud_chain_status


def test_action_required_ignores_stale_heartbeat_outside_watch_window():
    status = {
        "watch_window_open": False,
        "supabase": {
            "ok": True,
            "checks": [{"mode": "etf", "healthy": False}],
        },
        "github": {},
    }

    assert cloud_chain_status.has_action_required(status) is False


def test_action_required_flags_stale_heartbeat_inside_watch_window():
    status = {
        "watch_window_open": True,
        "supabase": {
            "ok": True,
            "checks": [{"mode": "stocks", "healthy": False}],
        },
        "github": {},
    }

    assert cloud_chain_status.has_action_required(status) is True


def test_action_required_uses_watchdog_window_not_scanner_window():
    status = {
        "scan_window_open": True,
        "watchdog_window_open": False,
        "supabase": {
            "ok": True,
            "checks": [{"mode": "stocks", "healthy": False}],
        },
        "github": {},
    }

    assert cloud_chain_status.has_action_required(status) is False


def test_action_required_flags_failed_core_workflow():
    status = {
        "watch_window_open": False,
        "supabase": {"ok": True, "checks": []},
        "github": {
            "Swing Terminal Cloud Scanner": {
                "ok": True,
                "runs": [{"conclusion": "failure"}],
            }
        },
    }

    assert cloud_chain_status.has_action_required(status) is True


def test_action_required_flags_configured_bad_cloudflare_health():
    status = {
        "watch_window_open": False,
        "supabase": {"ok": True, "checks": []},
        "github": {},
        "cloudflare_health": {"configured": True, "ok": False},
    }

    assert cloud_chain_status.has_action_required(status) is True


def test_action_required_ignores_unconfigured_cloudflare_health():
    status = {
        "watch_window_open": False,
        "supabase": {"ok": True, "checks": []},
        "github": {},
        "cloudflare_health": {"configured": False, "ok": False},
    }

    assert cloud_chain_status.has_action_required(status) is False


def test_action_required_flags_latest_failed_supabase_run():
    status = {
        "watch_window_open": False,
        "supabase": {
            "ok": True,
            "runs": [{"status": "FAIL", "exit_code": 5}],
            "checks": [],
        },
        "github": {},
    }

    assert cloud_chain_status.has_action_required(status) is True


def test_latest_failure_text_prefers_quality_gate_detail():
    assert cloud_chain_status.latest_failure_text({
        "payload": {
            "failure": {
                "kind": "etf_coverage_quality_gate",
                "etf_coverage_error": "err_count=5 above max_errors=4",
            },
        },
    }) == "etf_coverage_quality_gate: err_count=5 above max_errors=4"


def test_load_latest_issue_skips_ok_run(monkeypatch):
    config = cloud_chain_status.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")
    monkeypatch.setattr(
        cloud_chain_status.supabase_io,
        "request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected query")),
    )

    assert cloud_chain_status.load_latest_issue(config, [{"run_id": "ok", "status": "OK"}]) is None


def test_load_latest_issue_fetches_failed_payload(monkeypatch):
    config = cloud_chain_status.supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    def fake_request_json(config, method, path, *, query=None):
        assert method == "GET"
        assert path == "scan_runs"
        assert query["run_id"] == "eq.failed"
        return [{"run_id": "failed", "status": "FAIL", "payload": {"failure": {"kind": "scanner_command_failure"}}}]

    monkeypatch.setattr(cloud_chain_status.supabase_io, "request_json", fake_request_json)

    issue = cloud_chain_status.load_latest_issue(config, [{"run_id": "failed", "status": "FAIL"}])

    assert issue["payload"]["failure"]["kind"] == "scanner_command_failure"


def test_format_minutes_is_compact():
    assert cloud_chain_status.format_minutes(None) == "unknown"
    assert cloud_chain_status.format_minutes(4) == "4m"
    assert cloud_chain_status.format_minutes(130) == "2h10m"


def test_load_cloudflare_health_not_configured():
    assert cloud_chain_status.load_cloudflare_health("") == {
        "configured": False,
        "ok": False,
        "detail": "not configured",
    }


def test_load_cloudflare_health_ready():
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"ok": true, "ready": true, "missingRequiredEnv": []}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    status = cloud_chain_status.load_cloudflare_health("https://worker.example/health", lambda *args, **kwargs: response)

    assert status["configured"] is True
    assert status["ok"] is True
    assert status["ready"] is True
    assert status["missing"] == []


def test_load_cloudflare_health_not_ready():
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"ok": true, "ready": false, "missingRequiredEnv": ["GITHUB_ACTIONS_TOKEN"]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    status = cloud_chain_status.load_cloudflare_health("https://worker.example/health", lambda *args, **kwargs: response)

    assert status["configured"] is True
    assert status["ok"] is False
    assert status["missing"] == ["GITHUB_ACTIONS_TOKEN"]


def test_build_status_includes_notification_health(monkeypatch):
    monkeypatch.setattr(cloud_chain_status, "load_default_env", lambda: None)
    monkeypatch.setattr(cloud_chain_status, "load_supabase_status", lambda: {"ok": True, "runs": [], "checks": []})
    monkeypatch.setattr(cloud_chain_status, "load_github_status", lambda repo, limit: {})
    monkeypatch.setattr(cloud_chain_status, "load_cloudflare_health", lambda: {"configured": False, "ok": False})
    monkeypatch.setattr(
        cloud_chain_status.run_scan_notify,
        "notification_health",
        lambda: {"cloud_ready": True, "channel": "telegram"},
    )

    status = cloud_chain_status.build_status("owner/repo", 1)

    assert status["notification"]["cloud_ready"] is True
    assert status["notification"]["channel"] == "telegram"
