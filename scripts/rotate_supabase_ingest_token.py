#!/usr/bin/env python3
"""Generate a scanner ingest token and print the SQL hash registration.

The raw token is written only to ~/.config/swing-terminal/cloud.env. The SQL
printed by this script contains only a SHA-256 hash, so it is safe to apply via
the Supabase connector.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
from pathlib import Path


CLOUD_ENV = Path.home() / ".config" / "swing-terminal" / "cloud.env"


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    seen = set()
    lines = []
    for raw in existing_lines:
        if "=" not in raw or raw.strip().startswith("#"):
            lines.append(raw)
            continue
        key, _value = raw.split("=", 1)
        key = key.strip()
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(raw)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate Supabase scanner ingest token")
    parser.add_argument("--key-id", default="render-primary")
    args = parser.parse_args()

    token = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    env = parse_env(CLOUD_ENV)
    env["SUPABASE_INGEST_TOKEN"] = token
    write_env(CLOUD_ENV, env)

    print(f"Wrote SUPABASE_INGEST_TOKEN to {CLOUD_ENV}")
    print("Apply this SQL via Supabase connector:")
    print()
    print(
        "insert into private.scanner_ingest_keys (key_id, token_sha256, active, rotated_at) "
        f"values ('{args.key_id}', '{token_hash}', true, now()) "
        "on conflict (key_id) do update set "
        "token_sha256 = excluded.token_sha256, active = true, rotated_at = now();"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
