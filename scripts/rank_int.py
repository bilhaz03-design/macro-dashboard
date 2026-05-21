"""
rank_int.py — Rank-based Inverse Normal Transform for cross-instrument normalization.

Converts raw signal values to standard-normal z-scores via rank percentile,
making thresholds instrument-agnostic and distribution-free.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata, norm
from typing import Union

# Standard threshold constants (inverse normal of percentile)
Z_P70 = 0.524    # |z| >= p70
Z_P85 = 1.036    # |z| >= p85
Z_P95 = 1.645    # |z| >= p95
Z_P99 = 2.326    # |z| >= p99
Z_P995 = 2.576   # |z| >= p99.5


def rank_int(
    x: Union[np.ndarray, pd.Series],
    ddof_correction: bool = True,
) -> np.ndarray:
    """
    Rank-based Inverse Normal Transform on a 1D array.

    Ranks x using method='average', computes percentile p = (rank - 0.5) / n,
    then maps to z = norm.ppf(p). NaN values are ignored in ranking and
    returned as NaN in output.

    Parameters
    ----------
    x : array-like (1D)
        Input values. May contain NaN.
    ddof_correction : bool
        Reserved for future variance correction; currently unused.

    Returns
    -------
    np.ndarray
        Z-scores aligned with input positions. NaN where input is NaN.

    Example
    -------
    >>> rank_int(np.array([1, 2, 3, 4, 5]))
    array([-1.28..., -0.52...,  0.  ,  0.52...,  1.28...])
    """
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    mask = ~np.isnan(x)
    valid = x[mask]
    if len(valid) == 0:
        return out
    n = len(valid)
    ranks = rankdata(valid, method="average")
    percentiles = (ranks - 0.5) / n
    out[mask] = norm.ppf(percentiles)
    return out


def rolling_rank_int(
    s: pd.Series,
    window: int = 1260,
    min_periods: int = 252,
) -> pd.Series:
    """
    Rolling Rank-INT z-score for a time series.

    For each time t, ranks s[t] against the window [t-window+1, t],
    computes p = (rank - 0.5) / n_valid, returns z = norm.ppf(p).
    Returns NaN when fewer than min_periods valid observations exist.

    Parameters
    ----------
    s : pd.Series
        Input series, typically with DatetimeIndex.
    window : int
        Rolling window length in observations (default 1260 ~ 5y trading days).
    min_periods : int
        Minimum valid observations required to emit a non-NaN z-score.

    Returns
    -------
    pd.Series
        Z-scores with same index as input.

    Example
    -------
    >>> rolling_rank_int(pd.Series(range(500)), window=252, min_periods=63)
    """
    values = s.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)

    for t in range(n):
        start = max(0, t - window + 1)
        window_vals = values[start : t + 1]
        valid_mask = ~np.isnan(window_vals)
        n_valid = valid_mask.sum()
        if n_valid < min_periods or np.isnan(values[t]):
            continue
        valid_vals = window_vals[valid_mask]
        # rank of the current value within the window
        ranks = rankdata(valid_vals, method="average")
        # find position of current value in valid_vals
        # current value corresponds to the last element if it's valid
        current_in_valid = valid_mask.cumsum()[-1] - 1
        p = (ranks[current_in_valid] - 0.5) / n_valid
        out[t] = norm.ppf(p)

    return pd.Series(out, index=s.index, name=s.name)


def signal_strength(z: float) -> str:
    """
    Map a z-score to a qualitative signal label.

    Parameters
    ----------
    z : float
        Signed z-score.

    Returns
    -------
    str
        'weak' (|z| < p70), 'medium' (p70-p85), 'strong' (p85-p95),
        'extreme' (|z| >= p95).
    """
    az = abs(z)
    if az >= Z_P95:
        return "extreme"
    if az >= Z_P85:
        return "strong"
    if az >= Z_P70:
        return "medium"
    return "weak"


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math

    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        global failures
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures += 1

    print("=== rank_int unit tests ===")

    # Test 1: monotone z-scores, mean ≈ 0, std ≈ 1
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    z = rank_int(a)
    check("monotone increasing", all(z[i] < z[i + 1] for i in range(len(z) - 1)))
    check("mean ≈ 0", abs(np.mean(z)) < 1e-10, f"mean={np.mean(z):.6f}")
    check("std ≈ 1 (approx)", 0.8 < np.std(z) < 1.2, f"std={np.std(z):.4f}")

    # Test 2: outlier → high rank but not extreme on small n=5
    a2 = np.array([100.0, 1.0, 1.0, 1.0, 1.0])
    z2 = rank_int(a2)
    check("outlier gets highest z", z2[0] == max(z2))
    # rank of 100 = 5, p = (5-0.5)/5 = 0.9, z = norm.ppf(0.9) ≈ 1.282
    expected_z = norm.ppf(0.9)
    check("outlier z ≈ 1.28 (p90)", abs(z2[0] - expected_z) < 1e-6,
          f"got {z2[0]:.4f}, expected {expected_z:.4f}")

    # Test 3: NaN preserved
    a3 = np.array([1.0, np.nan, 3.0, 4.0])
    z3 = rank_int(a3)
    check("NaN at pos 1 preserved", math.isnan(z3[1]))
    check("non-NaN ranked correctly", not math.isnan(z3[0]) and not math.isnan(z3[2]))
    check("remaining mean ≈ 0", abs(np.nanmean(z3)) < 1e-10)

    print("\n=== rolling_rank_int unit tests ===")

    np.random.seed(42)
    n_obs = 2000
    synthetic = pd.Series(
        np.random.randn(n_obs).cumsum(),
        index=pd.date_range("2015-01-01", periods=n_obs, freq="B"),
    )
    rz = rolling_rank_int(synthetic, window=252, min_periods=63)

    check("returns pd.Series", isinstance(rz, pd.Series))
    check("same length as input", len(rz) == n_obs)
    check("NaN before min_periods=63", all(math.isnan(v) for v in rz.iloc[:62]))
    check("non-NaN after warmup", not math.isnan(rz.iloc[63]))
    check("all z in [-4, 4] range", rz.dropna().between(-4, 4).all(),
          f"min={rz.dropna().min():.3f} max={rz.dropna().max():.3f}")

    print("\n=== signal_strength tests ===")
    check("weak below p70", signal_strength(0.3) == "weak")
    check("medium p70-p85", signal_strength(0.8) == "medium")
    check("strong p85-p95", signal_strength(1.2) == "strong")
    check("extreme p95+", signal_strength(2.0) == "extreme")
    check("negative extreme", signal_strength(-2.0) == "extreme")

    print(f"\n{'All tests passed.' if failures == 0 else f'{failures} test(s) FAILED.'}")
    exit(failures)
