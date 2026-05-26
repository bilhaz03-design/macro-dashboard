#!/usr/bin/env python3
"""
Current MLPB trade gate.

Reads the broad final-falsification event set and grades the latest current
MLPB candidates. The goal is to separate "interesting signal" from "capital
review candidate".

The output is still not an automatic trade instruction. Broker spread/depth and
manual news checks remain hard blockers.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from mlpb_prime_utils import add_prime_features, prime_tier, qt_quality, visual_quality

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "data" / "mlpb_final_falsification_events.json"
CACHE_DIR = ROOT / "data" / "mlpb_broad_cache"
OUT = ROOT / "data" / f"mlpb_current_trade_gate_{datetime.now().strftime('%Y-%m-%d')}.txt"
JSON_OUT = ROOT / "data" / "mlpb_current_trade_gate.json"
LATEST_PERIOD = os.environ.get("SWING_TERMINAL_MLPB_LATEST_PERIOD", "8y")
CACHE_MAX_AGE_HOURS = float(os.environ.get("SWING_TERMINAL_MLPB_CACHE_MAX_AGE_HOURS", "8"))
YFINANCE_TIMEOUT_SECONDS = float(os.environ.get("SWING_TERMINAL_YFINANCE_TIMEOUT_SECONDS", "15"))


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+.2f}%"


def num(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:.{digits}f}"


def winsor_mean(s: pd.Series, q: float = 0.05) -> float:
    s = s.dropna()
    if len(s) == 0:
        return np.nan
    lo, hi = s.quantile(q), s.quantile(1 - q)
    return float(s.clip(lo, hi).mean())


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.replace('/', '_').replace('^', '')}.csv"


def cache_file_fresh(path: Path) -> bool:
    try:
        age_seconds = datetime.now().timestamp() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= CACHE_MAX_AGE_HOURS * 3600


def load_cached(ticker: str, *, require_fresh: bool = True) -> pd.DataFrame | None:
    path = cache_path(ticker)
    if not path.exists():
        return None
    if require_fresh and not cache_file_fresh(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
    except Exception:
        return None
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in df.columns for c in cols):
        return None
    df = df[cols].dropna().copy()
    df = df[df["Volume"] > 0].sort_index()
    return df if len(df) else None


def save_cached(ticker: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path(ticker), index_label="Date")


def normalize_ohlcv(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    raw = raw.copy()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0] for col in raw.columns]
    required = {"Open", "High", "Low", "Close", "Volume"}
    if required - set(raw.columns):
        return None
    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    df = df[df["Volume"] > 0]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df if len(df) else None


def extract_batch_ticker(raw: pd.DataFrame | None, ticker: str) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        first_level = raw.columns.get_level_values(0)
        second_level = raw.columns.get_level_values(1)
        if ticker in first_level:
            return raw[ticker]
        if ticker in second_level:
            return raw.xs(ticker, axis=1, level=1)
        return None
    return raw


def ensure_latest_caches(tickers: list[str]) -> dict:
    """Populate fresh latest-price caches for the current MLPB candidates."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    unique = sorted({str(ticker) for ticker in tickers if str(ticker)})
    pending = [ticker for ticker in unique if load_cached(ticker) is None]
    fetched: list[str] = []
    failed: list[str] = []
    if pending:
        try:
            raw = yf.download(
                pending,
                period=LATEST_PERIOD,
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=True,
                group_by="ticker",
                timeout=YFINANCE_TIMEOUT_SECONDS,
            )
        except Exception:
            raw = None
        for ticker in pending:
            df = normalize_ohlcv(extract_batch_ticker(raw, ticker))
            if df is not None and len(df) >= 260:
                save_cached(ticker, df)
                fetched.append(ticker)
            else:
                failed.append(ticker)
    available = [ticker for ticker in unique if load_cached(ticker) is not None]
    return {
        "requested": len(unique),
        "available": len(available),
        "fetched": fetched,
        "failed": failed,
        "period": LATEST_PERIOD,
    }


