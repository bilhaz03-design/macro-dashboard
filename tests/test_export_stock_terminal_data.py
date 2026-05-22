import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import export_stock_terminal_data as stock_export


def test_current_signal_action_is_binary_trade_when_strict_and_validated():
    combo = {
        "tier": "TIER1",
        "quality_score": 96,
        "mae_p10": -0.10,
    }
    gates = [{"gate": "strict"}]

    assert stock_export.current_signal_action(combo, gates) == "TRADE"
    assert stock_export.current_signal_blockers(combo, gates) == []


def test_current_signal_action_blocks_weak_or_untested_signals():
    weak_combo = {
        "tier": "NO_TRADE",
        "quality_score": 41,
        "mae_p10": -0.12,
    }

    assert stock_export.current_signal_action(weak_combo, []) == "NO_TRADE"
    assert stock_export.current_signal_action(None, []) == "NO_TRADE"


def test_stock_action_returns_only_trade_or_no_trade():
    best = {
        "tier": "TIER1",
        "quality_score": 96,
        "mae_p10": -0.10,
    }
    current = [{
        "action": "TRADE",
        "gates": [{"gate": "strict"}],
    }]

    assert stock_export.stock_action(best, current) == "TRADE"
    assert stock_export.stock_action({**best, "quality_score": 80}, current) == "NO_TRADE"


def test_journal_normalizes_legacy_stock_actions(tmp_path):
    path = tmp_path / "stock-signal-journal.json"
    path.write_text(json.dumps({
        "signals": [{
            "key": "2026-05-21|BILI|PB126",
            "date": "2026-05-21",
            "ticker": "BILI",
            "action": "RESEARCH_ONLY",
            "tier": "RESEARCH_ONLY",
            "active": False,
        }]
    }), encoding="utf-8")

    stock_export.update_stock_signal_journal([], path=path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert {item.get("action") for item in payload["signals"]} == {"NO_TRADE"}
    assert {item.get("tier") for item in payload["signals"]} == {"NO_TRADE"}
