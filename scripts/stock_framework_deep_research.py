#!/usr/bin/env python3
"""
stock_framework_deep_research.py — broader stock-framework stress test.

This is a research-only second pass. It tests whether the ETF/Index RankINT
framework can become a stock-satellite scanner after adding market breadth,
matched random baselines, path-risk gates, and OOS/regime splits.
"""

from __future__ import annotations

import json
import math
import os
import sys
import warnings
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rank_int import Z_P70, Z_P85, rolling_rank_int  # noqa: E402

OUT = ROOT / "data" / f"stock_framework_deep_research_{datetime.now().strftime('%Y-%m-%d')}.txt"
EVENTS_OUT = ROOT / "data" / "stock_framework_deep_events.json"
CURRENT_OUT = ROOT / "data" / "stock_framework_deep_current_signals.json"
CACHE_DIR = ROOT / "data" / "stock_cache"

RANK_WINDOW = 1260
MIN_PERIODS = 252
VOL_WINDOW = 63
DEDUP_DAYS = 21
FWD_DAYS = (5, 21, 63)
CURRENT_LOOKBACK_BARS = 10
BASELINE_ITERS = 1000
CACHE_MAX_AGE_HOURS = float(os.environ.get("SWING_TERMINAL_STOCK_CACHE_MAX_AGE_HOURS", "6"))


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    market: str
    group: str


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+.2f}%"


def num(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:.{digits}f}"


