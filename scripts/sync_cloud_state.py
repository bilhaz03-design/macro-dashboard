#!/usr/bin/env python3
"""Pull latest cloud scanner state from Supabase into the local terminal files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(ROOT / "scripts"))

import supabase_io  # noqa: E402
from env_loader import load_default_env  # noqa: E402


JSON_ARTIFACTS = {
    "latest-signals": DATA_DIR / "latest-signals.json",
    "signal-journal": DATA_DIR / "signal-journal.json",
    "stock-signal-journal": DATA_DIR / "stock-signal-journal.json",
    "signal-notify-state": DATA_DIR / "signal-notify-state.json",
    "live-trades": DATA_DIR / "live-trades.json",
    "execution-map": DATA_DIR / "execution_map.json",
    "stock-current-coverage": DATA_DIR / "stock_framework_current_scan_coverage.json",
}

TEXT_ARTIFACTS = {
    "scan-data-js": ROOT / "dashboard" / "scan_data.js",
    "stock-data-js": ROOT / "dashboard" / "stock_data.js",
}


def main() -> int:
    load_default_env()
    config = supabase_io.config_from_env(required=True)
    restored = []
    for key, path in JSON_ARTIFACTS.items():
        if supabase_io.restore_json_artifact(config, key, path):
            restored.append(key)
    for key, path in TEXT_ARTIFACTS.items():
        if supabase_io.restore_text_artifact(config, key, path):
            restored.append(key)
    print(f"Restored from Supabase: {', '.join(restored) if restored else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