def latest_features(ticker: str, setup: str) -> dict:
    df = load_cached(ticker)
    if df is None or len(df) < 260:
        return {}
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    prev = close.shift(1)
    out = df.copy()
    for w in (21, 50, 126, 200):
        out[f"SMA{w}"] = close.rolling(w, min_periods=w).mean()
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    out["ATR14_PCT"] = tr.rolling(14, min_periods=14).mean() / close
    out["RSI14"] = rsi(close, 14)
    out["DOLLAR_VOL20"] = (close * out["Volume"]).rolling(20, min_periods=20).mean()
    out = add_prime_features(out)
    latest = out.iloc[-1]
    ma = int(setup.replace("MLPB", ""))
    sma = latest.get(f"SMA{ma}", np.nan)
    return {
        "latest_date": latest.name.date().isoformat(),
        "latest_close": float(latest["Close"]),
        "latest_dist_sma": float(latest["Close"] / sma - 1) if np.isfinite(sma) and sma > 0 else np.nan,
        "latest_atr14_pct": float(latest["ATR14_PCT"]),
        "latest_rsi14": float(latest["RSI14"]),
        "latest_dollar_vol20": float(latest["DOLLAR_VOL20"]),
        "latest_dd63": float(latest.get("DD63", np.nan)),
        "latest_clv": float(latest.get("CLV", np.nan)),
        "latest_range_atr_ratio": float(latest.get("RANGE_ATR_RATIO", np.nan)),
        "latest_body_atr_ratio": float(latest.get("BODY_ATR_RATIO", np.nan)),
        "latest_qt_z": float(latest.get("QT_Z", np.nan)),
        "latest_qt_z_delta": float(latest.get("QT_Z_DELTA", np.nan)),
        "latest_qt_stretch_pctile": float(latest.get("QT_STRETCH_PCTILE", np.nan)),
        "latest_qt_abs_stretch_pctile": float(latest.get("QT_ABS_STRETCH_PCTILE", np.nan)),
        "latest_qt_phase": str(latest.get("QT_PHASE", "UNKNOWN")),
        "latest_qt_wait_score": float(latest.get("QT_WAIT_SCORE", np.nan)),
        "latest_qt_wait_label": str(latest.get("QT_WAIT_LABEL", "UNKNOWN")),
        "latest_qt_confirmation": str(latest.get("QT_CONFIRMATION", "UNKNOWN")),
        "latest_qt_post_entry_state": str(latest.get("QT_POST_ENTRY_STATE", "UNKNOWN")),
        "latest_qt_regime": str(latest.get("QT_REGIME", "UNKNOWN")),
    }


