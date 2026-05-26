#!/usr/bin/env python3
"""Shared MLPB Prime Lite helpers.

The goal is to turn the QT Compass idea into scanner-safe features:
robust detrended z-score, z-score momentum, candle cleanliness, and a
human-readable tier. These are filters, not standalone trade signals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PrimeThresholds:
    z_len: int = 252
    short_mad_len: int = 63
    z_mom_len: int = 3
    percentile_len: int = 504


def finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def cache_path(cache_dir: Path, ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("^", "")
    return cache_dir / f"{safe}.csv"


def load_cached_ohlcv(cache_dir: Path, ticker: str) -> pd.DataFrame | None:
    path = cache_path(cache_dir, ticker)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
    except Exception:
        return None
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if any(col not in df.columns for col in cols):
        return None
    out = df[cols].dropna().copy()
    out = out[out["Volume"] > 0].sort_index()
    return out if len(out) else None


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def qt_prime_lite(close: pd.Series, thresholds: PrimeThresholds = PrimeThresholds()) -> pd.DataFrame:
    """Compute QT Prime Lite features.

    The model is intentionally scanner-safe: no future bars, deterministic
    rolling windows, and human-readable labels. It expands QT Compass into:
    residual/trend stretch, ticker-specific percentile, phase, value of
    waiting, minimum confirmation needed, and post-entry monitor state.
    """

    values = np.log(close.astype(float).replace(0, np.nan)).to_numpy(dtype=float)
    n = len(values)
    z = np.full(n, np.nan)
    trend = np.full(n, np.nan)
    mad_adaptive = np.full(n, np.nan)
    x = np.arange(thresholds.z_len, dtype=float)

    for pos in range(thresholds.z_len, n):
        window = values[pos - thresholds.z_len : pos]
        if not np.isfinite(window).all() or not np.isfinite(values[pos]):
            continue
        slope, intercept = np.polyfit(x, window, 1)
        fit = intercept + slope * x
        residuals = window - fit
        long_mad = np.median(np.abs(residuals - np.median(residuals))) * 1.4826
        short_resid = residuals[-thresholds.short_mad_len :]
        short_mad = np.median(np.abs(short_resid - np.median(short_resid))) * 1.4826
        scale = 0.65 * short_mad + 0.35 * long_mad
        current_trend = intercept + slope * thresholds.z_len
        trend[pos] = current_trend
        mad_adaptive[pos] = scale
        if scale > 0 and math.isfinite(scale):
            z[pos] = (values[pos] - current_trend) / scale

    z_series = pd.Series(z, index=close.index)
    delta = (z_series - z_series.shift(thresholds.z_mom_len)) / thresholds.z_mom_len
    valid_qt = np.isfinite(z_series.to_numpy(dtype=float)) & np.isfinite(delta.to_numpy(dtype=float))

    z_values = z_series.to_numpy(dtype=float)
    stretch_pctile = np.full(n, np.nan)
    abs_stretch_pctile = np.full(n, np.nan)
    min_periods = min(126, thresholds.percentile_len)
    for pos in range(n):
        start = max(0, pos - thresholds.percentile_len + 1)
        window = z_values[start : pos + 1]
        window = window[np.isfinite(window)]
        current = z_values[pos]
        if len(window) < min_periods or not np.isfinite(current):
            continue
        stretch_pctile[pos] = float(np.mean(window <= current))
        abs_stretch_pctile[pos] = float(np.mean(np.abs(window) <= abs(current)))

    stretch_series = pd.Series(stretch_pctile, index=close.index)
    abs_stretch_series = pd.Series(abs_stretch_pctile, index=close.index)
    phase = np.select(
        [
            (z_series <= -1.2) & (delta <= -0.05),
            (z_series <= -1.2) & (delta > -0.05) & (delta <= 0.08),
            (z_series < 0.9) & (delta > 0.08),
            (z_series.between(-1.2, 0.9)) & (delta >= -0.05),
            z_series > 1.6,
            delta < -0.30,
        ],
        ["EARLY_FALLING", "STABILIZING", "REPAIRING", "SUPPORTED_PULLBACK", "CHASING", "FADING"],
        default="NEUTRAL",
    )
    phase = np.where(valid_qt, phase, "UNKNOWN")
    regime = np.select(
        [
            np.isin(phase, ["REPAIRING", "SUPPORTED_PULLBACK"]),
            np.isin(phase, ["CHASING"]),
            np.isin(phase, ["FADING", "EARLY_FALLING"]),
            np.isin(phase, ["STABILIZING"]),
        ],
        ["SUPPORTIVE", "EXTENDED", "FADING", "RECOVERY"],
        default="NEUTRAL",
    )
    regime = np.where(valid_qt, regime, "UNKNOWN")
    wait_score = np.select(
        [
            np.isin(phase, ["EARLY_FALLING", "FADING"]),
            np.isin(phase, ["CHASING"]) | (z_series > 1.2),
            np.isin(phase, ["STABILIZING"]),
            np.isin(phase, ["REPAIRING", "SUPPORTED_PULLBACK"]),
        ],
        [86, 78, 58, 24],
        default=46,
    ).astype(float)
    wait_score = pd.Series(wait_score, index=close.index)
    wait_score = np.where(abs_stretch_series > 0.92, np.maximum(wait_score, 68), wait_score)
    wait_score = pd.Series(wait_score, index=close.index)
    wait_score = wait_score.where(valid_qt, np.nan)
    wait_label = np.select(
        [
            wait_score >= 65,
            wait_score >= 35,
        ],
        ["HIGH_WAIT_VALUE", "MEDIUM_WAIT_VALUE"],
        default="LOW_WAIT_VALUE",
    )
    wait_label = np.where(valid_qt, wait_label, "UNKNOWN")
    confirmation = np.select(
        [
            np.isin(phase, ["REPAIRING", "SUPPORTED_PULLBACK"]) & (wait_score < 35),
            phase == "STABILIZING",
            phase == "EARLY_FALLING",
            phase == "CHASING",
            phase == "FADING",
        ],
        ["FRAMEWORK_VALID_ENOUGH", "EOD_HOLD_OR_RECLAIM", "FOLLOW_THROUGH_CLOSE", "PULLBACK_OR_RESET", "AVOID_UNTIL_REPAIR"],
        default="EOD_CONFIRMATION",
    )
    confirmation = np.where(valid_qt, confirmation, "UNKNOWN")
    post_entry = np.select(
        [
            np.isin(phase, ["REPAIRING", "SUPPORTED_PULLBACK"]) & (delta > 0),
            np.isin(phase, ["SUPPORTED_PULLBACK", "STABILIZING"]) & (delta >= -0.05),
            z_series > 1.8,
            np.isin(phase, ["EARLY_FALLING", "FADING"]) | (delta < -0.25),
        ],
        ["IMPROVING", "STABLE", "EXTENDED_MANAGE_RISK", "DETERIORATING"],
        default="WATCH",
    )
    post_entry = np.where(valid_qt, post_entry, "UNKNOWN")
    return pd.DataFrame(
        {
            "QT_Z": z_series,
            "QT_Z_DELTA": delta,
            "QT_STRETCH_PCTILE": stretch_series,
            "QT_ABS_STRETCH_PCTILE": abs_stretch_series,
            "QT_PHASE": phase,
            "QT_WAIT_SCORE": wait_score,
            "QT_WAIT_LABEL": wait_label,
            "QT_CONFIRMATION": confirmation,
            "QT_POST_ENTRY_STATE": post_entry,
            "QT_REGIME": regime,
            "QT_TREND_LOG": trend,
            "QT_MAD": mad_adaptive,
        },
        index=close.index,
    )


def add_prime_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    open_ = out["Open"].astype(float)
    prev = close.shift(1)

    for window in (21, 50, 126, 252):
        if f"SMA{window}" not in out.columns:
            out[f"SMA{window}"] = close.rolling(window, min_periods=window).mean()

    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    if "ATR14_PCT" not in out.columns:
        out["ATR14_PCT"] = tr.rolling(14, min_periods=14).mean() / close
    if "RSI14" not in out.columns:
        out["RSI14"] = rsi(close, 14)
    if "CLV" not in out.columns:
        rng = high - low
        out["CLV"] = np.where(rng == 0, 0.0, (2 * close - high - low) / rng)
    if "DOLLAR_VOL20" not in out.columns:
        out["DOLLAR_VOL20"] = (close * out["Volume"]).rolling(20, min_periods=20).mean()
    if "DD63" not in out.columns:
        out["DD63"] = close / close.rolling(63, min_periods=63).max() - 1

    out["BAR_RANGE_PCT"] = (high - low) / close
    out["BODY_PCT"] = (close - open_).abs() / close
    out["RANGE_ATR_RATIO"] = out["BAR_RANGE_PCT"] / out["ATR14_PCT"].replace(0, np.nan)
    out["BODY_ATR_RATIO"] = out["BODY_PCT"] / out["ATR14_PCT"].replace(0, np.nan)
    qt = qt_prime_lite(close)
    for col in qt.columns:
        out[col] = qt[col]
    return out


def visual_quality(row: pd.Series | dict, setup: str | None = None) -> dict[str, Any]:
    ma = int(str(setup or row.get("setup") or "MLPB50").replace("MLPB", ""))
    dist = float(row.get("dist_sma", row.get("latest_dist_sma", np.nan)))
    atr = float(row.get("atr14_pct", row.get("latest_atr14_pct", np.nan)))
    clv = float(row.get("clv", np.nan))
    dd63 = float(row.get("dd63", np.nan))
    range_atr = float(row.get("range_atr_ratio", row.get("RANGE_ATR_RATIO", np.nan)))
    body_atr = float(row.get("body_atr_ratio", row.get("BODY_ATR_RATIO", np.nan)))
    rsi_val = float(row.get("rsi14", row.get("latest_rsi14", np.nan)))

    reasons: list[str] = []
    score = 0
    if finite(dist):
        if 0 < dist <= 0.04:
            score += 26
        elif dist <= 0.08:
            score += 18
            reasons.append("slightly extended above SMA")
        elif dist <= 0.12:
            score += 6
            reasons.append("extended 8-12% above SMA")
        else:
            score -= 10
            reasons.append("too extended above SMA")
    if finite(atr):
        if atr <= 0.05:
            score += 18
        elif atr <= 0.08:
            score += 10
            reasons.append("elevated ATR")
        else:
            score -= 8
            reasons.append("high ATR")
    if finite(clv):
        if clv >= 0.35:
            score += 18
        elif clv >= 0.10:
            score += 8
            reasons.append("weak close location")
        else:
            score -= 10
            reasons.append("poor close location")
    if finite(dd63):
        if -0.25 <= dd63 <= -0.03:
            score += 12
        elif dd63 > -0.03:
            reasons.append("not much pullback from local high")
        else:
            reasons.append("deep drawdown path")
    if finite(range_atr):
        if range_atr <= 1.65:
            score += 12
        elif range_atr <= 2.3:
            score += 4
            reasons.append("wide signal candle")
        else:
            score -= 8
            reasons.append("chaotic signal candle")
    if finite(body_atr):
        if body_atr <= 0.90:
            score += 8
        elif body_atr > 1.4:
            reasons.append("large body vs ATR")
    if finite(rsi_val) and rsi_val > 72:
        reasons.append("RSI overheated")
        score -= 6
    if ma == 21 and finite(dist) and dist > 0.08:
        score -= 6
        reasons.append("fast-SMA setup is chased")

    score = int(max(0, min(100, score)))
    if score >= 78:
        grade = "CLEAN"
    elif score >= 58:
        grade = "MIXED"
    else:
        grade = "MESSY"
    return {"score": score, "grade": grade, "reasons": reasons[:5]}


def qt_quality(row: pd.Series | dict) -> dict[str, Any]:
    z = float(row.get("qt_z", row.get("QT_Z", np.nan)))
    dz = float(row.get("qt_z_delta", row.get("QT_Z_DELTA", np.nan)))
    stretch_pctile = float(row.get("qt_stretch_pctile", row.get("QT_STRETCH_PCTILE", np.nan)))
    abs_stretch_pctile = float(row.get("qt_abs_stretch_pctile", row.get("QT_ABS_STRETCH_PCTILE", np.nan)))
    phase = str(row.get("qt_phase", row.get("QT_PHASE", "UNKNOWN")))
    wait_score = float(row.get("qt_wait_score", row.get("QT_WAIT_SCORE", np.nan)))
    wait_label = str(row.get("qt_wait_label", row.get("QT_WAIT_LABEL", "UNKNOWN")))
    confirmation = str(row.get("qt_confirmation", row.get("QT_CONFIRMATION", "UNKNOWN")))
    post_entry = str(row.get("qt_post_entry_state", row.get("QT_POST_ENTRY_STATE", "UNKNOWN")))
    regime = str(row.get("qt_regime", row.get("QT_REGIME", "UNKNOWN")))
    reasons: list[str] = []
    score = 45
    if finite(z):
        if -1.4 <= z <= 0.8:
            score += 20
        elif 0.8 < z <= 1.6:
            score += 10
            reasons.append("QT moderately extended")
        elif z > 1.6:
            score -= 18
            reasons.append("QT overextended")
        elif z < -1.8:
            score += 6
            reasons.append("QT deep value/pullback")
    if finite(stretch_pctile):
        if 0.08 <= stretch_pctile <= 0.70:
            score += 10
        elif stretch_pctile < 0.08:
            score += 4
            reasons.append("ticker-specific extreme low")
        elif stretch_pctile > 0.86:
            score -= 10
            reasons.append("ticker-specific upper stretch")
    if finite(abs_stretch_pctile) and abs_stretch_pctile > 0.94:
        score -= 6
        reasons.append("historically extreme stretch")
    if finite(dz):
        if dz > 0.08:
            score += 20
        elif dz >= -0.05:
            score += 10
        elif dz < -0.30:
            score -= 18
            reasons.append("QT momentum fading")
    if phase in {"REPAIRING", "SUPPORTED_PULLBACK"}:
        score += 20
    elif phase == "STABILIZING":
        score += 8
        reasons.append("QT stabilizing, needs confirmation")
    elif phase in {"EARLY_FALLING", "FADING"}:
        score -= 18
        reasons.append("QT phase still deteriorating")
    elif phase == "CHASING":
        score -= 14
        reasons.append("QT chasing phase")
    if finite(wait_score):
        if wait_score < 35:
            score += 8
        elif wait_score >= 65:
            score -= 12
            reasons.append("waiting has high value")
    if regime in {"EXTENDED", "FADING"}:
        score -= 4
    score = int(max(0, min(100, score)))
    if score >= 72:
        label = "QT_SUPPORT"
    elif score >= 52:
        label = "QT_NEUTRAL"
    else:
        label = "QT_BLOCK"
    return {
        "score": score,
        "label": label,
        "z": z,
        "z_delta": dz,
        "stretch_pctile": stretch_pctile,
        "abs_stretch_pctile": abs_stretch_pctile,
        "phase": phase,
        "wait_score": wait_score,
        "wait_label": wait_label,
        "confirmation": confirmation,
        "post_entry_state": post_entry,
        "regime": regime,
        "reasons": reasons[:5],
    }


def prime_tier(
    *,
    action_label: str,
    score: int,
    setup: str,
    warnings: list[str],
    blocks: list[str],
    visual: dict[str, Any],
    qt: dict[str, Any],
    hist: dict[str, Any],
) -> str:
    if action_label == "EVENT_BLOCKED" or any("Earnings within" in block for block in blocks):
        return "EVENT_BLOCKED"
    if action_label == "NO_TRADE":
        return "FAILED_STRUCTURE"
    historical_weak = any("Historical group/setup edge is not strong enough" in warning for warning in warnings)
    manual_only_blocks = all(
        block.startswith("Manual major-news") or block.startswith("Manual Nordnet")
        for block in blocks
    )
    path_risk = hist.get("nextopen_mae21_p10")
    severe_path = finite(path_risk) and float(path_risk) < -0.24
    phase_ok = qt.get("phase") in {"REPAIRING", "SUPPORTED_PULLBACK"} or qt.get("phase") is None
    wait_score = qt.get("wait_score")
    waiting_not_high = not finite(wait_score) or float(wait_score) < 65
    if (
        score >= 88
        and setup == "MLPB50"
        and visual.get("grade") == "CLEAN"
        and qt.get("label") == "QT_SUPPORT"
        and phase_ok
        and waiting_not_high
        and not historical_weak
        and not severe_path
        and manual_only_blocks
    ):
        return "A_PLUS_TRADE_CANDIDATE"
    if score >= 78 and visual.get("grade") in {"CLEAN", "MIXED"} and qt.get("label") != "QT_BLOCK":
        return "A_WATCH"
    if score >= 62:
        return "B_WATCH"
    return "FAILED_STRUCTURE"
