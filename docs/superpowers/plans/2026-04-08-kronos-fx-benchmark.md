# Kronos FX Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a walk-forward benchmark that determines if Kronos-small beats a random walk baseline for EURUSD daily close price prediction.

**Architecture:** Single script `scripts/kronos_fx_benchmark.py` with pure functions for each component (fetch, normalize, predict, evaluate, report). Unit tests cover all pure functions; Kronos loading tested via a lightweight mock. No imports of Kronos happen at module level — only inside `load_kronos()`, so unit tests run without Kronos installed.

**Tech Stack:** Python, yfinance, numpy, pandas, matplotlib, Kronos (local clone via sys.path)

---

## File map

| File | Role |
|------|------|
| `scripts/kronos_fx_benchmark.py` | Main script — all benchmark logic |
| `tests/test_kronos_benchmark.py` | Unit tests for all pure functions |

---

### Task 1: Scaffold + Kronos path resolver

**Files:**
- Create: `scripts/kronos_fx_benchmark.py`
- Create: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing test for `_setup_kronos_path` — valid path**

```python
# tests/test_kronos_benchmark.py
import os
import sys
import pytest

# Add scripts/ to path so we can import without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/Desktop/Finans\ Projects
pytest tests/test_kronos_benchmark.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'kronos_fx_benchmark'`

- [ ] **Step 3: Create `scripts/kronos_fx_benchmark.py` with `_setup_kronos_path`**

```python
# scripts/kronos_fx_benchmark.py
"""
Kronos FX Benchmark — Walk-forward evaluation of Kronos-small vs naive baseline.
Usage:
    pip install yfinance
    git clone https://github.com/shiyu-coder/Kronos ~/Desktop/Kronos
    pip install -r ~/Desktop/Kronos/requirements.txt
    python scripts/kronos_fx_benchmark.py
"""

import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # no display required
import matplotlib.pyplot as plt


# ── Kronos path resolution ───────────────────────────────────────────────────

def _setup_kronos_path(default: str = os.path.expanduser("~/Desktop/Kronos")) -> str:
    """Resolve Kronos repo path, insert into sys.path, return resolved path.

    Priority: KRONOS_PATH env var > default argument.
    Raises SystemExit with clone instructions if path does not exist.
    """
    kronos_path = os.environ.get("KRONOS_PATH", default)
    if not os.path.isdir(kronos_path):
        raise SystemExit(
            f"\nKronos repo not found at: {kronos_path}\n\n"
            f"Fix:\n"
            f"  git clone https://github.com/shiyu-coder/Kronos {kronos_path}\n"
            f"  pip install -r {kronos_path}/requirements.txt\n\n"
            f"Or set a custom path:\n"
            f"  KRONOS_PATH=/your/path python scripts/kronos_fx_benchmark.py\n"
        )
    if kronos_path not in sys.path:
        sys.path.insert(0, kronos_path)
    return kronos_path
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_kronos_benchmark.py::test_setup_kronos_path_valid \
       tests/test_kronos_benchmark.py::test_setup_kronos_path_missing \
       tests/test_kronos_benchmark.py::test_setup_kronos_path_env_override -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Finans\ Projects
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: kronos benchmark scaffold + path resolver"
```

---

### Task 2: Data fetching + column normalization

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`
- Modify: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing tests for `_normalize_columns` and `fetch_data`**

```python
# Append to tests/test_kronos_benchmark.py


