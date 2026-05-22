import sys
from pathlib import Path

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


def test_format_minutes_is_compact():
    assert cloud_chain_status.format_minutes(None) == "unknown"
    assert cloud_chain_status.format_minutes(4) == "4m"
    assert cloud_chain_status.format_minutes(130) == "2h10m"
