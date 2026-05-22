import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import secret_scan


def test_secret_scan_detects_telegram_token(tmp_path):
    path = tmp_path / "leak.txt"
    token = "123456789:" + "AA" + "abcdefghijklmnopqrstuvwxyzABCDE"
    path.write_text(f"TELEGRAM_BOT_TOKEN={token}\n")

    findings = secret_scan.scan_paths([path])

    assert len(findings) == 1
    assert findings[0].pattern == "telegram_bot_token"


def test_secret_scan_allows_placeholders_and_github_secret_expressions(tmp_path):
    path = tmp_path / "workflow.yml"
    path.write_text(
        "\n".join([
            "TELEGRAM_BOT_TOKEN=PASTE_TELEGRAM_BOT_TOKEN_HERE",
            "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
        ])
    )

    assert secret_scan.scan_paths([path]) == []


def test_secret_scan_detects_private_key_marker(tmp_path):
    path = tmp_path / "id_rsa"
    marker = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"
    path.write_text(f"{marker}\nnot-real\n")

    findings = secret_scan.scan_paths([path])

    assert len(findings) == 1
    assert findings[0].pattern == "private_key"