def test_normalize_columns_multiindex():
    """yfinance 0.2.x MultiIndex columns should be flattened to lowercase."""
    import pandas as pd
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
    import pandas as pd
    from kronos_fx_benchmark import _normalize_columns
    df = pd.DataFrame([[1, 2, 3, 4, 5]], columns=["Open", "High", "Low", "Close", "Volume"])
    result = _normalize_columns(df)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_data_returns_ohlcv(monkeypatch):
    """fetch_data should return DataFrame with ohlcv columns, no NaN in close."""
    import pandas as pd
    from kronos_fx_benchmark import fetch_data

    # Minimal mock — flat columns (older yfinance style)
    mock_data = pd.DataFrame(
        {"Open": [1.1], "High": [1.12], "Low": [1.09], "Close": [1.105], "Volume": [0.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )

    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **kw: mock_data)

    df = fetch_data(period="3y", ticker="EURUSD=X")
    assert set(df.columns) >= {"open", "high", "low", "close", "volume"}
    assert df["close"].isna().sum() == 0
    assert len(df) == 1
```

- [ ] **Step 2: Run — verify they fail**

```bash
pytest tests/test_kronos_benchmark.py::test_normalize_columns_multiindex \
       tests/test_kronos_benchmark.py::test_normalize_columns_flat \
       tests/test_kronos_benchmark.py::test_fetch_data_returns_ohlcv -v
```

Expected: `ImportError` or `AttributeError` (functions not yet defined)

- [ ] **Step 3: Add `_normalize_columns` and `fetch_data` to script**

```python
# Append to scripts/kronos_fx_benchmark.py (after _setup_kronos_path)

# ── Data ─────────────────────────────────────────────────────────────────────

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns (yfinance 0.2.x) and lowercase all names."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [col.lower() for col in df.columns]
    return df


def fetch_data(period: str = "3y", ticker: str = "EURUSD=X") -> pd.DataFrame:
    """Download OHLCV from yfinance, normalize columns, drop NaN close rows.

    Returns DataFrame with columns [open, high, low, close, volume],
    DatetimeIndex, sorted ascending.
    """
    import yfinance as yf
    raw = yf.download(ticker, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    raw = _normalize_columns(raw)
    df = raw[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    df.index = pd.to_datetime(df.index)
    return df.sort_index()
```

- [ ] **Step 4: Run — verify they pass**

```bash
pytest tests/test_kronos_benchmark.py::test_normalize_columns_multiindex \
       tests/test_kronos_benchmark.py::test_normalize_columns_flat \
       tests/test_kronos_benchmark.py::test_fetch_data_returns_ohlcv -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: data fetching + column normalization"
```

---

### Task 3: Naive baseline predictor

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`
- Modify: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_kronos_benchmark.py


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
```

- [ ] **Step 2: Run — verify they fail**

```bash
pytest tests/test_kronos_benchmark.py::test_naive_predict_constant \
       tests/test_kronos_benchmark.py::test_naive_predict_single_step -v
```

Expected: `ImportError` — `naive_predict` not defined

- [ ] **Step 3: Add `naive_predict`**

```python
# Append to scripts/kronos_fx_benchmark.py

# ── Predictors ────────────────────────────────────────────────────────────────

def naive_predict(context_close: pd.Series, pred_len: int) -> np.ndarray:
    """Random walk baseline: predict last known close for all future steps."""
    return np.full(pred_len, float(context_close.iloc[-1]))
```

- [ ] **Step 4: Run — verify they pass**

```bash
pytest tests/test_kronos_benchmark.py::test_naive_predict_constant \
       tests/test_kronos_benchmark.py::test_naive_predict_single_step -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: naive random walk baseline predictor"
```

---

### Task 4: Kronos predictor

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`
- Modify: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing test with mock predictor**

```python
# Append to tests/test_kronos_benchmark.py


def test_kronos_predict_shape():
    """kronos_predict returns numpy array of length pred_len."""
    import pandas as pd
    from kronos_fx_benchmark import kronos_predict

    # Build minimal context DataFrame (5 rows, OHLC)
    idx = pd.bdate_range("2024-01-01", periods=5)
    context = pd.DataFrame({
        "open":  [1.09, 1.10, 1.10, 1.11, 1.10],
        "high":  [1.10, 1.11, 1.11, 1.12, 1.11],
        "low":   [1.08, 1.09, 1.09, 1.10, 1.09],
        "close": [1.10, 1.10, 1.11, 1.10, 1.10],
    }, index=idx)

    # Mock predictor: .predict() returns DataFrame with 'close' column
    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 1.105)})

    result = kronos_predict(MockPredictor(), context, pred_len=5)
    assert result.shape == (5,)
    assert np.all(result == pytest.approx(1.105))
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_kronos_benchmark.py::test_kronos_predict_shape -v
```

Expected: `ImportError` — `kronos_predict` not defined

- [ ] **Step 3: Add `kronos_predict`**

```python
# Append to scripts/kronos_fx_benchmark.py

def kronos_predict(predictor, context: pd.DataFrame, pred_len: int = 5) -> np.ndarray:
    """Run KronosPredictor on context window, return predicted close prices.

    Args:
        predictor: KronosPredictor instance (loaded once externally)
        context:   DataFrame with DatetimeIndex, columns [open,high,low,close]
        pred_len:  number of future bars to predict

    Returns:
        numpy array of shape (pred_len,) — predicted close prices
    """
    x_ts = context.index.to_series().reset_index(drop=True)
    y_ts = pd.Series(
        pd.bdate_range(start=x_ts.iloc[-1] + pd.Timedelta(days=1), periods=pred_len)
    )
    x_df = context[["open", "high", "low", "close"]].reset_index(drop=True)
    pred = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=y_ts,
        pred_len=pred_len,
        T=0.6,
        top_p=0.9,
        sample_count=1,
    )
    return pred["close"].values


def load_kronos(kronos_path: str, device: str = "cpu"):
    """Load KronosTokenizer + Kronos model + return KronosPredictor.

    Imports happen here (not at module level) so unit tests work
    without Kronos installed.
    """
    from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402
    print("Loading Kronos-Tokenizer-base from HuggingFace...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    print("Loading Kronos-small from HuggingFace...")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    return KronosPredictor(model, tokenizer, device=device, max_context=512)
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_kronos_benchmark.py::test_kronos_predict_shape -v
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: kronos_predict + load_kronos"
```

---

### Task 5: Walk-forward evaluation

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`
- Modify: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_kronos_benchmark.py


def _make_df(n: int, start_price: float = 1.10) -> pd.DataFrame:
    """Helper: synthetic OHLC DataFrame with business day index."""
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
    """walk_forward should return a list of dicts with actual/naive/kronos keys."""
    from kronos_fx_benchmark import walk_forward

    df = _make_df(500)

    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 1.10)})

    results = walk_forward(
        df, MockPredictor(), lookback=100, pred_len=5, n_windows=5, stride=10
    )
    assert isinstance(results, list)
    assert len(results) == 5
    for r in results:
        assert set(r.keys()) == {"actual", "naive", "kronos"}
        assert r["actual"].shape == (5,)
        assert r["naive"].shape == (5,)
        assert r["kronos"].shape == (5,)


def test_walk_forward_naive_is_constant():
    """Naive predictions inside walk_forward equal last context close."""
    from kronos_fx_benchmark import walk_forward

    df = _make_df(200)

    class MockPredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
            return pd.DataFrame({"close": np.full(pred_len, 0.0)})

    results = walk_forward(
        df, MockPredictor(), lookback=50, pred_len=5, n_windows=3, stride=10
    )
    for r in results:
        # All naive values should be identical (last context close)
        assert np.all(r["naive"] == r["naive"][0])
```

- [ ] **Step 2: Run — verify they fail**

```bash
pytest tests/test_kronos_benchmark.py::test_walk_forward_returns_list \
       tests/test_kronos_benchmark.py::test_walk_forward_naive_is_constant -v
```

Expected: `ImportError` — `walk_forward` not defined

- [ ] **Step 3: Add `walk_forward`**

```python
# Append to scripts/kronos_fx_benchmark.py

# ── Walk-forward ──────────────────────────────────────────────────────────────

def walk_forward(
    df: pd.DataFrame,
    predictor,
    lookback: int = 400,
    pred_len: int = 5,
    n_windows: int = 20,
    stride: int = 10,
) -> list[dict]:
    """Sliding-window benchmark over the tail of df.

    Window layout:
        context: df[end_ctx - lookback : end_ctx]   (lookback bars)
        target:  df[end_ctx : end_ctx + pred_len]   (pred_len bars)

    First window's end_ctx is positioned so all n_windows fit in the tail.
    """
    # Anchor the first window so all n_windows fit without running out of data
    test_start = len(df) - (n_windows - 1) * stride - pred_len

    results = []
    for i in range(n_windows):
        end_ctx = test_start + i * stride
        start_ctx = end_ctx - lookback

        if start_ctx < 0 or end_ctx + pred_len > len(df):
            continue  # not enough data — skip silently

        context = df.iloc[start_ctx:end_ctx]
        target = df.iloc[end_ctx:end_ctx + pred_len]

        actual = target["close"].values
        naive = naive_predict(context["close"], pred_len)
        kronos = kronos_predict(predictor, context, pred_len)

        results.append({"actual": actual, "naive": naive, "kronos": kronos})
        print(f"  Window {i+1}/{n_windows} done", end="\r", flush=True)

    print()  # newline after progress
    return results
```

- [ ] **Step 4: Run — verify they pass**

```bash
pytest tests/test_kronos_benchmark.py::test_walk_forward_returns_list \
       tests/test_kronos_benchmark.py::test_walk_forward_naive_is_constant -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: walk-forward evaluation loop"
```

---

### Task 6: Evaluate + Report

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`
- Modify: `tests/test_kronos_benchmark.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_kronos_benchmark.py


def _make_results(n: int, kronos_offset: float, naive_offset: float) -> list[dict]:
    """Helper: synthetic walk_forward results with known MAE."""
    actual = np.ones((n, 5)) * 1.10
    kronos = actual + kronos_offset
    naive = actual + naive_offset
    return [
        {"actual": actual[i], "naive": naive[i], "kronos": kronos[i]}
        for i in range(n)
    ]


def test_evaluate_mae_values():
    """evaluate() returns correct MAE for known offsets."""
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.001, naive_offset=0.002)
    metrics = evaluate(results, horizons=(1, 3, 5))
    assert metrics[1]["mae_kronos"] == pytest.approx(0.001)
    assert metrics[1]["mae_naive"] == pytest.approx(0.002)


def test_evaluate_improvement_positive():
    """Kronos better than naive → improvement_pct > 0."""
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.001, naive_offset=0.003)
    metrics = evaluate(results)
    assert metrics[1]["improvement_pct"] > 0


def test_evaluate_improvement_negative():
    """Kronos worse than naive → improvement_pct < 0."""
    from kronos_fx_benchmark import evaluate
    results = _make_results(10, kronos_offset=0.005, naive_offset=0.001)
    metrics = evaluate(results)
    assert metrics[1]["improvement_pct"] < 0


def test_report_creates_png(tmp_path):
    """report() saves PNG to specified path."""
    from kronos_fx_benchmark import evaluate, report
    results = _make_results(5, 0.001, 0.002)
    metrics = evaluate(results)
    out = str(tmp_path / "benchmark.png")
    report(metrics, output_path=out)
    assert os.path.isfile(out)
```

- [ ] **Step 2: Run — verify they fail**

```bash
pytest tests/test_kronos_benchmark.py::test_evaluate_mae_values \
       tests/test_kronos_benchmark.py::test_evaluate_improvement_positive \
       tests/test_kronos_benchmark.py::test_evaluate_improvement_negative \
       tests/test_kronos_benchmark.py::test_report_creates_png -v
```

Expected: `ImportError` — `evaluate` and `report` not defined

- [ ] **Step 3: Add `evaluate` and `report`**

```python
# Append to scripts/kronos_fx_benchmark.py

# ── Evaluate ──────────────────────────────────────────────────────────────────

def evaluate(results: list[dict], horizons: tuple = (1, 3, 5)) -> dict:
    """Compute MAE per horizon for Kronos and naive baseline.

    Returns dict keyed by horizon day number, each value a dict:
        mae_kronos, mae_kronos_std, mae_naive, mae_naive_std, improvement_pct
    improvement_pct > 0 means Kronos is better.
    """
    out = {}
    for h in horizons:
        idx = h - 1
        mae_k = [
            abs(r["kronos"][idx] - r["actual"][idx])
            for r in results
            if len(r["actual"]) > idx
        ]
        mae_n = [
            abs(r["naive"][idx] - r["actual"][idx])
            for r in results
            if len(r["actual"]) > idx
        ]
        mean_k = float(np.mean(mae_k))
        mean_n = float(np.mean(mae_n))
        out[h] = {
            "mae_kronos":     mean_k,
            "mae_kronos_std": float(np.std(mae_k)),
            "mae_naive":      mean_n,
            "mae_naive_std":  float(np.std(mae_n)),
            "improvement_pct": (mean_n - mean_k) / mean_n * 100 if mean_n > 0 else 0.0,
        }
    return out


# ── Report ────────────────────────────────────────────────────────────────────

def report(metrics: dict, output_path: str = "data/kronos_eurusd_benchmark.png") -> None:
    """Print MAE table to terminal and save bar chart to output_path."""
    horizons = sorted(metrics.keys())

    # Terminal table
    print("\n" + "=" * 65)
    print(f"  KRONOS-SMALL vs NAIVE BASELINE — EURUSD DAGLIG (walk-forward)")
    print("=" * 65)
    print(f"{'Horisont':>10}  {'MAE Kronos':>14}  {'MAE Naive':>12}  {'Förbättring':>12}")
    print("-" * 65)
    for h in horizons:
        m = metrics[h]
        verdict = "✓" if m["improvement_pct"] > 10 else ("~" if m["improvement_pct"] > 5 else "✗")
        print(
            f"{'Dag '+str(h):>10}  "
            f"{m['mae_kronos']:>8.5f}±{m['mae_kronos_std']:.5f}  "
            f"{m['mae_naive']:>10.5f}  "
            f"{m['improvement_pct']:>+9.1f}%  {verdict}"
        )
    print("=" * 65)

    # Verdict
    d1 = metrics[1]["improvement_pct"]
    if d1 > 10:
        verdict_text = "SIGNAL FUNNEN. Kronos slår random walk med >10%. Fortsätt med integration."
    elif d1 > 5:
        verdict_text = "BORDERLINE. Granska dag 3 och dag 5 — marginell fördel."
    else:
        verdict_text = "INGEN SIGNAL. Kronos slår inte random walk. Förkasta för detta use case."
    print(f"\n  VERDICT: {verdict_text}\n")

    # Bar chart
    x = np.arange(len(horizons))
    mae_k = [metrics[h]["mae_kronos"] for h in horizons]
    mae_n = [metrics[h]["mae_naive"] for h in horizons]
    labels = [f"Dag {h}" for h in horizons]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - 0.2, mae_k, 0.4, label="Kronos-small", color="steelblue")
    ax.bar(x + 0.2, mae_n, 0.4, label="Naive (random walk)", color="salmon")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("MAE (pris, EURUSD)")
    ax.set_title("Kronos-small vs Naive Baseline — EURUSD daglig")
    ax.legend()
    plt.tight_layout()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"  Chart sparad: {output_path}\n")
