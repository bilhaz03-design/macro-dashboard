import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import stock_framework_deep_research as stocks


def test_cache_path_keeps_period_specific_current_scans_separate():
    assert stocks.cache_path("AAPL").name == "AAPL.csv"
    assert stocks.cache_path("AAPL", "8y").name == "AAPL.8y.csv"


def test_extract_batch_ticker_handles_ticker_first_multiindex():
    index = pd.date_range("2026-01-01", periods=2)
    columns = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]])
    raw = pd.DataFrame(1.0, index=index, columns=columns)

    frame = stocks.extract_batch_ticker(raw, "MSFT")

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_normalize_ohlcv_rejects_missing_required_columns():
    raw = pd.DataFrame({"Close": [10, 11]}, index=pd.date_range("2026-01-01", periods=2))

    assert stocks.normalize_ohlcv(raw) is None
