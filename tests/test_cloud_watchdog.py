import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cloud_watchdog


def test_evaluate_health_tracks_etf_and_stocks_separately():
    now = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    rows = [
        {
            "run_id": "fresh-etf",
            "created_at": "2026-05-22T09:50:00+00:00",
            "mode": "etf",
            "status": "OK",
        },
        {
            "run_id": "old-stocks",
            "created_at": "2026-05-22T07:00:00+00:00",
            "mode": "stocks",
            "status": "OK",
        },
    ]

    checks = cloud_watchdog.evaluate_health(rows, now, [("etf", 50), ("stocks", 130)])

    assert checks[0]["mode"] == "etf"
    assert checks[0]["healthy"] is True
    assert checks[0]["age_minutes"] == 10
    assert checks[1]["mode"] == "stocks"
    assert checks[1]["healthy"] is False
    assert checks[1]["age_minutes"] == 180


def test_all_mode_satisfies_mode_specific_health():
    now = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    rows = [{
        "run_id": "fresh-all",
        "created_at": "2026-05-22T09:45:00+00:00",
        "mode": "all",
        "status": "OK",
    }]

    checks = cloud_watchdog.evaluate_health(rows, now, [("etf", 50), ("stocks", 130)])

    assert [check["healthy"] for check in checks] == [True, True]


def test_heal_dispatch_cooldown_is_per_mode():
    now = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    state = {
        "last_heal_dispatch_at": {
            "etf": "2026-05-22T09:30:00+00:00",
            "stocks": "2026-05-22T08:00:00+00:00",
        }
    }

    assert cloud_watchdog.should_dispatch_heal(state, "etf", now, 45) is False
    assert cloud_watchdog.should_dispatch_heal(state, "stocks", now, 45) is True


def test_heal_force_bypasses_manual_smoke_cooldown():
    now = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    state = {"last_heal_dispatch_at": {"stocks": "2026-05-22T09:59:00+00:00"}}

    assert cloud_watchdog.heal_dispatch_allowed(state, "stocks", now, 45) is False
    assert cloud_watchdog.heal_dispatch_allowed(state, "stocks", now, 45, force=True) is True


def test_parse_expected_mode_specs():
    assert cloud_watchdog.parse_expected_mode_specs(["etf:50", "stocks:130"]) == [
        ("etf", 50),
        ("stocks", 130),
    ]


def test_dispatch_github_scan_keeps_lifecycle_status_separate():
    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
        result = cloud_watchdog.dispatch_github_scan(
            mode="etf",
            repo="owner/repo",
            ref="main",
            token="token",
            no_notify=True,
        )

    assert result == {
        "mode": "etf",
        "workflow": "swing-terminal-cloud.yml",
        "http_status": 204,
        "force": False,
    }
    request = urlopen.call_args.args[0]
    assert request.headers["Authorization"] == "Bearer token"


def test_dispatch_github_scan_can_force_manual_self_heal():
    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
        result = cloud_watchdog.dispatch_github_scan(
            mode="stocks",
            repo="owner/repo",
            ref="main",
            token="token",
            no_notify=True,
            force=True,
        )

    assert result["force"] is True
    request = urlopen.call_args.args[0]
    body = request.data.decode("utf-8")
    assert '"mode": "stocks"' in body
    assert '"force": "true"' in body
    assert '"no_notify": "true"' in body