```

- [ ] **Step 4: Run — verify they pass**

```bash
pytest tests/test_kronos_benchmark.py::test_evaluate_mae_values \
       tests/test_kronos_benchmark.py::test_evaluate_improvement_positive \
       tests/test_kronos_benchmark.py::test_evaluate_improvement_negative \
       tests/test_kronos_benchmark.py::test_report_creates_png -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/kronos_fx_benchmark.py tests/test_kronos_benchmark.py
git commit -m "feat: evaluate + report with MAE table and bar chart"
```

---

### Task 7: `main()` + final run

**Files:**
- Modify: `scripts/kronos_fx_benchmark.py`

- [ ] **Step 1: Add `main()` to script**

```python
# Append to scripts/kronos_fx_benchmark.py

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kronos FX Benchmark — EURUSD walk-forward")
    parser.add_argument("--ticker", default="EURUSD=X", help="yfinance ticker (default: EURUSD=X)")
    parser.add_argument("--period", default="3y", help="yfinance period (default: 3y)")
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument("--pred-len", type=int, default=5)
    parser.add_argument("--n-windows", type=int, default=20)
    parser.add_argument("--stride", type=int, default=10)
    parser.add_argument("--output", default="data/kronos_eurusd_benchmark.png")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    # 1. Setup Kronos
    kronos_path = _setup_kronos_path()
    print(f"Kronos repo: {kronos_path}")

    # 2. Fetch data
    print(f"\nFetching {args.ticker} ({args.period}) via yfinance...")
    df = fetch_data(period=args.period, ticker=args.ticker)
    print(f"Data: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")

    min_required = args.lookback + args.n_windows * args.stride + args.pred_len
    if len(df) < min_required:
        raise SystemExit(
            f"Not enough data: {len(df)} bars, need ≥{min_required}. "
            f"Try --period 5y or reduce --n-windows."
        )

    # 3. Load Kronos (downloads from HuggingFace on first run)
    print("\nLoading Kronos model (downloads ~200MB on first run)...")
    predictor = load_kronos(kronos_path, device=args.device)

    # 4. Walk-forward
    print(f"\nRunning walk-forward: {args.n_windows} windows, lookback={args.lookback}, pred_len={args.pred_len}")
    results = walk_forward(
        df, predictor,
        lookback=args.lookback,
        pred_len=args.pred_len,
        n_windows=args.n_windows,
        stride=args.stride,
    )
    print(f"Completed {len(results)} windows.")

    # 5. Evaluate + report
    metrics = evaluate(results, horizons=(1, 3, 5))
    report(metrics, output_path=args.output)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full unit test suite — all should pass**

