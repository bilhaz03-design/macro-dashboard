"""
Kronos FX Benchmark — Walk-forward evaluation of Kronos-small vs naive baseline.

Usage:
    git clone https://github.com/shiyu-coder/Kronos ~/Desktop/Kronos
    pip install -r ~/Desktop/Kronos/requirements.txt
    pip install yfinance
    python scripts/kronos_fx_benchmark.py

Optional:
    KRONOS_PATH=/your/path python scripts/kronos_fx_benchmark.py
"""

import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless — no display required
import matplotlib.pyplot as plt


# ── Kronos path resolution ────────────────────────────────────────────────────

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


# ── Data ──────────────────────────────────────────────────────────────────────

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


# ── Predictors ────────────────────────────────────────────────────────────────

def naive_predict(context_close: pd.Series, pred_len: int) -> np.ndarray:
    """Random walk baseline: predict last known close for all future steps."""
    return np.full(pred_len, float(context_close.iloc[-1]))


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
    """Load KronosTokenizer + Kronos model, return KronosPredictor.

    Imports happen here (not at module level) so unit tests work
    without Kronos installed.
    """
    from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402
    print("Loading Kronos-Tokenizer-base from HuggingFace...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    print("Loading Kronos-small from HuggingFace...")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    return KronosPredictor(model, tokenizer, device=device, max_context=512)


# ── Walk-forward ──────────────────────────────────────────────────────────────

def walk_forward(
    df: pd.DataFrame,
    predictor,
    lookback: int = 400,
    pred_len: int = 5,
    n_windows: int = 20,
    stride: int = 10,
) -> list:
    """Sliding-window benchmark over the tail of df.

    Window layout:
        context: df[end_ctx - lookback : end_ctx]   (lookback bars)
        target:  df[end_ctx : end_ctx + pred_len]   (pred_len bars)

    First window's end_ctx is positioned so all n_windows fit in the tail.
    """
    test_start = len(df) - (n_windows - 1) * stride - pred_len

    results = []
    for i in range(n_windows):
        end_ctx = test_start + i * stride
        start_ctx = end_ctx - lookback

        if start_ctx < 0 or end_ctx + pred_len > len(df):
            continue

        context = df.iloc[start_ctx:end_ctx]
        target = df.iloc[end_ctx:end_ctx + pred_len]

        actual = target["close"].values
        naive = naive_predict(context["close"], pred_len)
        kronos = kronos_predict(predictor, context, pred_len)

        results.append({"actual": actual, "naive": naive, "kronos": kronos})
        print(f"  Window {i+1}/{n_windows} done", end="\r", flush=True)

    print()
    return results


# ── Evaluate ──────────────────────────────────────────────────────────────────

def evaluate(results: list, horizons: tuple = (1, 3, 5)) -> dict:
    """Compute MAE per horizon for Kronos and naive baseline.

    Returns dict keyed by horizon day number, each value a dict:
        mae_kronos, mae_kronos_std, mae_naive, mae_naive_std, improvement_pct
    improvement_pct > 0 means Kronos is better than naive.
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
            "mae_kronos":      mean_k,
            "mae_kronos_std":  float(np.std(mae_k)),
            "mae_naive":       mean_n,
            "mae_naive_std":   float(np.std(mae_n)),
            "improvement_pct": (mean_n - mean_k) / mean_n * 100 if mean_n > 0 else 0.0,
        }
    return out


# ── Report ────────────────────────────────────────────────────────────────────

def report(metrics: dict, output_path: str = "data/kronos_eurusd_benchmark.png") -> None:
    """Print MAE comparison table to terminal and save bar chart to output_path."""
    horizons = sorted(metrics.keys())

    print("\n" + "=" * 65)
    print("  KRONOS-SMALL vs NAIVE BASELINE — EURUSD DAGLIG (walk-forward)")
    print("=" * 65)
    print(f"{'Horisont':>10}  {'MAE Kronos':>16}  {'MAE Naive':>12}  {'Förbättring':>12}")
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

    d1 = metrics[1]["improvement_pct"]
    if d1 > 10:
        verdict_text = "SIGNAL FUNNEN. Kronos slår random walk med >10%. Fortsätt integration."
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Kronos FX Benchmark — EURUSD walk-forward evaluation"
    )
    parser.add_argument("--ticker",    default="EURUSD=X")
    parser.add_argument("--period",    default="3y")
    parser.add_argument("--lookback",  type=int, default=400)
    parser.add_argument("--pred-len",  type=int, default=5)
    parser.add_argument("--n-windows", type=int, default=20)
    parser.add_argument("--stride",    type=int, default=10)
    parser.add_argument("--output",    default="data/kronos_eurusd_benchmark.png")
    parser.add_argument("--device",    default="cpu")
    args = parser.parse_args()

    kronos_path = _setup_kronos_path()
    print(f"Kronos repo: {kronos_path}")

    print(f"\nFetching {args.ticker} ({args.period}) via yfinance...")
    df = fetch_data(period=args.period, ticker=args.ticker)
    print(f"Data: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")

    min_required = args.lookback + (args.n_windows - 1) * args.stride + args.pred_len
    if len(df) < min_required:
        raise SystemExit(
            f"Not enough data: {len(df)} bars, need ≥{min_required}. "
            f"Try --period 5y or reduce --n-windows."
        )

    print("\nLoading Kronos model (downloads ~200MB on first run)...")
    predictor = load_kronos(kronos_path, device=args.device)

    print(f"\nRunning walk-forward: {args.n_windows} windows, "
          f"lookback={args.lookback}, pred_len={args.pred_len}")
    results = walk_forward(
        df, predictor,
        lookback=args.lookback,
        pred_len=args.pred_len,
        n_windows=args.n_windows,
        stride=args.stride,
    )
    print(f"Completed {len(results)} windows.")

    metrics = evaluate(results, horizons=(1, 3, 5))
    report(metrics, output_path=args.output)


if __name__ == "__main__":
    main()