def universe() -> tuple[Stock, ...]:
    china_adr = (
        Stock("BABA", "Alibaba ADR", "US", "China ADR"),
        Stock("JD", "JD.com ADR", "US", "China ADR"),
        Stock("PDD", "PDD Holdings ADR", "US", "China ADR"),
        Stock("BIDU", "Baidu ADR", "US", "China ADR"),
        Stock("NTES", "NetEase ADR", "US", "China ADR"),
        Stock("TCOM", "Trip.com ADR", "US", "China ADR"),
        Stock("TME", "Tencent Music ADR", "US", "China ADR"),
        Stock("BILI", "Bilibili ADR", "US", "China ADR"),
        Stock("BEKE", "KE Holdings ADR", "US", "China ADR"),
        Stock("ZTO", "ZTO Express ADR", "US", "China ADR"),
        Stock("YUMC", "Yum China", "US", "China ADR"),
        Stock("LI", "Li Auto ADR", "US", "China ADR"),
        Stock("NIO", "NIO ADR", "US", "China ADR"),
        Stock("XPEV", "XPeng ADR", "US", "China ADR"),
        Stock("FUTU", "Futu Holdings ADR", "US", "China ADR"),
        Stock("YMM", "Full Truck Alliance ADR", "US", "China ADR"),
        Stock("EDU", "New Oriental ADR", "US", "China ADR"),
        Stock("TAL", "TAL Education ADR", "US", "China ADR"),
    )
    hk = (
        Stock("0700.HK", "Tencent", "HK", "China HK"),
        Stock("9988.HK", "Alibaba HK", "HK", "China HK"),
        Stock("3690.HK", "Meituan", "HK", "China HK"),
        Stock("9618.HK", "JD.com HK", "HK", "China HK"),
        Stock("9888.HK", "Baidu HK", "HK", "China HK"),
        Stock("9999.HK", "NetEase HK", "HK", "China HK"),
        Stock("1211.HK", "BYD", "HK", "China HK"),
        Stock("1810.HK", "Xiaomi", "HK", "China HK"),
        Stock("1024.HK", "Kuaishou", "HK", "China HK"),
        Stock("2318.HK", "Ping An", "HK", "China HK"),
        Stock("0883.HK", "CNOOC", "HK", "China HK"),
        Stock("0941.HK", "China Mobile", "HK", "China HK"),
        Stock("2020.HK", "ANTA Sports", "HK", "China HK"),
        Stock("9992.HK", "Pop Mart", "HK", "China HK"),
        Stock("2015.HK", "Li Auto HK", "HK", "China HK"),
        Stock("9866.HK", "NIO HK", "HK", "China HK"),
        Stock("9868.HK", "XPeng HK", "HK", "China HK"),
    )
    us = (
        Stock("AAPL", "Apple", "US", "US mega"),
        Stock("MSFT", "Microsoft", "US", "US mega"),
        Stock("NVDA", "NVIDIA", "US", "US mega"),
        Stock("AMZN", "Amazon", "US", "US mega"),
        Stock("GOOGL", "Alphabet", "US", "US mega"),
        Stock("META", "Meta", "US", "US mega"),
        Stock("TSLA", "Tesla", "US", "US mega"),
        Stock("AVGO", "Broadcom", "US", "US mega"),
        Stock("AMD", "AMD", "US", "US large"),
        Stock("NFLX", "Netflix", "US", "US large"),
        Stock("CRM", "Salesforce", "US", "US large"),
        Stock("ORCL", "Oracle", "US", "US large"),
        Stock("ADBE", "Adobe", "US", "US large"),
        Stock("NOW", "ServiceNow", "US", "US large"),
        Stock("JPM", "JPMorgan", "US", "US financials"),
        Stock("BAC", "Bank of America", "US", "US financials"),
        Stock("GS", "Goldman Sachs", "US", "US financials"),
        Stock("MS", "Morgan Stanley", "US", "US financials"),
        Stock("V", "Visa", "US", "US quality"),
        Stock("MA", "Mastercard", "US", "US quality"),
        Stock("COST", "Costco", "US", "US quality"),
        Stock("HD", "Home Depot", "US", "US quality"),
        Stock("WMT", "Walmart", "US", "US defensive"),
        Stock("PG", "Procter & Gamble", "US", "US defensive"),
        Stock("KO", "Coca-Cola", "US", "US defensive"),
        Stock("PEP", "PepsiCo", "US", "US defensive"),
        Stock("MCD", "McDonald's", "US", "US defensive"),
        Stock("NKE", "Nike", "US", "US consumer"),
        Stock("SBUX", "Starbucks", "US", "US consumer"),
        Stock("CAT", "Caterpillar", "US", "US industrials"),
        Stock("DE", "Deere", "US", "US industrials"),
        Stock("GE", "GE Aerospace", "US", "US industrials"),
        Stock("UNH", "UnitedHealth", "US", "US healthcare"),
        Stock("LLY", "Eli Lilly", "US", "US healthcare"),
        Stock("MRK", "Merck", "US", "US healthcare"),
        Stock("ABBV", "AbbVie", "US", "US healthcare"),
        Stock("XOM", "Exxon Mobil", "US", "US energy"),
        Stock("CVX", "Chevron", "US", "US energy"),
        Stock("COP", "ConocoPhillips", "US", "US energy"),
    )
    sweden = (
        Stock("VOLV-B.ST", "Volvo B", "SE", "Sweden large"),
        Stock("INVE-B.ST", "Investor B", "SE", "Sweden large"),
        Stock("ATCO-A.ST", "Atlas Copco A", "SE", "Sweden large"),
        Stock("ATCO-B.ST", "Atlas Copco B", "SE", "Sweden large"),
        Stock("ERIC-B.ST", "Ericsson B", "SE", "Sweden large"),
        Stock("HM-B.ST", "H&M B", "SE", "Sweden large"),
        Stock("SEB-A.ST", "SEB A", "SE", "Sweden banks"),
        Stock("SWED-A.ST", "Swedbank A", "SE", "Sweden banks"),
        Stock("SHB-A.ST", "Handelsbanken A", "SE", "Sweden banks"),
        Stock("NDA-SE.ST", "Nordea SE", "SE", "Sweden banks"),
        Stock("ASSA-B.ST", "Assa Abloy B", "SE", "Sweden industrials"),
        Stock("SAND.ST", "Sandvik", "SE", "Sweden industrials"),
        Stock("TEL2-B.ST", "Tele2 B", "SE", "Sweden defensive"),
        Stock("ELUX-B.ST", "Electrolux B", "SE", "Sweden cyclicals"),
        Stock("HEXA-B.ST", "Hexagon B", "SE", "Sweden industrials"),
        Stock("ALFA.ST", "Alfa Laval", "SE", "Sweden industrials"),
        Stock("AZN.ST", "AstraZeneca Stockholm", "SE", "Sweden healthcare"),
        Stock("SKF-B.ST", "SKF B", "SE", "Sweden industrials"),
        Stock("ESSITY-B.ST", "Essity B", "SE", "Sweden defensive"),
        Stock("BOL.ST", "Boliden", "SE", "Sweden materials"),
    )
    europe = (
        Stock("ASML.AS", "ASML", "EU", "Europe quality"),
        Stock("SAP.DE", "SAP", "EU", "Europe quality"),
        Stock("SIE.DE", "Siemens", "EU", "Europe industrials"),
        Stock("AIR.PA", "Airbus", "EU", "Europe industrials"),
        Stock("MC.PA", "LVMH", "EU", "Europe quality"),
        Stock("OR.PA", "L'Oreal", "EU", "Europe defensive"),
        Stock("RMS.PA", "Hermes", "EU", "Europe quality"),
        Stock("NESN.SW", "Nestle", "EU", "Europe defensive"),
        Stock("NOVN.SW", "Novartis", "EU", "Europe healthcare"),
        Stock("RO.SW", "Roche", "EU", "Europe healthcare"),
        Stock("SHEL.L", "Shell", "EU", "Europe energy"),
        Stock("BP.L", "BP", "EU", "Europe energy"),
        Stock("ULVR.L", "Unilever", "EU", "Europe defensive"),
        Stock("HSBA.L", "HSBC", "EU", "Europe financials"),
    )
    return china_adr + hk + us + sweden + europe


def cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("^", "")
    return CACHE_DIR / f"{safe}.csv"


def cache_file_fresh(path: Path) -> bool:
    try:
        age_seconds = datetime.now().timestamp() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= CACHE_MAX_AGE_HOURS * 3600


def fetch_ohlcv(ticker: str) -> pd.DataFrame | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(ticker)
    if path.exists():
        try:
            cached = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
            if not cached.empty and cache_file_fresh(path):
                return cached
        except Exception:
            pass
    raw = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=True, threads=False)
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    required = {"Open", "High", "Low", "Close", "Volume"}
    if required - set(raw.columns):
        return None
    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    df = df[df["Volume"] > 0]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(path, index_label="Date")
    return df


def wilder_rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_clv(df: pd.DataFrame) -> pd.Series:
    rng = df["High"] - df["Low"]
    return pd.Series(np.where(rng == 0, 0.0, (2 * df["Close"] - df["High"] - df["Low"]) / rng), index=df.index)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    for w in (21, 52, 126, 252):
        out[f"SMA{w}"] = close.rolling(w, min_periods=w).mean()
    out["RSI9"] = wilder_rsi(close, 9)
    out["dist21"] = close / out["SMA21"] - 1
    out["dist126"] = close / out["SMA126"] - 1
    out["dist252"] = close / out["SMA252"] - 1
    clv = compute_clv(out)
    vol = out["Volume"].astype(float)
    rvol = vol / vol.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    log_vol = np.log(vol.replace(0, np.nan))
    log_z = (
        (log_vol - log_vol.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean())
        / log_vol.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std().replace(0, np.nan)
    ).clip(lower=0)
    out["DirRVOL63"] = rvol * clv
    out["DirLogVolZ63"] = log_z * clv
    out["ri_rsi"] = rolling_rank_int(out["RSI9"], RANK_WINDOW, MIN_PERIODS)
    out["ri_dist21"] = rolling_rank_int(out["dist21"], RANK_WINDOW, MIN_PERIODS)
    out["ri_dist126"] = rolling_rank_int(out["dist126"], RANK_WINDOW, MIN_PERIODS)
    out["ri_dist252"] = rolling_rank_int(out["dist252"], RANK_WINDOW, MIN_PERIODS)
    out["ri_dirrvol"] = rolling_rank_int(out["DirRVOL63"], RANK_WINDOW, MIN_PERIODS)
    out["ri_logvolz"] = rolling_rank_int(out["DirLogVolZ63"], RANK_WINDOW, MIN_PERIODS)
    prev_close = close.shift(1)
    tr = pd.concat([(out["High"] - out["Low"]).abs(), (out["High"] - prev_close).abs(), (out["Low"] - prev_close).abs()], axis=1).max(axis=1)
    out["atr21_pct"] = tr.rolling(21, min_periods=21).mean() / close
    out["gap_abs"] = (out["Open"] / prev_close - 1).abs()
    out["gap_p95_252"] = out["gap_abs"].rolling(252, min_periods=126).quantile(0.95)
    out["vol63_ann"] = close.pct_change().rolling(63, min_periods=63).std() * math.sqrt(252)
    out["bull_stack"] = (close > out["SMA21"]) & (out["SMA21"] > out["SMA52"]) & (out["SMA52"] > out["SMA126"]) & (out["SMA126"] > out["SMA252"])
    out["sma126_gt_252"] = out["SMA126"] > out["SMA252"]
    out["rsi9_delta_1d"] = out["RSI9"].diff(1)
    out["rsi9_delta_5d"] = out["RSI9"].diff(5)
    return out