```bash
cd ~/Desktop/Finans\ Projects
pytest tests/test_kronos_benchmark.py -v
```

Expected: `12 passed` (all tasks 1-6 tests)

- [ ] **Step 3: Prerequisites check**

```bash
# Clone Kronos if not present
ls ~/Desktop/Kronos 2>/dev/null || git clone https://github.com/shiyu-coder/Kronos ~/Desktop/Kronos

# Install dependencies
pip install -r ~/Desktop/Kronos/requirements.txt
pip install yfinance
```

- [ ] **Step 4: Run the benchmark**

```bash
cd ~/Desktop/Finans\ Projects
python scripts/kronos_fx_benchmark.py
```

Expected output:
```
Kronos repo: /Users/.../Desktop/Kronos
Fetching EURUSD=X (3y) via yfinance...
Data: 756 bars, 2022-04-08 → 2025-04-07
Loading Kronos model (downloads ~200MB on first run)...
Running walk-forward: 20 windows, lookback=400, pred_len=5
  Window 20/20 done
Completed 20 windows.

=================================================================
  KRONOS-SMALL vs NAIVE BASELINE — EURUSD DAGLIG (walk-forward)
=================================================================
  Horisont      MAE Kronos     MAE Naive   Förbättring
-----------------------------------------------------------------
     Dag 1  0.00XXX±0.00XXX     0.00XXX       +/-XX.X%  ✓/~/✗
     Dag 3  0.00XXX±0.00XXX     0.00XXX       +/-XX.X%
     Dag 5  0.00XXX±0.00XXX     0.00XXX       +/-XX.X%
=================================================================
  VERDICT: [baserat på resultat]

  Chart sparad: data/kronos_eurusd_benchmark.png
```

