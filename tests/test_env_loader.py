import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import env_loader


def test_load_env_file_handles_quotes_and_preserves_existing(monkeypatch, tmp_path):
    path = tmp_path / "cloud.env"
    path.write_text("""
# comment
SUPABASE_URL="https://example.supabase.co"
TELEGRAM_CHAT_ID='12345'
EMPTY=
""")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "existing")

    loaded = env_loader.load_env_file(path)

    assert "SUPABASE_URL" in loaded
    assert os.environ["SUPABASE_URL"] == "https://example.supabase.co"
    assert os.environ["TELEGRAM_CHAT_ID"] == "existing"
    assert os.environ["EMPTY"] == ""


def test_load_env_file_override(monkeypatch, tmp_path):
    path = tmp_path / "alerts.env"
    path.write_text("TELEGRAM_CHAT_ID=12345\n")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "existing")

    env_loader.load_env_file(path, override=True)

    assert os.environ["TELEGRAM_CHAT_ID"] == "12345"
