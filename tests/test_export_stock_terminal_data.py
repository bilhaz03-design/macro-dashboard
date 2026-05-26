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


def test_current_signal_action_keeps_weak_or_untested_signals_alive_without_trade():
    weak_combo = {
        "tier": "NO_TRADE",
        "quality_score": 66,
        "mae_p10": -0.12,
    }

    assert stock_export.current_signal_action(weak_combo, [{"gate": "loose"}]) == "WAIT_SIGNAL"
    assert stock_export.current_signal_action(None, []) == "PAPER_TRACK"


def test_current_signal_action_blocks_only_hard_path_risk():
    blocked_combo = {
        "tier": "TIER1",
        "quality_score": 97,
        "mae_p10": -0.27,
    }

    assert stock_export.current_signal_action(blocked_combo, [{"gate": "strict"}]) == "BLOCKED"


def test_current_signal_action_uses_wait_95_for_near_miss():
    combo = {
        "tier": "TIER1",
        "quality_score": 91,
        "mae_p10": -0.10,
    }

    assert stock_export.current_signal_action(combo, [{"gate": "loose"}]) == "WAIT_95_SIGNAL"


def test_governance_explains_wait_signal_upgrade_path():
    row = {
        "action": "WAIT_SIGNAL",
        "quality_score": 72,
        "tier": "NO_TRADE",
        "blockers": ["quality<95", "no strict live gate"],
        "gates": [{"gate": "loose"}],
    }

    governance = stock_export.governance_for_signal(row)

    assert governance["status"] == "ALIVE_WAIT"
    assert "Q72" in governance["evidence"]
    assert "Needs Q >= 95 or a stronger exact setup" in governance["next"]
    assert "Needs strict/EOD confirmation" in governance["next"]


def test_trade_thesis_is_probabilistic_not_a_hard_filter():
    row = {
        "action": "WAIT_SIGNAL",
        "quality_score": 72,
        "tier": "NO_TRADE",
        "blockers": ["quality<95", "no strict live gate"],
        "gates": [{"gate": "loose"}],
        "signal": "PB126",
        "group": "China",
    }

    thesis = stock_export.trade_thesis_for_signal(row)

    assert thesis["stance"] == "Alive, but wait for confirmation"
    assert 35 <= thesis["subjective_probability"] <= 78
    assert "subjective" in thesis["probability_note"]
    assert thesis["bull_case"]
    assert thesis["bear_case"]
    assert "Price confirms" in thesis["buy_if"][0]


def test_stock_journal_tracks_lifecycle_upgrades(tmp_path):
    path = tmp_path / "stock-signal-journal.json"
    path.write_text(json.dumps({
        "signals": [{
            "key": "2026-05-22|KEYS|MLPB50",
            "date": "2026-05-22",
            "ticker": "KEYS",
            "signal": "MLPB50",
            "action": "PAPER_TRACK",
            "active": True,
            "first_seen_at": "2026-05-22T10:00:00",
            "seen_count": 1,
            "thesis": {"subjective_probability": 48},
        }]
    }), encoding="utf-8")
    row = {
        "date": "2026-05-22",
        "ticker": "KEYS",
        "name": "Keysight",
        "signal": "MLPB50",
        "action": "WAIT_SIGNAL",
        "entry": 100,
        "quality_score": 94,
        "tier": "NO_TRADE",
        "gates": [{"gate": "mlpb50"}],
        "thesis": {
            "stance": "Alive, but wait for confirmation",
            "subjective_probability": 59,
            "wait_for": ["Needs follow-through"],
            "buy_if": ["Price confirms"],
        },
    }

    memory = stock_export.update_stock_signal_journal([row], path=path)

    item = memory["today"][0]
    assert item["lifecycle"]["state"] == "UPGRADED"
    assert item["lifecycle"]["probability_delta"] == 11
    assert memory["upgraded_count"] == 1


def test_signal_outcome_reads_price_cache(tmp_path, monkeypatch):
    cache = tmp_path / "stock_cache"
    cache.mkdir()
    (tmp_path / "mlpb").mkdir()
    rows = ["Date,Open,High,Low,Close,Volume"]
    for i in range(25):
        close = 100 + i
        rows.append(f"2026-01-{i+1:02d},{close},{close+1},{close-1},{close},1000")
    (cache / "ABC.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(stock_export, "STOCK_CACHE_DIR", cache)
    monkeypatch.setattr(stock_export, "MLPB_CACHE_DIR", tmp_path / "mlpb")
    stock_export._PRICE_HISTORY_CACHE.clear()

    outcome = stock_export.signal_outcome("ABC", "2026-01-01", 100)

    assert outcome["status"] == "MATURED_21D"
    assert outcome["bars_elapsed"] == 24
    assert outcome["fwd5"] == 0.05
    assert outcome["fwd10"] == 0.10
    assert outcome["fwd21"] == 0.21


def test_post_entry_monitor_labels_open_winners():
    item = {
        "action": "WAIT_SIGNAL",
        "active": True,
        "outcome": {
            "status": "OPEN",
            "bars_elapsed": 8,
            "latest_return": 0.061,
        },
    }

    monitor = stock_export.post_entry_monitor(item)

    assert monitor["label"] == "Open monitor"
    assert monitor["tone"] == "positive"
    assert "6.1%" in monitor["note"]


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
    assert stock_export.stock_action({**best, "quality_score": 80}, current) == "WAIT_95_SIGNAL"


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
