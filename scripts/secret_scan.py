#!/usr/bin/env python3
"""Small repo-local secret scanner for high-risk token formats."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


PATTERNS = (
    SecretPattern("telegram_bot_token", re.compile(r"\b\d{7,12}:AA[A-Za-z0-9_-]{30,}\b")),
    SecretPattern("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}\b")),
    SecretPattern("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("supabase_or_jwt_secret", re.compile(r"\beyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    SecretPattern("cloudflare_token_assignment", re.compile(r"\bCLOUDFLARE_API_TOKEN\s*=\s*[\"']?[A-Za-z0-9_-]{30,}")),
)

SAFE_LINE_HINTS = (
    "${{ secrets.",
    "PASTE_",
    "_HERE",
    "example",
    "EXAMPLE",
    "fake",
    "Fake",
    "dummy",
    "token=\"token\"",
    'token="token"',
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    pattern: str
    preview: str


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("git ls-files failed")
    return [
        ROOT / item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def line_is_allowed(line: str) -> bool:
    return any(hint in line for hint in SAFE_LINE_HINTS)


def redact(text: str) -> str:
    text = text.strip()
    if len(text) <= 24:
        return text
    return f"{text[:12]}...{text[-6:]}"


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if line_is_allowed(line):
            continue
        for pattern in PATTERNS:
            for match in pattern.regex.finditer(line):
                findings.append(Finding(path, line_number, pattern.name, redact(match.group(0))))
    return findings


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(path, text))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan tracked files for high-risk token formats")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional explicit paths; defaults to git-tracked files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [path if path.is_absolute() else ROOT / path for path in args.paths] if args.paths else tracked_files()
    findings = scan_paths(paths)
    for finding in findings:
        rel = finding.path.relative_to(ROOT) if finding.path.is_relative_to(ROOT) else finding.path
        print(f"{rel}:{finding.line_number}: {finding.pattern}: {finding.preview}")
    if findings:
        print(f"secret_scan: {len(findings)} possible secret(s) found", file=sys.stderr)
        return 2
    print(f"secret_scan: ok ({len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