def signal_specs(df: pd.DataFrame) -> dict[str, tuple[pd.Series, pd.Series]]:
    cap = (df["ri_dirrvol"] <= -Z_P85) & (df["ri_rsi"] <= -Z_P85) & (df["ri_dist252"] <= -Z_P85)
    pb21 = df["bull_stack"] & (df["ri_dirrvol"] >= Z_P70) & (df["ri_rsi"] <= -Z_P70) & (df["ri_dist21"] <= -Z_P70)
    pb126 = df["sma126_gt_252"] & (df["ri_dirrvol"] >= Z_P70) & (df["ri_rsi"] <= -Z_P70) & (df["ri_dist126"] <= -Z_P70)
    low_gap = df["gap_p95_252"] <= 0.08
    low_vol = df["vol63_ann"] <= 0.55
    rebound = df["rsi9_delta_1d"] > 0
    logvol_confirm = df["ri_logvolz"] <= -Z_P70
    all_valid = pd.Series(True, index=df.index)
    return {
        "CAP": (cap, all_valid),
        "CAP_BULL126": (cap & df["sma126_gt_252"], df["sma126_gt_252"]),
        "CAP_LOGVOL": (cap & logvol_confirm, all_valid),
        "CAP_REBOUND": (cap & rebound, all_valid),
        "PB21": (pb21, df["bull_stack"]),
        "PB126": (pb126, df["sma126_gt_252"]),
        "PB126_LOWGAP": (pb126 & low_gap, df["sma126_gt_252"] & low_gap),
        "PB126_LOWVOL": (pb126 & low_vol, df["sma126_gt_252"] & low_vol),
        "PB126_REBOUND": (pb126 & rebound, df["sma126_gt_252"]),
    }


def dedup(mask: pd.Series) -> pd.Series:
    out = pd.Series(False, index=mask.index)
    last = None
    for dt, ok in mask.fillna(False).items():
        if not ok:
            continue
        if last is None or (dt - last).days >= DEDUP_DAYS:
            out.loc[dt] = True
            last = dt
    return out