- [ ] **Step 5: Final commit**

```bash
git add scripts/kronos_fx_benchmark.py
git commit -m "feat: main() entrypoint + kronos fx benchmark complete"
```

---

## Self-review

**Spec coverage:**
- ✓ fetch_data() — Task 2
- ✓ naive_predict() — Task 3
- ✓ kronos_predict() + load_kronos() — Task 4
- ✓ walk_forward() — Task 5
- ✓ evaluate() — Task 6
- ✓ report() terminal + PNG — Task 6
- ✓ Kronos path resolver + KRONOS_PATH env — Task 1
- ✓ yfinance MultiIndex normalization — Task 2
- ✓ y_timestamp via pd.bdate_range — Task 4
- ✓ Framgångskriterium (>10% / 5-10% / <5%) — report() verdict
- ✓ Output: `data/kronos_eurusd_benchmark.png` — Task 7

**No placeholders:** All steps contain actual code.

**Type consistency:**
- `naive_predict(context_close: pd.Series, pred_len: int) -> np.ndarray` — used correctly in walk_forward
- `kronos_predict(predictor, context: pd.DataFrame, pred_len: int) -> np.ndarray` — used correctly
- `walk_forward() -> list[dict]` keys `{actual, naive, kronos}` — consumed correctly by `evaluate()`
- `evaluate() -> dict` keyed by int horizon — consumed correctly by `report()`
