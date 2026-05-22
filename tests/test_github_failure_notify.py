import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import github_failure_notify


def _args(**overrides):
    values = {
        "workflow": "Swing Terminal Cloud Scanner",
        "job": "scan",
        "mode": "stocks",
        "run_url": "https://github.com/owner/repo/actions/runs/123",
        "repo": "owner/repo",
        "event": "schedule",
        "actor": "github-actions",
        "sha": "abcdef1234567890",
        "extra": "",
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_failure_message_contains_run_context():
    title, subtitle, body = github_failure_notify.build_failure_message(_args())

    assert title == "Swing Terminal Cloud Scanner FAILED"
    assert subtitle == "scan"
    assert "Mode: stocks" in body
    assert "SHA: abcdef123456" in body
    assert "https://github.com/owner/repo/actions/runs/123" in body


def test_notify_failure_dry_run_does_not_send(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(github_failure_notify, "push_telegram", lambda *parts: sent.append(parts))

    assert github_failure_notify.notify_failure(_args(dry_run=True)) == 0

    assert sent == []
    assert "Swing Terminal Cloud Scanner FAILED" in capsys.readouterr().out


def test_notify_failure_sends_telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(github_failure_notify, "push_telegram", lambda *parts: sent.append(parts))

    assert github_failure_notify.notify_failure(_args()) == 0

    assert sent[0][0] == "Swing Terminal Cloud Scanner FAILED"
    assert sent[0][1] == "scan"
    assert "Mode: stocks" in sent[0][2]