def labeled_events(df: pd.DataFrame, stock: Stock, signal: str, mask: pd.Series) -> list[dict]:
    close = df["Close"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    pos_by_idx = {idx: pos for pos, idx in enumerate(df.index)}
    rows: list[dict] = []
    for idx in df.index[dedup(mask)]:
        pos = pos_by_idx[idx]
        entry = close[pos]
        if not np.isfinite(entry) or entry <= 0:
            continue
        row = {
            "ticker": stock.ticker,
            "name": stock.name,
            "market": stock.market,
            "group": stock.group,
            "signal": signal,
            "date": idx.date().isoformat(),
            "entry": float(entry),
            "ri_rsi": float(df.loc[idx, "ri_rsi"]) if np.isfinite(df.loc[idx, "ri_rsi"]) else np.nan,
            "ri_dirrvol": float(df.loc[idx, "ri_dirrvol"]) if np.isfinite(df.loc[idx, "ri_dirrvol"]) else np.nan,
            "gap_p95_252": float(df.loc[idx, "gap_p95_252"]) if np.isfinite(df.loc[idx, "gap_p95_252"]) else np.nan,
            "vol63_ann": float(df.loc[idx, "vol63_ann"]) if np.isfinite(df.loc[idx, "vol63_ann"]) else np.nan,
            "rsi9_delta_1d": float(df.loc[idx, "rsi9_delta_1d"]) if np.isfinite(df.loc[idx, "rsi9_delta_1d"]) else np.nan,
            "rsi9_delta_5d": float(df.loc[idx, "rsi9_delta_5d"]) if np.isfinite(df.loc[idx, "rsi9_delta_5d"]) else np.nan,
        }
        ok = True
        for h in FWD_DAYS:
            if pos + h >= len(close) or not np.isfinite(close[pos + h]):
                ok = False
                break
            row[f"fwd{h}"] = float(close[pos + h] / entry - 1)
            row[f"mae{h}"] = float(np.nanmin(low[pos + 1 : pos + h + 1]) / entry - 1)
            row[f"mfe{h}"] = float(np.nanmax(high[pos + 1 : pos + h + 1]) / entry - 1)
        if ok:
            rows.append(row)
    return rows


def current_events(df: pd.DataFrame, stock: Stock, signal: str, mask: pd.Series) -> list[dict]:
    rows: list[dict] = []
    for idx in df.index[-CURRENT_LOOKBACK_BARS:]:
        if not bool(mask.reindex([idx]).fillna(False).iloc[0]):
            continue
        rows.append(
            {
                "ticker": stock.ticker,
                "name": stock.name,
                "market": stock.market,
                "group": stock.group,
                "signal": signal,
                "date": idx.date().isoformat(),
                "entry": float(df.loc[idx, "Close"]),
                "ri_rsi": float(df.loc[idx, "ri_rsi"]) if np.isfinite(df.loc[idx, "ri_rsi"]) else np.nan,
                "ri_dirrvol": float(df.loc[idx, "ri_dirrvol"]) if np.isfinite(df.loc[idx, "ri_dirrvol"]) else np.nan,
                "gap_p95_252": float(df.loc[idx, "gap_p95_252"]) if np.isfinite(df.loc[idx, "gap_p95_252"]) else np.nan,
                "vol63_ann": float(df.loc[idx, "vol63_ann"]) if np.isfinite(df.loc[idx, "vol63_ann"]) else np.nan,
            }
        )
    return rows


def returns_for_positions(df: pd.DataFrame, eligible: pd.Series) -> np.ndarray:
    close = df["Close"].to_numpy(dtype=float)
    valid_last = len(close) - 22
    pos = np.flatnonzero(eligible.fillna(False).to_numpy()) if len(eligible) else np.array([], dtype=int)
    pos = pos[(pos >= 252) & (pos < valid_last)]
    out = []
    for p in pos:
        entry = close[p]
        if np.isfinite(entry) and entry > 0 and np.isfinite(close[p + 21]):
            out.append(close[p + 21] / entry - 1)
    return np.array(out, dtype=float)


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    out: dict[str, float | int | tuple[float, float]] = {"n": len(rows)}
    for h in FWD_DAYS:
        vals = np.array([r[f"fwd{h}"] for r in rows], dtype=float)
        mae = np.array([r[f"mae{h}"] for r in rows], dtype=float)
        out[f"fwd{h}_mean"] = float(np.mean(vals))
        out[f"fwd{h}_median"] = float(np.median(vals))
        out[f"fwd{h}_hit"] = float(np.mean(vals > 0))
        out[f"mae{h}_p10"] = float(np.percentile(mae, 10))
        out[f"mae{h}_median"] = float(np.median(mae))
    vals21 = np.array([r["fwd21"] for r in rows], dtype=float)
    rng = np.random.default_rng(42)
    if len(vals21) >= 5:
        boot = np.array([np.mean(rng.choice(vals21, len(vals21), replace=True)) for _ in range(3000)])
        out["fwd21_ci"] = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))
    else:
        out["fwd21_ci"] = (np.nan, np.nan)
    mae21 = np.array([r["mae21"] for r in rows], dtype=float)
    out["shake10"] = float(np.mean(mae21 <= -0.10))
    out["shake20"] = float(np.mean(mae21 <= -0.20))
    return out


