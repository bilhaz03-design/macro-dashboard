import json
import math
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


def test_large_json_payload_compresses_and_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_COMPRESS_JSON_BYTES", "1")
    payload = {"items": [{"ticker": "ICGA", "score": 1.2, "bad": math.nan}], "path": tmp_path / "artifact.json"}

    stored = supabase_io.maybe_compress_json_payload(payload)

    assert stored[supabase_io.COMPRESSED_PAYLOAD_MARKER] == supabase_io.COMPRESSED_JSON_ENCODING
    assert stored["raw_bytes"] > 0
    assert stored["compressed_bytes"] > 0
    assert supabase_io.decode_json_payload(stored) == {
        "items": [{"ticker": "ICGA", "score": 1.2, "bad": None}],
        "path": str(tmp_path / "artifact.json"),
    }


def test_download_json_artifact_decodes_compressed_payload(monkeypatch):
    monkeypatch.setenv("SUPABASE_COMPRESS_JSON_BYTES", "1")
    compressed = supabase_io.maybe_compress_json_payload({"signals": [{"ticker": "EIDO"}]})
    config = supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    monkeypatch.setattr(
        supabase_io,
        "select_rows",
        lambda config, table, filters, select="*", limit=None: [{"payload": compressed}],
    )

    assert supabase_io.download_json_artifact(config, "latest-signals") == {"signals": [{"ticker": "EIDO"}]}


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


def test_request_json_sanitizes_nonfinite_numbers(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=30):
        captured["body"] = req.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setattr(supabase_io.urllib.request, "urlopen", fake_urlopen)
    config = supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    supabase_io.request_json(
        config,
        "POST",
        "scanner_artifacts",
        body={"payload": {"qt_z": math.nan, "ok": 1.0, "bad": math.inf}},
    )

    assert json.loads(captured["body"]) == {"payload": {"qt_z": None, "ok": 1.0, "bad": None}}
    assert "NaN" not in captured["body"]
    assert "Infinity" not in captured["body"]


def test_request_json_retries_transient_timeout(monkeypatch):
    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'[{"ok": true}]'

    def fake_urlopen(req, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("slow")
        return FakeResponse()

    monkeypatch.setenv("SUPABASE_HTTP_ATTEMPTS", "2")
    monkeypatch.setattr(supabase_io.time_module, "sleep", lambda seconds: None)
    monkeypatch.setattr(supabase_io.urllib.request, "urlopen", fake_urlopen)
    config = supabase_io.SupabaseConfig(url="https://example.supabase.co", key="secret")

    assert supabase_io.request_json(config, "GET", "scanner_artifacts") == [{"ok": True}]
    assert calls["count"] == 2
