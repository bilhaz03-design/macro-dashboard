import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cloudflare_backup_preflight as preflight


def test_build_status_reports_missing_required(monkeypatch):
    monkeypatch.setattr(preflight, "load_default_env", lambda: None)
    monkeypatch.setattr(preflight, "gh_secret_names", lambda repo: {
        "CF_SUPABASE_URL",
        "CF_SUPABASE_PUBLISHABLE_KEY",
        "CF_SUPABASE_INGEST_TOKEN",
    })
    monkeypatch.setenv("SUPABASE_URL", "set")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "set")
    monkeypatch.setenv("SUPABASE_INGEST_TOKEN", "set")
    monkeypatch.delenv("CLOUDFLARE_BACKUP_HEALTH_URL", raising=False)

    status = preflight.build_status("owner/repo")

    assert status["deploy_ready"] is False
    assert status["required_missing"] == [
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "CF_GITHUB_ACTIONS_TOKEN",
    ]
    assert status["health_url_configured"] is False


def test_build_status_ready_when_required_secrets_exist(monkeypatch):
    monkeypatch.setattr(preflight, "load_default_env", lambda: None)
    monkeypatch.setattr(preflight, "gh_secret_names", lambda repo: set(preflight.DEPLOY_REQUIRED_REPO_SECRETS))

    status = preflight.build_status("owner/repo")

    assert status["deploy_ready"] is True
    assert status["required_missing"] == []


def test_sync_supabase_repo_secrets_dry_run_does_not_call_gh(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "url")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "key")
    monkeypatch.setenv("SUPABASE_INGEST_TOKEN", "token")
    monkeypatch.setattr(
        preflight,
        "run_cmd",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gh secret set should not run")),
    )

    results = preflight.sync_supabase_repo_secrets("owner/repo", dry_run=True)

    assert [item["status"] for item in results] == ["would_set", "would_set", "would_set"]


def test_sync_supabase_repo_secrets_marks_missing_local(monkeypatch):
    for name in preflight.LOCAL_TO_REPO_SECRET:
        monkeypatch.delenv(name, raising=False)

    results = preflight.sync_supabase_repo_secrets("owner/repo", dry_run=False)

    assert [item["status"] for item in results] == ["missing_local", "missing_local", "missing_local"]