def matched_random(rows: list[dict], pools: dict[tuple[str, str], np.ndarray], seed_key: str) -> dict:
    if len(rows) < 3:
        return {"mean": np.nan, "p_ge_obs": np.nan, "p95": np.nan}
    observed = float(np.mean([r["fwd21"] for r in rows]))
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["ticker"], r["signal"])
        counts[key] = counts.get(key, 0) + 1
    rng = np.random.default_rng(zlib.crc32(seed_key.encode("utf-8")))
    means = []
    for _ in range(BASELINE_ITERS):
        sample = []
        for key, n in counts.items():
            pool = pools.get(key, np.array([], dtype=float))
            if len(pool) == 0:
                continue
            sample.extend(rng.choice(pool, n, replace=True))
        if sample:
            means.append(float(np.mean(sample)))
    arr = np.array(means, dtype=float)
    return {
        "mean": float(np.mean(arr)) if len(arr) else np.nan,
        "p_ge_obs": float(np.mean(arr >= observed)) if len(arr) else np.nan,
        "p95": float(np.percentile(arr, 95)) if len(arr) else np.nan,
    }


def verdict(s: dict, rand: dict) -> str:
    n = int(s.get("n", 0))
    mean = float(s.get("fwd21_mean", np.nan))
    hit = float(s.get("fwd21_hit", np.nan))
    ci_low = float(s.get("fwd21_ci", (np.nan, np.nan))[0])
    mae = float(s.get("mae21_p10", np.nan))
    shake20 = float(s.get("shake20", np.nan))
    lift = mean - float(rand.get("mean", np.nan))
    p_random = float(rand.get("p_ge_obs", np.nan))
    if np.isfinite(mae) and mae <= -0.25:
        return "BLOCK_PATH_RISK"
    if n >= 30 and mean >= 0.03 and hit >= 0.58 and ci_low > 0 and lift >= 0.01 and p_random <= 0.05 and shake20 <= 0.12:
        return "RESEARCH_PASS"
    if n >= 20 and mean >= 0.025 and hit >= 0.55 and lift > 0 and p_random <= 0.20 and mae > -0.22:
        return "WATCHLIST"
    return "NO_EDGE"


def split_summary(rows: list[dict]) -> list[tuple[str, dict]]:
    splits = [("pre2020", None, "2019-12-31"), ("2020-2022", "2020-01-01", "2022-12-31"), ("2023+", "2023-01-01", None)]
    out = []
    for label, start, end in splits:
        rr = []
        for r in rows:
            if start and r["date"] < start:
                continue
            if end and r["date"] > end:
                continue
            rr.append(r)
        out.append((label, summarise(rr)))
    return out


