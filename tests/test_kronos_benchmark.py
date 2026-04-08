import os
import sys
import pytest
import numpy as np
import pandas as pd

# Add scripts/ to path so we can import without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


# ── Task 1: _setup_kronos_path ────────────────────────────────────────────────

def test_setup_kronos_path_valid(tmp_path, monkeypatch):
    """Valid path should insert into sys.path and return the path."""
    from kronos_fx_benchmark import _setup_kronos_path
    fake_repo = tmp_path / "Kronos"
    fake_repo.mkdir()
    monkeypatch.delenv("KRONOS_PATH", raising=False)
    result = _setup_kronos_path(default=str(fake_repo))
    assert str(fake_repo) in sys.path
    assert result == str(fake_repo)


def test_setup_kronos_path_missing(tmp_path, monkeypatch):
    """Missing path should raise SystemExit with helpful message."""
    from kronos_fx_benchmark import _setup_kronos_path
    monkeypatch.delenv("KRONOS_PATH", raising=False)
    with pytest.raises(SystemExit) as exc:
        _setup_kronos_path(default=str(tmp_path / "NonExistent"))
    assert "git clone" in str(exc.value)


def test_setup_kronos_path_env_override(tmp_path, monkeypatch):
    """KRONOS_PATH env var should override default."""
    from kronos_fx_benchmark import _setup_kronos_path
    fake_repo = tmp_path / "MyKronos"
    fake_repo.mkdir()
    monkeypatch.setenv("KRONOS_PATH", str(fake_repo))
    result = _setup_kronos_path(default="/should/not/be/used")
    assert result == str(fake_repo)


# ── Task 2: _normalize_columns + fetch_data ───────────────────────────────────

def test_normalize_columns_multiindex():
    """yfinance 0.2.x MultiIndex columns should be flattened to lowercase."""
    from kronos_fx_benchmark import _normalize_columns
    mi = pd.MultiIndex.from_tuples([
        ("Open", "EURUSD=X"), ("High", "EURUSD=X"), ("Low", "EURUSD=X"),
        ("Close", "EURUSD=X"), ("Volume", "EURUSD=X"),
    ])
    df = pd.DataFrame([[1.10, 1.11, 1.09, 1.105, 1000]], columns=mi)
    result = _normalize_columns(df)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_columns_flat():
    """Flat string columns should be lowercased."""
    from kronos_fx_benchmark import _normalize_columns
    df = pd.DataFrame([[1, 2, 3, 4, 5]], columns=["Open", "High", "Low", "Close", "Volume"])
    result = _normalize_columns(df)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_data_returns_ohlcv(monkeypatch):
    """fetch_data should return DataFrame with ohlcv columns, no NaN in close."""
    from kronos_fx_benchmark import fetch_data
    import yfinance as yf

    mock_data = pd.DataFrame(
        {"Open": [1.1], "High": [1.12], "Low": [1.09], "Close": [1.105], "Volume": [0.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    monkeypatch.setattr(yf, "download", lambda *a, **kw: mock_data)

    df = fetch_data(period="3y", ticker="EURUSD=X")
    assert set(df.columns) >= {"open", "high", "low", "close", "volume"}
    assert df["close"].isna().sum() == 0
    assert len(df) == 1


# ── Task 3: naive_predict ────────────────────────────────────────────────────

def test_naive_predict_constant():
    """Naive predictor returns last close repeated for all horizons."""
    from kronos_fx_benchmark import naive_predict
    close = pd.Series([1.08, 1.09, 1.10])
    result = naive_predict(close, pred_len=5)
    assert result.shape == (5,)
    assert np.all(result == pytest.approx(1.10))


def test_naive_predict_single_step():
    from kronos_fx_benchmark import naive_predict
    close = pd.Series([1.05])
    result = naive_predict(close, pred_len=1)
    assert result[0] == pytest.approx(1.05)


# ── Task 4: kronos_predict ───────────────────────────────────────────────────

def test_kronos_predict_shape():
    """kronos_predict returns numpy array of length pred_len."""
    from kronos_fx_benchmark import kronos_predict

    idx = pd.bdate_range("2024-01-01", periods=5)
    context = pd.DataFrame({
        "open":  [1.09, 1.10, 1.10, 1.11, 1.10],
        "high":  [1.10, 1.11, 1.11, 1.12, 1.11],
        "low":   [1.08, 1.09, 1.09, 1.10, 1.09],
        "close": [1.10, 1.10, 1.11, 1.10, 1.10],
    }, index=idx)

    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 1.105)})

    result = kronos_predict(MockPredictor(), context, pred_len=5)
    assert result.shape == (5,)
    assert np.all(result == pytest.approx(1.105))


# ── Task 5: walk_forward ─────────────────────────────────────────────────────

def _make_df(n: int, start_price: float = 1.10) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(42)
    close = start_price + np.cumsum(rng.normal(0, 0.001, n))
    return pd.DataFrame({
        "open": close - 0.001,
        "high": close + 0.002,
        "low":  close - 0.002,
        "close": close,
        "volume": np.zeros(n),
    }, index=idx)


def test_walk_forward_returns_list():
    from kronos_fx_benchmark import walk_forward

    df = _make_df(500)

    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 1.10)})

    results = walk_forward(df, MockPredictor(), lookback=100, pred_len=5, n_windows=5, stride=10)
    assert isinstance(results, list)
    assert len(results) == 5
    for r in results:
        assert set(r.keys()) == {"actual", "naive", "kronos"}
        assert r["actual"].shape == (5,)
        assert r["naive"].shape == (5,)
        assert r["kronos"].shape == (5,)


def test_walk_forward_naive_is_constant():
    from kronos_fx_benchmark import walk_forward

    df = _make_df(200)

    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 0.0)})

    results = walk_forward(df, MockPredictor(), lookback=50, pred_len=5, n_windows=3, stride=10)
    for r in results:
        assert np.all(r["naive"] == r["naive"][0])


# ── Task 6: evaluate + report ────────────────────────────────────────────────

def _make_results(n: int, kronos_offset: float, naive_offset: float) -> list:
    actual = np.ones((n, 5)) * 1.10
    kronos = actual + kronos_offset
    naive = actual + naive_offset
    return [
        {"actual": actual[i], "naive": naive[i], "kronos": kronos[i]}
        for i in range(n)
    ]


def test_evaluate_mae_values():
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.001, naive_offset=0.002)
    metrics = evaluate(results, horizons=(1, 3, 5))
    assert metrics[1]["mae_kronos"] == pytest.approx(0.001)
    assert metrics[1]["mae_naive"] == pytest.approx(0.002)


def test_evaluate_improvement_positive():
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.001, naive_offset=0.003)
    metrics = evaluate(results)
    assert metrics[1]["improvement_pct"] > 0


def test_evaluate_improvement_negative():
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.005, naive_offset=0.001)
    metrics = evaluate(results)
    assert metrics[1]["improvement_pct"] < 0


def test_report_creates_png(tmp_path):
    from kronos_fx_benchmark import evaluate, report
    results = _make_results(5, 0.001, 0.002)
    metrics = evaluate(results)
    out = str(tmp_path / "benchmark.png")
    report(metrics, output_path=out)
    assert os.path.isfile(out)
