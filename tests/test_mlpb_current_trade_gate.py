import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import mlpb_current_trade_gate
from mlpb_current_trade_gate import coerce_earnings_date, ensure_latest_caches, latest_gate_date, score


def test_latest_gate_date_prefers_latest_price_date_over_signal_date():
    current = pd.DataFrame({"date": ["2026-05-21", "2026-05-22"]})
    latest_rows = [{"latest_date": "2026-05-25"}, {"latest_date": "2026-05-24"}]

    assert latest_gate_date(current, latest_rows) == "2026-05-25"


def test_latest_gate_date_falls_back_to_signal_date_without_price_data():
    current = pd.DataFrame({"date": ["2026-05-21", "2026-05-22"]})

    assert latest_gate_date(current, [{}, {"latest_date": None}]) == "2026-05-22"


def test_coerce_earnings_date_handles_common_yfinance_shapes():
    assert coerce_earnings_date({"Earnings Date": [pd.Timestamp("2026-08-18")]}) == "2026-08-18"
    assert coerce_earnings_date(pd.Series([pd.NaT, "2026-06-04"])) == "2026-06-04"

    calendar = pd.DataFrame({"Value": [pd.Timestamp("2026-07-31")]}, index=["Earnings Date"])
    assert coerce_earnings_date(calendar) == "2026-07-31"


def test_score_blocks_missing_latest_price_features():
    cand = pd.Series({
        "variant": "STRICT",
        "setup": "MLPB50",
        "market_regime": "RISK_ON",
        "close": 100.0,
        "date": "2026-05-22",
    })
    hist = {"n": 100, "nextopen21_winsor": 0.08, "nextopen21_hit": 0.60}

    score_val, label, warnings, blocks = score(cand, hist, "test", {}, "2026-05-26", None)

    assert score_val < 80
    assert label == "NO_TRADE"
    assert "Latest price features missing." in blocks


def test_ensure_latest_caches_fetches_missing_ticker(monkeypatch, tmp_path):
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    raw = pd.DataFrame(
        {
            "Open": np.linspace(90, 110, len(dates)),
            "High": np.linspace(91, 111, len(dates)),
            "Low": np.linspace(89, 109, len(dates)),
            "Close": np.linspace(90, 110, len(dates)),
            "Volume": np.full(len(dates), 1_000_000),
        },
        index=dates,
    )

    monkeypatch.setattr(mlpb_current_trade_gate, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mlpb_current_trade_gate.yf, "download", lambda *args, **kwargs: raw)

    stats = ensure_latest_caches(["TEST"])

    assert stats["requested"] == 1
    assert stats["available"] == 1
    assert stats["fetched"] == ["TEST"]
    assert (tmp_path / "TEST.csv").exists()
