#!/usr/bin/env python3
"""Load local swing-terminal env files without pulling in python-dotenv."""

from __future__ import annotations

import os
import shlex
from pathlib import Path


DEFAULT_ENV_FILES = (
    Path.home() / ".config" / "swing-terminal" / "cloud.env",
    Path.home() / ".config" / "swing-terminal" / "alerts.env",
)


def load_env_file(path: Path, *, override: bool = False) -> list[str]:
    loaded: list[str] = []
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if not key:
            continue
        try:
            parsed = shlex.split(value.strip(), comments=False, posix=True)
            value = parsed[0] if parsed else ""
        except ValueError:
            value = value.strip().strip("\"'")
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def load_default_env(*, override: bool = False) -> list[str]:
    loaded: list[str] = []
    for path in DEFAULT_ENV_FILES:
        loaded.extend(load_env_file(path, override=override))
    return loaded