def main() -> int:
    stocks = universe()
    feature_by_ticker: dict[str, pd.DataFrame] = {}
    pools: dict[tuple[str, str], np.ndarray] = {}
    events: list[dict] = []
    current: list[dict] = []
    coverage: list[dict] = []
    lines: list[str] = []

    for i, stock in enumerate(stocks, 1):
        print(f"[{i:03d}/{len(stocks)}] {stock.ticker}", flush=True)
        df = fetch_ohlcv(stock.ticker)
        if df is None or len(df) < 756:
            coverage.append({"ticker": stock.ticker, "name": stock.name, "market": stock.market, "group": stock.group, "status": "FAIL", "bars": 0 if df is None else len(df)})
            continue
        feat = add_features(df)
        feature_by_ticker[stock.ticker] = feat
        coverage.append(
            {
                "ticker": stock.ticker,
                "name": stock.name,
                "market": stock.market,
                "group": stock.group,
                "status": "OK",
                "bars": len(feat),
                "start": feat.index.min().date().isoformat(),
                "end": feat.index.max().date().isoformat(),
                "median_vol": float(feat["Volume"].tail(1260).median()),
            }
        )
        specs = signal_specs(feat)
        for signal, (mask, eligible) in specs.items():
            rows = labeled_events(feat, stock, signal, mask)
            events.extend(rows)
            current.extend(current_events(feat, stock, signal, mask))
            pools[(stock.ticker, signal)] = returns_for_positions(feat, eligible)

    lines.append("# Stock Framework Deep Research")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"Universe: {len(stocks)} liquid large-cap stocks across China ADR/HK, US, Sweden, and Europe.")
    lines.append(f"Labeled events: {len(events)}. Baseline: {BASELINE_ITERS} matched random simulations per row-set.")
    lines.append("")

    lines.append("## Signal stress test")
    lines.append("")
    lines.append("| Signal | N | fwd21 | Random21 | Lift | p(random>=obs) | Hit | CI95 | MAE21 p10 | Shake20 | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for signal in ("CAP", "CAP_BULL126", "CAP_LOGVOL", "CAP_REBOUND", "PB21", "PB126", "PB126_LOWGAP", "PB126_LOWVOL", "PB126_REBOUND"):
        rows = [r for r in events if r["signal"] == signal]
        s = summarise(rows)
        rand = matched_random(rows, pools, f"signal:{signal}")
        ci = s.get("fwd21_ci", (np.nan, np.nan))
        lift = s.get("fwd21_mean", np.nan) - rand.get("mean", np.nan)
        lines.append(
            f"| {signal} | {s.get('n', 0)} | {pct(s.get('fwd21_mean'))} | {pct(rand.get('mean'))} | {pct(lift)} | "
            f"{num(rand.get('p_ge_obs'), 3)} | {pct(s.get('fwd21_hit'))} | [{pct(ci[0])}, {pct(ci[1])}] | "
            f"{pct(s.get('mae21_p10'))} | {pct(s.get('shake20'))} | {verdict(s, rand)} |"
        )

    lines.append("")
    lines.append("## Market and group stability")
    lines.append("")
    lines.append("| Scope | Signal | N | fwd21 | Random21 | Lift | p_rand | Hit | MAE21 p10 | Verdict |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    group_rows = []
    for key in ("market", "group"):
        for value in sorted({r[key] for r in events}):
            for signal in ("CAP", "CAP_BULL126", "PB126", "PB126_LOWGAP", "PB126_LOWVOL", "PB126_REBOUND"):
                rows = [r for r in events if r[key] == value and r["signal"] == signal]
                if len(rows) < 20:
                    continue
                s = summarise(rows)
                rand = matched_random(rows, pools, f"{key}:{value}:{signal}")
                lift = s.get("fwd21_mean", np.nan) - rand.get("mean", np.nan)
                group_rows.append((lift, value, signal, s, rand, key))
    for _, value, signal, s, rand, key in sorted(group_rows, reverse=True)[:50]:
        lift = s.get("fwd21_mean", np.nan) - rand.get("mean", np.nan)
        lines.append(
            f"| {key}:{value} | {signal} | {s.get('n', 0)} | {pct(s.get('fwd21_mean'))} | {pct(rand.get('mean'))} | "
            f"{pct(lift)} | {num(rand.get('p_ge_obs'), 3)} | {pct(s.get('fwd21_hit'))} | "
            f"{pct(s.get('mae21_p10'))} | {verdict(s, rand)} |"
        )

    lines.append("")
    lines.append("## Best ticker/signal candidates")
    lines.append("")
    lines.append("| Ticker | Name | Signal | N | fwd21 | Random21 | Lift | p_rand | Hit | CI95 low | MAE21 p10 | Shake20 | Verdict |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    combo_rows = []
    for ticker in sorted({r["ticker"] for r in events}):
        for signal in ("CAP", "CAP_BULL126", "CAP_LOGVOL", "CAP_REBOUND", "PB126", "PB126_LOWGAP", "PB126_LOWVOL", "PB126_REBOUND"):
            rows = [r for r in events if r["ticker"] == ticker and r["signal"] == signal]
            if len(rows) < 10:
                continue
            s = summarise(rows)
            rand = matched_random(rows, pools, f"combo:{ticker}:{signal}")
            lift = s.get("fwd21_mean", np.nan) - rand.get("mean", np.nan)
            combo_rows.append((verdict(s, rand), lift, s.get("fwd21_mean", -999), ticker, signal, s, rand, rows[0]["name"]))
    verdict_rank = {"RESEARCH_PASS": 3, "WATCHLIST": 2, "NO_EDGE": 1, "BLOCK_PATH_RISK": 0}
    combo_rows.sort(key=lambda x: (verdict_rank.get(x[0], -1), x[1], x[2]), reverse=True)
    for v, lift, _, ticker, signal, s, rand, name in combo_rows[:40]:
        ci = s.get("fwd21_ci", (np.nan, np.nan))
        lines.append(
            f"| {ticker} | {name} | {signal} | {s.get('n', 0)} | {pct(s.get('fwd21_mean'))} | {pct(rand.get('mean'))} | "
            f"{pct(lift)} | {num(rand.get('p_ge_obs'), 3)} | {pct(s.get('fwd21_hit'))} | {pct(ci[0])} | "
            f"{pct(s.get('mae21_p10'))} | {pct(s.get('shake20'))} | {v} |"
        )

    lines.append("")
    lines.append("## Temporal split for best candidates")
    lines.append("")
    lines.append("| Ticker | Signal | Split | N | fwd21 | Hit | CI95 low | MAE21 p10 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for v, _, _, ticker, signal, _, _, _ in combo_rows[:15]:
        rows = [r for r in events if r["ticker"] == ticker and r["signal"] == signal]
        for label, s in split_summary(rows):
            ci = s.get("fwd21_ci", (np.nan, np.nan))
            lines.append(f"| {ticker} | {signal} | {label} | {s.get('n', 0)} | {pct(s.get('fwd21_mean'))} | {pct(s.get('fwd21_hit'))} | {pct(ci[0])} | {pct(s.get('mae21_p10'))} |")

    lines.append("")
    lines.append("## Current raw signals")
    lines.append("")
    lines.append(f"Current = latest {CURRENT_LOOKBACK_BARS} bars; no forward labels. Use only as research/paper-tracking until approved.")
    lines.append("")
    lines.append("| Date | Ticker | Name | Group | Signal | Entry | ri_RSI | ri_DirRVOL | gap95 | vol63 |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|")
    for r in sorted(current, key=lambda x: (x["date"], x["ticker"], x["signal"]), reverse=True)[:80]:
        lines.append(
            f"| {r['date']} | {r['ticker']} | {r['name']} | {r['group']} | {r['signal']} | {num(r['entry'])} | "
            f"{num(r['ri_rsi'], 3)} | {num(r['ri_dirrvol'], 3)} | {pct(r.get('gap_p95_252'))} | {pct(r.get('vol63_ann'))} |"
        )

    lines.append("")
    lines.append("## Interim rule")
    lines.append("")
    lines.append("- Broad single-stock use is blocked unless a combo clears matched random, path risk, and temporal split checks.")
    lines.append("- The stock scanner can exist as a research scanner; live use requires whitelist + earnings/spread/liquidity gate.")
    lines.append("- A 95% confidence claim can apply to this rejection/gating decision, not to any single trade outcome.")

    OUT.write_text("\n".join(lines) + "\n")
    EVENTS_OUT.write_text(json.dumps(events, indent=2))
    CURRENT_OUT.write_text(json.dumps(current, indent=2))
    print(f"Wrote {OUT}")
    print(f"Events: {len(events)}")
    print(f"Current: {len(current)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
