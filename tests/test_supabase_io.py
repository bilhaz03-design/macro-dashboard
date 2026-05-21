import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import supabase_io


def test_config_from_env_accepts_service_role(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    config = supabase_io.config_from_env(required=True)

    assert config.url == "https://example.supabase.co"
    assert config.key == "secret"
    assert config.ingest_token == ""
    assert config.rest_url == "https://example.supabase.co/rest/v1"


def test_config_from_env_rejects_placeholders(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "PASTE_SUPABASE_SERVICE_ROLE_KEY_HERE")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_INGEST_TOKEN", raising=False)

    assert supabase_io.config_from_env(required=False) is None


def test_config_from_env_accepts_publishable_key_with_ingest_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_123")
    monkeypatch.setenv("SUPABASE_INGEST_TOKEN", "ingest-secret")

    config = supabase_io.config_from_env(required=True)

    assert config.key == "sb_publishable_123"
    assert config.ingest_token == "ingest-secret"


def test_upload_json_file_builds_artifact_row(monkeypatch, tmp_path):
    path = tmp_path / "latest-signals.json"
    path.write_text(json.dumps({"date": "2026-05-21", "signals": []}))
    captured = {}

    def fake_upsert(config, table, rows, on_conflict=None):
        captured["table"] = table
        captured["rows"] = rows
        captured["on_conflict"] = on_conflict
        return rows

    monkeypatch.setattr(supabase_io, "upsert_rows", fake_upsert)
    config = supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    assert supabase_io.upload_json_file(
        config,
        artifact_key="latest-signals",
        kind="latest_signals",
        path=path,
        scan_date="2026-05-21",
    ) is True

    assert captured["table"] == "scanner_artifacts"
    assert captured["on_conflict"] == "artifact_key"
    assert captured["rows"][0]["artifact_key"] == "latest-signals"
    assert captured["rows"][0]["payload"]["date"] == "2026-05-21"


def test_text_artifact_roundtrip_helpers(monkeypatch, tmp_path):
    uploaded = {}

    def fake_upload(config, **kwargs):
        uploaded.update(kwargs)

    def fake_download(config, artifact_key):
        assert artifact_key == "scan-data-js"
        return {"text": "window.SCAN_DATA = {};\n"}

    monkeypatch.setattr(supabase_io, "upload_json_artifact", fake_upload)
    monkeypatch.setattr(supabase_io, "download_json_artifact", fake_download)
    config = supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    supabase_io.upload_text_artifact(
        config,
        artifact_key="scan-data-js",
        kind="scan_data_js",
        text="window.SCAN_DATA = {};\n",
    )
    out = tmp_path / "scan_data.js"

    assert uploaded["payload"]["text"] == "window.SCAN_DATA = {};\n"
    assert supabase_io.restore_text_artifact(config, "scan-data-js", out) is True
    assert out.read_text() == "window.SCAN_DATA = {};\n"