def coerce_earnings_date(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        if pd.isna(raw):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(raw, dict):
        for key in ("Earnings Date", "EarningsDate", "earningsDate"):
            parsed = coerce_earnings_date(raw.get(key))
            if parsed:
                return parsed
        for value in raw.values():
            parsed = coerce_earnings_date(value)
            if parsed:
                return parsed
        return None
    if isinstance(raw, pd.DataFrame):
        for key in ("Earnings Date", "EarningsDate", "earningsDate"):
            if key in raw.index:
                parsed = coerce_earnings_date(raw.loc[key])
                if parsed:
                    return parsed
            if key in raw.columns:
                parsed = coerce_earnings_date(raw[key])
                if parsed:
                    return parsed
        for col in raw.columns:
            if "earn" in str(col).lower():
                parsed = coerce_earnings_date(raw[col])
                if parsed:
                    return parsed
        return None
    if isinstance(raw, pd.Series):
        for value in raw.dropna().tolist():
            parsed = coerce_earnings_date(value)
            if parsed:
                return parsed
        return None
    if isinstance(raw, (list, tuple, set, np.ndarray, pd.Index)):
        for value in raw:
            parsed = coerce_earnings_date(value)
            if parsed:
                return parsed
        return None
    if isinstance(raw, pd.Timestamp):
        if pd.isna(raw):
            return None
        return raw.date().isoformat()
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def next_earnings(ticker: str) -> str | None:
    try:
        cal = yf.Ticker(ticker).calendar
    except Exception:
        return None
    return coerce_earnings_date(cal)


def latest_gate_date(current: pd.DataFrame, latest_rows: list[dict]) -> str:
    latest_dates = []
    for row in latest_rows:
        raw = row.get("latest_date")
        if not raw:
            continue
        try:
            latest_dates.append(pd.to_datetime(raw).date())
        except Exception:
            continue
    if latest_dates:
        return max(latest_dates).isoformat()
    return max(pd.to_datetime(current["date"]).dt.date).isoformat()


def business_days_between(start: str, end: str) -> int:
    try:
        return int(np.busday_count(pd.to_datetime(start).date(), pd.to_datetime(end).date()))
    except Exception:
        return 999


def hist_stats(events: pd.DataFrame, cand: pd.Series) -> tuple[dict, str]:
    group = cand["group"]
    setup = cand["setup"]
    variant = cand["variant"]
    pools = [
        (events[(events["group"] == group) & (events["setup"] == setup) & (events["variant"] == variant)], "group/setup/variant"),
        (events[(events["group"] == group) & (events["setup"] == setup) & (events["variant"].isin(["QUALITY", "STRICT"]))], "group/setup/quality+strict"),
        (events[(events["setup"] == setup) & (events["variant"] == variant)], "all/setup/variant"),
        (events[(events["setup"] == setup) & (events["variant"].isin(["QUALITY", "STRICT"]))], "all/setup/quality+strict"),
    ]
    chosen, label = pools[-1]
    for pool, pool_label in pools:
        if len(pool) >= 25:
            chosen, label = pool, pool_label
            break
    if chosen.empty:
        return {"n": 0}, "none"
    return (
        {
            "n": int(len(chosen)),
            "nextopen21_mean": float(chosen["nextopen_fwd21"].mean()),
            "nextopen21_median": float(chosen["nextopen_fwd21"].median()),
            "nextopen21_winsor": winsor_mean(chosen["nextopen_fwd21"]),
            "nextopen21_hit": float((chosen["nextopen_fwd21"] > 0).mean()),
            "nextopen_mae21_p10": float(chosen["nextopen_mae21"].quantile(0.10)),
        },
        label,
    )


def score(cand: pd.Series, hist: dict, hist_source: str, latest: dict, latest_global_date: str, earnings_date: str | None) -> tuple[int, str, list[str], list[str]]:
    score_val = 0
    warnings: list[str] = []
    blocks: list[str] = []

    if cand["variant"] == "STRICT":
        score_val += 18
    elif cand["variant"] == "QUALITY":
        score_val += 8

    if cand["setup"] == "MLPB50":
        score_val += 22
    elif cand["setup"] == "MLPB21":
        score_val += 8

    if cand["market_regime"] == "RISK_ON":
        score_val += 12
    elif cand["market_regime"] == "RISK_OFF":
        score_val -= 12
        warnings.append("Market regime is risk-off.")

    n = hist.get("n", 0)
    if n >= 75:
        score_val += 12
    elif n >= 35:
        score_val += 8
    elif n >= 25:
        score_val += 4
    else:
        blocks.append("Historical sample below 25.")

    winsor = hist.get("nextopen21_winsor", np.nan)
    hit = hist.get("nextopen21_hit", np.nan)
    if winsor > 0.055 and hit > 0.58:
        score_val += 16
    elif winsor > 0.035 and hit > 0.55:
        score_val += 8
    else:
        warnings.append("Historical group/setup edge is not strong enough for capital-ready status.")

    latest_close = latest.get("latest_close", np.nan)
    if not np.isfinite(latest_close):
        score_val -= 30
        blocks.append("Latest price features missing.")
    since_signal = latest_close / float(cand["close"]) - 1 if np.isfinite(latest_close) else np.nan
    if np.isfinite(since_signal):
        if since_signal > 0.10:
            score_val -= 10
            warnings.append("Move since signal >10%; needs fresh base/retest.")
        elif since_signal < -0.08:
            score_val -= 10
            warnings.append("Signal is failing since trigger.")
        else:
            score_val += 8

    signal_age = business_days_between(cand["date"], latest_global_date)
    if signal_age <= 2:
        score_val += 8
    elif signal_age <= 5:
        score_val += 4
    else:
        score_val -= 5
        warnings.append("Signal is older than 5 trading days.")

    dist = latest.get("latest_dist_sma", np.nan)
    if np.isfinite(dist):
        if dist <= 0:
            score_val -= 10
            warnings.append("Price no longer reclaimed selected SMA.")
        elif dist <= 0.04:
            score_val += 10
        elif dist <= 0.08:
            score_val += 8
        elif dist <= 0.12:
            warnings.append("Extended 8-12% above selected SMA.")
        else:
            score_val -= 10
            warnings.append("Extended >12% above selected SMA.")

    atr = latest.get("latest_atr14_pct", np.nan)
    if np.isfinite(atr):
        if atr <= 0.05:
            score_val += 8
        elif atr <= 0.08:
            score_val += 5
        elif atr <= 0.12:
            score_val += 1
            warnings.append("High ATR; smaller size required.")
        else:
            score_val -= 10
            warnings.append("Very high ATR.")

    dvol = latest.get("latest_dollar_vol20", np.nan)
    if np.isfinite(dvol):
        if dvol >= 100_000_000:
            score_val += 8
        elif dvol >= 20_000_000:
            score_val += 4
        else:
            blocks.append("Dollar volume below $20M.")

    if earnings_date:
        days_to_earnings = business_days_between(latest_global_date, earnings_date)
        if 0 <= days_to_earnings <= 10:
            blocks.append(f"Earnings within 10 trading days ({earnings_date}).")
        elif days_to_earnings < 0:
            warnings.append(f"Calendar shows last/old earnings date ({earnings_date}); verify manually.")

    blocks.append("Manual major-news check required.")
    blocks.append("Manual Nordnet spread/depth check required.")

    score_val = int(max(0, min(100, score_val)))
    historical_weak = any("Historical group/setup edge is not strong enough" in w for w in warnings)
    price_lost_sma = any("Price no longer reclaimed selected SMA" in w for w in warnings)
    chase_risk = any("Move since signal >10%" in w for w in warnings)

    latest_missing = any("Latest price features missing" in b for b in blocks)
    if latest_missing:
        label = "NO_TRADE"
    elif any("Earnings within" in b for b in blocks):
        label = "EVENT_BLOCKED"
    elif price_lost_sma or chase_risk:
        label = "WATCH_PULLBACK" if score_val >= 62 else "NO_TRADE"
    elif score_val >= 80 and not historical_weak and not warnings:
        label = "TRADE_REVIEW_MANUAL_PENDING"
    elif score_val >= 62:
        label = "WATCH_PULLBACK"
    else:
        label = "NO_TRADE"
    return score_val, label, warnings, blocks


def main() -> int:
    data = json.loads(IN.read_text(encoding="utf-8"))
    events = pd.DataFrame(data["events"])
    current = pd.DataFrame(data["current_candidates"])
    if current.empty:
        OUT.write_text("No current candidates.\n", encoding="utf-8")
        return 0

    cache_stats = ensure_latest_caches(sorted(current["ticker"].unique()))
    print(
        "[mlpb_current_trade_gate] latest cache coverage: "
        f"{cache_stats['available']}/{cache_stats['requested']} "
        f"fetched={len(cache_stats['fetched'])} failed={len(cache_stats['failed'])}",
        flush=True,
    )
    prepared = [(cand, latest_features(cand["ticker"], cand["setup"])) for _, cand in current.iterrows()]
    latest_rows = [latest for _, latest in prepared]
    latest_with_price = sum(1 for latest in latest_rows if latest.get("latest_date"))
    latest_global_date = latest_gate_date(current, [latest for _, latest in prepared])
    earnings_cache = {ticker: next_earnings(ticker) for ticker in sorted(current["ticker"].unique())}
    rows = []
    for cand, latest in prepared:
        hist, hist_source = hist_stats(events, cand)
        earnings_date = earnings_cache.get(cand["ticker"])
        score_val, label, warnings, blocks = score(cand, hist, hist_source, latest, latest_global_date, earnings_date)
        latest_prime_row = {
            "dist_sma": latest.get("latest_dist_sma", np.nan),
            "atr14_pct": latest.get("latest_atr14_pct", np.nan),
            "rsi14": latest.get("latest_rsi14", np.nan),
            "dd63": latest.get("latest_dd63", np.nan),
            "clv": latest.get("latest_clv", np.nan),
            "range_atr_ratio": latest.get("latest_range_atr_ratio", np.nan),
            "body_atr_ratio": latest.get("latest_body_atr_ratio", np.nan),
            "qt_z": latest.get("latest_qt_z", np.nan),
            "qt_z_delta": latest.get("latest_qt_z_delta", np.nan),
            "qt_stretch_pctile": latest.get("latest_qt_stretch_pctile", np.nan),
            "qt_abs_stretch_pctile": latest.get("latest_qt_abs_stretch_pctile", np.nan),
            "qt_phase": latest.get("latest_qt_phase", "UNKNOWN"),
            "qt_wait_score": latest.get("latest_qt_wait_score", np.nan),
            "qt_wait_label": latest.get("latest_qt_wait_label", "UNKNOWN"),
            "qt_confirmation": latest.get("latest_qt_confirmation", "UNKNOWN"),
            "qt_post_entry_state": latest.get("latest_qt_post_entry_state", "UNKNOWN"),
            "qt_regime": latest.get("latest_qt_regime", "UNKNOWN"),
        }
        visual = visual_quality(latest_prime_row, cand["setup"])
        qt = qt_quality(latest_prime_row)
        tier = prime_tier(
            action_label=label,
            score=score_val,
            setup=str(cand["setup"]),
            warnings=warnings,
            blocks=blocks,
            visual=visual,
            qt=qt,
            hist=hist,
        )
        rows.append(
            {
                **cand.to_dict(),
                **latest,
                "earnings_date": earnings_date,
                "hist_source": hist_source,
                "hist": hist,
                "score": score_val,
                "label": label,
                "latest_visual_score": visual["score"],
                "latest_visual_grade": visual["grade"],
                "latest_visual_reasons": visual["reasons"],
                "latest_qt_score": qt["score"],
                "latest_qt_label": qt["label"],
                "latest_qt_reasons": qt["reasons"],
                "latest_qt_z": qt["z"],
                "latest_qt_z_delta": qt["z_delta"],
                "latest_qt_stretch_pctile": qt["stretch_pctile"],
                "latest_qt_abs_stretch_pctile": qt["abs_stretch_pctile"],
                "latest_qt_phase": qt["phase"],
                "latest_qt_wait_score": qt["wait_score"],
                "latest_qt_wait_label": qt["wait_label"],
                "latest_qt_confirmation": qt["confirmation"],
                "latest_qt_post_entry_state": qt["post_entry_state"],
                "latest_qt_regime": qt["regime"],
                "prime_tier": tier,
                "warnings": warnings,
                "blocks": blocks,
            }
        )

    out = pd.DataFrame(rows).sort_values(["score", "date", "ticker"], ascending=[False, False, True])
    JSON_OUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "latest_data_coverage": {
                    **cache_stats,
                    "candidate_rows": len(prepared),
                    "candidate_rows_with_latest": latest_with_price,
                },
                "candidates": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = []
    lines.append("# Current MLPB trade gate")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Latest price date used by gate: {latest_global_date}")
    lines.append(
        "Latest cache coverage: "
        f"{cache_stats['available']}/{cache_stats['requested']} tickers; "
        f"{latest_with_price}/{len(prepared)} candidate rows with latest features"
    )
    lines.append("")
    lines.append("## Candidates")
    table_rows = []
    for _, row in out.iterrows():
        hist = row["hist"]
        table_rows.append(
            {
                "ticker": row["ticker"],
                "date": row["date"],
                "group": row["group"],
                "setup": row["setup"],
                "variant": row["variant"],
                "score": row["score"],
                "label": row["label"],
                "prime": row.get("prime_tier", "-"),
                "visual": row.get("latest_visual_grade", "-"),
                "qt": row.get("latest_qt_label", "-"),
                "phase": row.get("latest_qt_phase", "-"),
                "wait": row.get("latest_qt_wait_label", "-"),
                "since": row["latest_close"] / row["close"] - 1 if np.isfinite(row.get("latest_close", np.nan)) else np.nan,
                "dist_now": row.get("latest_dist_sma", np.nan),
                "atr": row.get("latest_atr14_pct", np.nan),
                "rsi": row.get("latest_rsi14", np.nan),
                "hist_n": hist.get("n", 0),
                "hist_win": hist.get("nextopen21_winsor", np.nan),
                "hist_hit": hist.get("nextopen21_hit", np.nan),
                "earnings": row.get("earnings_date") or "-",
                "warnings": "; ".join(row["warnings"][:2]),
                "blocks": "; ".join(row["blocks"][:2]),
            }
        )
    table = pd.DataFrame(table_rows)
    for col in ["since", "dist_now", "atr", "hist_win", "hist_hit"]:
        table[col] = table[col].map(pct)
    table["rsi"] = table["rsi"].map(lambda x: num(x, 1))
    lines.append(table.to_string(index=False))
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- TRADE_REVIEW_MANUAL_PENDING means quant gates are good enough for manual review, not an automatic buy.")
    lines.append("- EVENT_BLOCKED means the setup can be valid but should not be entered without deciding how to handle earnings/news risk.")
    lines.append("- MLPB50 remains the preferred live pattern; MLPB21 needs stronger theme/regime support.")
    lines.append("- Manual news and Nordnet spread/depth checks are always blocking by design.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Wrote {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
