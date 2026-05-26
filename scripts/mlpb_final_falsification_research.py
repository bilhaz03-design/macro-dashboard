#!/usr/bin/env python3
"""
MLPB final falsification research.

This is the "try to kill it" pass for Momentum Leader Pullback:
- broader liquid US universe from current S&P 500 + Nasdaq 100 constituents
- previous theme tickers added explicitly
- matched random leader-day baseline
- realistic next-open entry check
- market-regime split
- filter stress: ATR, distance from SMA, RSI, dollar volume

Important limitation: current-index membership creates survivorship bias. This
script is not a proof of future profitability; it is a falsification layer used
to decide whether MLPB deserves a terminal scanner slot.
"""

from __future__ import annotations

import json
import math
import os
import warnings
import zlib
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from mlpb_prime_utils import add_prime_features, qt_quality, visual_quality

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "mlpb_broad_cache"
OUT = ROOT / "data" / f"mlpb_final_falsification_{datetime.now().strftime('%Y-%m-%d')}.txt"
JSON_OUT = ROOT / "data" / "mlpb_final_falsification_events.json"

BENCHMARK = "SPY"
MARKET_TICKERS = ("SPY", "QQQ", "SMH")
FWD_DAYS = (5, 10, 21, 42)
MAX_FWD = max(FWD_DAYS)
DEDUP_BARS = 15
MIN_HISTORY = 360
BASELINE_ITERS = int(os.environ.get("MLPB_FINAL_BASELINE_ITERS", "250"))
CACHE_MAX_AGE_HOURS = float(os.environ.get("MLPB_FINAL_CACHE_MAX_AGE_HOURS", "24"))


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    group: str
    source: str


THEME_OVERRIDES: dict[str, str] = {
    # AI infra / memory-optics
    "SNDK": "AI infra / memory-optics",
    "MU": "AI infra / memory-optics",
    "AAOI": "AI infra / memory-optics",
    "AXTI": "AI infra / memory-optics",
    "WDC": "AI infra / memory-optics",
    "STX": "AI infra / memory-optics",
    "COHR": "AI infra / memory-optics",
    "LITE": "AI infra / memory-optics",
    "FN": "AI infra / memory-optics",
    "CIEN": "AI infra / memory-optics",
    # AI adjacent
    "ANET": "AI infra / networking",
    "MRVL": "AI infra / networking",
    "AVGO": "AI infra / networking",
    "NVDA": "AI infra / compute",
    "SMCI": "AI infra / compute",
    "VRT": "AI infra / power-cooling",
    "DELL": "AI infra / compute",
    "HPE": "AI infra / compute",
    "TSM": "AI infra / semiconductors",
    "ASML": "AI infra / semiconductors",
    "ARM": "AI infra / semiconductors",
    "ALAB": "AI infra / connectivity",
    "CRDO": "AI infra / connectivity",
    "MTSI": "AI infra / connectivity",
    "MPWR": "AI infra / power-semiconductors",
    "ACLS": "AI infra / semiconductor equipment",
    "CAMT": "AI infra / semiconductor equipment",
    "AMKR": "AI infra / semiconductor supply chain",
    "LSCC": "AI infra / semiconductors",
    "RMBS": "AI infra / memory-optics",
    "AEHR": "AI infra / semiconductor equipment",
    "VECO": "AI infra / semiconductor equipment",
    "ENTG": "AI infra / semiconductor equipment",
    "MKSI": "AI infra / semiconductor equipment",
    "APP": "AI software / advertising",
    "PLTR": "AI software / data platforms",
    "NBIS": "AI cloud / neocloud",
    "CRWD": "AI software / cyber",
    "NET": "AI software / cyber",
    "DDOG": "AI software / cyber",
    "ZS": "AI software / cyber",
    "SNOW": "AI software / cyber",
    "MDB": "AI software / cyber",
    "OKTA": "AI software / cyber",
    # China/Asia growth
    "BABA": "China ADR / Asia growth",
    "JD": "China ADR / Asia growth",
    "PDD": "China ADR / Asia growth",
    "BIDU": "China ADR / Asia growth",
    "NTES": "China ADR / Asia growth",
    "TCOM": "China ADR / Asia growth",
    "TME": "China ADR / Asia growth",
    "BILI": "China ADR / Asia growth",
    "BEKE": "China ADR / Asia growth",
    "NIO": "China ADR / Asia growth",
    "XPEV": "China ADR / Asia growth",
    "FUTU": "China ADR / Asia growth",
    "SE": "China ADR / Asia growth",
    # Power / grid / nuclear
    "GEV": "Power / grid / nuclear",
    "VST": "Power / grid / nuclear",
    "CEG": "Power / grid / nuclear",
    "ETN": "Power / grid / nuclear",
    "PWR": "Power / grid / nuclear",
    "EME": "Power / grid / nuclear",
    "IESC": "Power / grid / nuclear",
    "STRL": "Power / grid / nuclear",
    "TLN": "Power / grid / data centers",
    "NRG": "Power / grid / data centers",
    "EQIX": "Data centers / REIT",
    "DLR": "Data centers / REIT",
    "NVT": "Power / electrical infrastructure",
    "POWL": "Power / electrical infrastructure",
    "FIX": "AI infra / data-center buildout",
    "MOD": "AI infra / cooling",
    "CLS": "AI infra / data-center buildout",
    "FLEX": "AI infra / data-center buildout",
    "WCC": "Power / electrical infrastructure",
    "HUBB": "Power / electrical infrastructure",
    "MTZ": "Power / electrical infrastructure",
    "ACM": "Power / electrical infrastructure",
    "J": "Power / electrical infrastructure",
    "CARR": "AI infra / cooling",
    "JCI": "AI infra / cooling",
    "TT": "AI infra / cooling",
    "OKLO": "Power / grid / nuclear",
    "SMR": "Power / grid / nuclear",
    "CCJ": "Power / grid / nuclear",
    "NNE": "Power / grid / nuclear",
    "LEU": "Power / grid / nuclear",
    "UEC": "Power / grid / nuclear",
    # Other high-narrative themes
    "IONQ": "Quantum",
    "RGTI": "Quantum",
    "QBTS": "Quantum",
    "QUBT": "Quantum",
    "ARQQ": "Quantum",
    "COIN": "Crypto / blockchain",
    "MSTR": "Crypto / blockchain",
    "HOOD": "Crypto / blockchain",
    "MARA": "Crypto / blockchain",
    "RIOT": "Crypto / blockchain",
    "CLSK": "Crypto / blockchain",
    "IREN": "Crypto / blockchain",
    "HUT": "Crypto / blockchain",
    "BITF": "Crypto / blockchain",
    "WULF": "Crypto / AI-HPC miners",
    "BTDR": "Crypto / AI-HPC miners",
    "APLD": "Crypto / AI-HPC miners",
    "CIFR": "Crypto / AI-HPC miners",
    "SYM": "Robotics / automation",
    "ROK": "Robotics / automation",
    "TER": "Robotics / automation",
    "PATH": "Robotics / automation",
    "MBLY": "Robotics / automation",
    "ZBRA": "Robotics / automation",
    "ABBNY": "Robotics / automation",
    "RKLB": "Space / defense infrastructure",
    "ASTS": "Space / defense infrastructure",
    "HWM": "Aerospace / defense infrastructure",
    "LUNR": "Space / defense infrastructure",
    "SAAB-B.ST": "Defense / aerospace infrastructure",
    "RHM.DE": "Defense / aerospace infrastructure",
    "LDO.MI": "Defense / aerospace infrastructure",
    "KOG.OL": "Defense / aerospace infrastructure",
    "KTOS": "Defense / aerospace infrastructure",
    "AVAV": "Defense / aerospace infrastructure",
    "LMT": "Defense / aerospace infrastructure",
    "NOC": "Defense / aerospace infrastructure",
    "GD": "Defense / aerospace infrastructure",
    "HII": "Defense / aerospace infrastructure",
    "LHX": "Defense / aerospace infrastructure",
    "TDG": "Defense / aerospace infrastructure",
    "AXON": "Defense / aerospace infrastructure",
}


STATIC_FALLBACK = (
    "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO BRK-B JPM V MA LLY COST NFLX WMT ORCL XOM HD PG JNJ BAC ABBV KO "
    "PLTR GE CSCO IBM AMD CRM CVX WFC ABT MCD DIS GS NOW INTU MRK CAT ISRG RTX PEP TMO QCOM AMAT TXN LIN BKNG "
    "SPGI LOW HON PGR UNP ADP DE TJX GILD BSX PANW SYK C MS ADI VRTX LRCX MMC ETN MU KLAC ANET CB SCHW NKE "
    "SBUX SHOP SMCI DELL HPE MRVL COHR LITE CIEN FN SNDK AAOI AXTI WDC STX VRT GEV VST CEG PWR EME IESC STRL "
    "OKLO SMR CCJ IONQ RGTI QBTS QUBT COIN MSTR HOOD MARA RIOT CLSK IREN HUT SYM TER PATH MBLY ZBRA"
).split()


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+.2f}%"


def num(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:.{digits}f}"


def yf_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("^", "")
    return CACHE_DIR / f"{safe}.csv"


def cache_fresh(path: Path) -> bool:
    try:
        age = datetime.now().timestamp() - path.stat().st_mtime
    except OSError:
        return False
    return age <= CACHE_MAX_AGE_HOURS * 3600


def normalize(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in raw.columns for c in required):
        return None
    df = raw[required].dropna().copy()
    df = df[df["Volume"] > 0]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return None if df.empty else df


def read_cached(ticker: str) -> pd.DataFrame | None:
    path = cache_path(ticker)
    if not cache_fresh(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
    except Exception:
        return None
    return normalize(df)


def write_cached(ticker: str, df: pd.DataFrame | None) -> None:
    if df is None or df.empty:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.index.name = "Date"
    out.to_csv(cache_path(ticker))


def get_index_universe() -> tuple[list[Stock], list[str]]:
    stocks: dict[str, Stock] = {}
    failures: list[str] = []

    try:
        html = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 (MLPB research; local personal use)"},
            timeout=20,
        ).text
        spx = pd.read_html(StringIO(html))[0]
        for _, row in spx.iterrows():
            ticker = yf_symbol(str(row["Symbol"]).strip())
            sector = str(row.get("GICS Sector", "S&P 500")).strip()
            group = THEME_OVERRIDES.get(ticker, sector)
            stocks[ticker] = Stock(ticker, str(row.get("Security", ticker)), group, "S&P 500")
    except Exception as exc:
        failures.append(f"S&P500 list fetch failed: {exc}")

    try:
        html = requests.get(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers={"User-Agent": "Mozilla/5.0 (MLPB research; local personal use)"},
            timeout=20,
        ).text
        ndx_tables = pd.read_html(StringIO(html))
        ndx = next(t for t in ndx_tables if any(str(c).lower() in {"ticker", "symbol"} for c in t.columns))
        sym_col = next(c for c in ndx.columns if str(c).lower() in {"ticker", "symbol"})
        name_col = next((c for c in ndx.columns if str(c).lower() in {"company", "security", "name"}), sym_col)
        for _, row in ndx.iterrows():
            ticker = yf_symbol(str(row[sym_col]).strip())
            group = THEME_OVERRIDES.get(ticker, "Nasdaq 100")
            stocks.setdefault(ticker, Stock(ticker, str(row.get(name_col, ticker)), group, "Nasdaq 100"))
    except Exception as exc:
        failures.append(f"Nasdaq100 list fetch failed: {exc}")

    for ticker in STATIC_FALLBACK:
        group = THEME_OVERRIDES.get(ticker, "Fallback liquid")
        stocks.setdefault(ticker, Stock(ticker, ticker, group, "Fallback/theme"))

    for ticker, group in THEME_OVERRIDES.items():
        stocks[ticker] = Stock(ticker, ticker, group, "Theme override")

    for ticker in MARKET_TICKERS:
        stocks.setdefault(ticker, Stock(ticker, ticker, "Index ETF", "Market"))

    return sorted(stocks.values(), key=lambda s: s.ticker), failures


def fetch_all(stocks: list[Stock]) -> tuple[dict[str, pd.DataFrame | None], list[str]]:
    tickers = sorted({s.ticker for s in stocks} | set(MARKET_TICKERS) | {BENCHMARK})
    out: dict[str, pd.DataFrame | None] = {}
    to_fetch: list[str] = []
    for ticker in tickers:
        cached = read_cached(ticker)
        if cached is not None and len(cached) >= MIN_HISTORY:
            out[ticker] = cached
        else:
            to_fetch.append(ticker)

    failures: list[str] = []
    chunk_size = 55
    for i in range(0, len(to_fetch), chunk_size):
        chunk = to_fetch[i : i + chunk_size]
        raw = yf.download(chunk, period="8y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="ticker")
        for ticker in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker in raw.columns.get_level_values(0):
                        sub = raw[ticker]
                    elif ticker in raw.columns.get_level_values(1):
                        sub = raw.xs(ticker, axis=1, level=1)
                    else:
                        sub = None
                else:
                    sub = raw
                df = normalize(sub)
                if df is None or len(df) < MIN_HISTORY:
                    failures.append(ticker)
                    out[ticker] = None
                else:
                    out[ticker] = df
                    write_cached(ticker, df)
            except Exception:
                failures.append(ticker)
                out[ticker] = None
    return out, failures


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_market_features(frames: dict[str, pd.DataFrame | None]) -> pd.DataFrame:
    base = frames.get("SPY")
    if base is None or base.empty:
        return pd.DataFrame()
    idx = base.index
    out = pd.DataFrame(index=idx)
    for ticker in MARKET_TICKERS:
        df = frames.get(ticker)
        if df is None or df.empty:
            continue
        c = df["Close"].reindex(idx).ffill()
        out[f"{ticker}_Close"] = c
        out[f"{ticker}_SMA50"] = c.rolling(50, min_periods=50).mean()
        out[f"{ticker}_SMA200"] = c.rolling(200, min_periods=200).mean()
        out[f"{ticker}_RET21"] = c / c.shift(21) - 1
    risk_on = (
        (out.get("SPY_Close") > out.get("SPY_SMA200"))
        & (out.get("QQQ_Close") > out.get("QQQ_SMA50"))
        & (out.get("SMH_Close") > out.get("SMH_SMA50"))
    )
    risk_off = out.get("SPY_Close") < out.get("SPY_SMA200")
    out["MARKET_REGIME"] = np.where(risk_on, "RISK_ON", np.where(risk_off, "RISK_OFF", "MIXED"))
    return out


def add_features(df: pd.DataFrame, bench: pd.DataFrame | None, market: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    prev_close = close.shift(1)
    for w in (10, 21, 50, 126, 200, 252):
        out[f"SMA{w}"] = close.rolling(w, min_periods=w).mean()
    for w in (21, 63, 126, 252):
        out[f"RET{w}"] = close / close.shift(w) - 1
    for w in (21, 50, 126):
        out[f"SMA{w}_SLOPE21"] = out[f"SMA{w}"] / out[f"SMA{w}"].shift(21) - 1
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    out["ATR14_PCT"] = tr.rolling(14, min_periods=14).mean() / close
    rng = high - low
    out["CLV"] = np.where(rng == 0, 0.0, (2 * close - high - low) / rng)
    out["RSI14"] = rsi(close, 14)
    out["RVOL20"] = out["Volume"] / out["Volume"].rolling(20, min_periods=20).mean()
    out["DOLLAR_VOL20"] = (close * out["Volume"]).rolling(20, min_periods=20).mean()
    out["DD63"] = close / close.rolling(63, min_periods=63).max() - 1
    if bench is not None and not bench.empty:
        bclose = bench["Close"].reindex(out.index).ffill()
        out["RS63"] = out["RET63"] - (bclose / bclose.shift(63) - 1)
        out["RS126"] = out["RET126"] - (bclose / bclose.shift(126) - 1)
    else:
        out["RS63"] = np.nan
        out["RS126"] = np.nan
    out["MARKET_REGIME"] = market["MARKET_REGIME"].reindex(out.index).ffill() if not market.empty else "UNKNOWN"
    out["TREND_OK"] = (close > out["SMA126"]) & (out["SMA50"] > out["SMA126"]) & (out["SMA126_SLOPE21"] > 0)
    out["LEADER_BASIC"] = out["TREND_OK"] & (out["RET63"] > 0.20) & (out["RET126"] > 0.25) & (out["RS63"] > 0.08)
    out["LEADER_STRICT"] = out["TREND_OK"] & (out["RET63"] > 0.35) & (out["RET126"] > 0.45) & (out["RS63"] > 0.18) & (out["RS126"] > 0.15)
    out = add_prime_features(out)
    return out


def signal_mask(df: pd.DataFrame, ma: int, variant: str) -> pd.Series:
    close = df["Close"]
    low = df["Low"]
    prev_close = close.shift(1)
    sma = df[f"SMA{ma}"]
    atrp = df["ATR14_PCT"].clip(lower=0.02, upper=0.18)
    touch_band = {21: 0.030, 50: 0.040, 126: 0.060}[ma]
    max_reclaim_extension = {21: 0.120, 50: 0.180, 126: 0.250}[ma]
    max_penetration = {21: 0.035, 50: 0.060, 126: 0.100}[ma]
    rsi_lo = {21: 45, 50: 40, 126: 35}[ma]
    rsi_hi = {21: 76, 50: 74, 126: 72}[ma]
    touched = (low <= sma * (1 + touch_band)) & (low >= sma * (1 - max_penetration))
    reclaimed = (close > sma) & ((close / sma - 1) <= np.maximum(max_reclaim_extension, 1.2 * atrp))
    trend_extra = df["SMA50_SLOPE21"] > 0
    if ma == 21:
        trend_extra = trend_extra & (df["SMA21"] > df["SMA50"])
    base = df["LEADER_BASIC"] & trend_extra & touched & reclaimed
    if variant == "BASIC":
        return base
    confirm = base & (df["CLV"] > 0.15) & (close > prev_close)
    if variant == "CONFIRM":
        return confirm
    quality = confirm & (df["RVOL20"] >= 0.75) & (df["RSI14"].between(rsi_lo, rsi_hi))
    if variant == "QUALITY":
        return quality
    if variant == "STRICT":
        return quality & df["LEADER_STRICT"] & (df["CLV"] > 0.30)
    raise ValueError(variant)


def dedup(mask: pd.Series) -> pd.Series:
    result = pd.Series(False, index=mask.index)
    last_pos = -10_000
    for pos, (_, ok) in enumerate(mask.fillna(False).items()):
        if ok and pos - last_pos >= DEDUP_BARS:
            result.iloc[pos] = True
            last_pos = pos
    return result


def event_rows(df: pd.DataFrame, stock: Stock, setup: str, ma: int, variant: str, mask: pd.Series) -> list[dict]:
    close = df["Close"].to_numpy(dtype=float)
    open_ = df["Open"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    idx_to_pos = {idx: pos for pos, idx in enumerate(df.index)}
    rows: list[dict] = []
    for idx in df.index[dedup(mask)]:
        pos = idx_to_pos[idx]
        if pos + MAX_FWD + 1 >= len(close):
            continue
        entry = close[pos]
        next_open = open_[pos + 1]
        next_close = close[pos + 1]
        if not all(np.isfinite(x) and x > 0 for x in (entry, next_open, next_close)):
            continue
        row = {
            "ticker": stock.ticker,
            "name": stock.name,
            "group": stock.group,
            "source": stock.source,
            "setup": setup,
            "ma": ma,
            "variant": variant,
            "date": idx.date().isoformat(),
            "entry": float(entry),
            "next_open": float(next_open),
            "gap_next_open": float(next_open / entry - 1),
            "ret63": float(df.loc[idx, "RET63"]),
            "ret126": float(df.loc[idx, "RET126"]),
            "rs63": float(df.loc[idx, "RS63"]),
            "rs126": float(df.loc[idx, "RS126"]),
            "rsi14": float(df.loc[idx, "RSI14"]),
            "clv": float(df.loc[idx, "CLV"]),
            "rvol20": float(df.loc[idx, "RVOL20"]),
            "atr14_pct": float(df.loc[idx, "ATR14_PCT"]),
            "dollar_vol20": float(df.loc[idx, "DOLLAR_VOL20"]),
            "dist_sma": float(entry / df.loc[idx, f"SMA{ma}"] - 1),
            "dd63": float(df.loc[idx, "DD63"]),
            "range_atr_ratio": float(df.loc[idx, "RANGE_ATR_RATIO"]),
            "body_atr_ratio": float(df.loc[idx, "BODY_ATR_RATIO"]),
            "qt_z": float(df.loc[idx, "QT_Z"]),
            "qt_z_delta": float(df.loc[idx, "QT_Z_DELTA"]),
            "qt_stretch_pctile": float(df.loc[idx, "QT_STRETCH_PCTILE"]),
            "qt_abs_stretch_pctile": float(df.loc[idx, "QT_ABS_STRETCH_PCTILE"]),
            "qt_phase": str(df.loc[idx, "QT_PHASE"]),
            "qt_wait_score": float(df.loc[idx, "QT_WAIT_SCORE"]),
            "qt_wait_label": str(df.loc[idx, "QT_WAIT_LABEL"]),
            "qt_confirmation": str(df.loc[idx, "QT_CONFIRMATION"]),
            "qt_post_entry_state": str(df.loc[idx, "QT_POST_ENTRY_STATE"]),
            "qt_regime": str(df.loc[idx, "QT_REGIME"]),
            "market_regime": str(df.loc[idx, "MARKET_REGIME"]),
        }
        visual = visual_quality(row, setup)
        qt = qt_quality(row)
        row["visual_score"] = visual["score"]
        row["visual_grade"] = visual["grade"]
        row["visual_reasons"] = visual["reasons"]
        row["qt_score"] = qt["score"]
        row["qt_label"] = qt["label"]
        row["qt_reasons"] = qt["reasons"]
        for h in FWD_DAYS:
            row[f"fwd{h}"] = float(close[pos + h] / entry - 1)
            row[f"mae{h}"] = float(np.nanmin(low[pos + 1 : pos + h + 1]) / entry - 1)
            row[f"mfe{h}"] = float(np.nanmax(high[pos + 1 : pos + h + 1]) / entry - 1)
            row[f"nextopen_fwd{h}"] = float(close[pos + h + 1] / next_open - 1)
            row[f"nextopen_mae{h}"] = float(np.nanmin(low[pos + 1 : pos + h + 2]) / next_open - 1)
            row[f"nextclose_fwd{h}"] = float(close[pos + h + 1] / next_close - 1)
        rows.append(row)
    return rows


def current_rows(df: pd.DataFrame, stock: Stock, lookback: int = 7) -> list[dict]:
    rows: list[dict] = []
    tail = df.index[-lookback:]
    for ma in (21, 50):
        for variant in ("QUALITY", "STRICT"):
            mask = signal_mask(df, ma, variant)
            for idx in tail:
                if bool(mask.loc[idx]):
                    close = float(df.loc[idx, "Close"])
                    rows.append(
                        {
                            "ticker": stock.ticker,
                            "name": stock.name,
                            "group": stock.group,
                            "setup": f"MLPB{ma}",
                            "variant": variant,
                            "date": idx.date().isoformat(),
                            "close": close,
                            "ret63": float(df.loc[idx, "RET63"]),
                            "rs63": float(df.loc[idx, "RS63"]),
                            "rsi14": float(df.loc[idx, "RSI14"]),
                            "atr14_pct": float(df.loc[idx, "ATR14_PCT"]),
                            "dist_sma": float(close / df.loc[idx, f"SMA{ma}"] - 1),
                            "dd63": float(df.loc[idx, "DD63"]),
                            "clv": float(df.loc[idx, "CLV"]),
                            "rvol20": float(df.loc[idx, "RVOL20"]),
                            "range_atr_ratio": float(df.loc[idx, "RANGE_ATR_RATIO"]),
                            "body_atr_ratio": float(df.loc[idx, "BODY_ATR_RATIO"]),
                            "qt_z": float(df.loc[idx, "QT_Z"]),
                            "qt_z_delta": float(df.loc[idx, "QT_Z_DELTA"]),
                            "qt_stretch_pctile": float(df.loc[idx, "QT_STRETCH_PCTILE"]),
                            "qt_abs_stretch_pctile": float(df.loc[idx, "QT_ABS_STRETCH_PCTILE"]),
                            "qt_phase": str(df.loc[idx, "QT_PHASE"]),
                            "qt_wait_score": float(df.loc[idx, "QT_WAIT_SCORE"]),
                            "qt_wait_label": str(df.loc[idx, "QT_WAIT_LABEL"]),
                            "qt_confirmation": str(df.loc[idx, "QT_CONFIRMATION"]),
                            "qt_post_entry_state": str(df.loc[idx, "QT_POST_ENTRY_STATE"]),
                            "qt_regime": str(df.loc[idx, "QT_REGIME"]),
                            "dollar_vol20": float(df.loc[idx, "DOLLAR_VOL20"]),
                            "market_regime": str(df.loc[idx, "MARKET_REGIME"]),
                        }
                    )
    return rows


def winsor_mean(s: pd.Series, q: float = 0.05) -> float:
    s = s.dropna()
    if len(s) == 0:
        return np.nan
    lo, hi = s.quantile(q), s.quantile(1 - q)
    return float(s.clip(lo, hi).mean())


def bootstrap_ci(vals: pd.Series, iters: int = 1200) -> tuple[float, float]:
    vals = vals.dropna().to_numpy(dtype=float)
    if len(vals) < 12:
        return np.nan, np.nan
    rng = np.random.default_rng(zlib.crc32(np.round(vals, 6).tobytes()) & 0xFFFFFFFF)
    means = [rng.choice(vals, len(vals), replace=True).mean() for _ in range(iters)]
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def summarize_group(group: pd.DataFrame) -> dict:
    return {
        "n": int(len(group)),
        "fwd21": float(group["fwd21"].mean()),
        "nextopen21": float(group["nextopen_fwd21"].mean()),
        "median21": float(group["fwd21"].median()),
        "winsor21": winsor_mean(group["fwd21"]),
        "hit21": float((group["fwd21"] > 0).mean()),
        "mae21_p10": float(group["mae21"].quantile(0.10)),
        "fwd42": float(group["fwd42"].mean()),
        "gap_next_open": float(group["gap_next_open"].mean()),
    }


def matched_random_baseline(events: pd.DataFrame, features: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rng = np.random.default_rng(95095)
    rows = []
    focus_keys = events[events["setup"].isin(["MLPB21", "MLPB50"])].groupby(["variant", "setup"]).size().index
    for variant, setup in focus_keys:
        group = events[(events["variant"] == variant) & (events["setup"] == setup)]
        ma = int(setup.replace("MLPB", ""))
        ticker_counts = group["ticker"].value_counts().to_dict()
        pools: dict[str, np.ndarray] = {}
        for ticker in ticker_counts:
            df = features.get(ticker)
            if df is None or df.empty:
                continue
            eligible = df[df["LEADER_BASIC"] & df["TREND_OK"]].dropna(subset=[f"SMA{ma}", "RET63", "RS63"])
            pos = pd.Series(np.arange(len(df)), index=df.index).reindex(eligible.index).dropna().astype(int).to_numpy()
            pos = pos[(pos + MAX_FWD + 1) < len(df)]
            if len(pos):
                pools[ticker] = pos
        for h in (21, 42):
            means, hits, next_means = [], [], []
            for _ in range(BASELINE_ITERS):
                vals, next_vals = [], []
                for ticker, count in ticker_counts.items():
                    pool = pools.get(ticker)
                    if pool is None or len(pool) == 0:
                        continue
                    df = features[ticker]
                    c = df["Close"].to_numpy(dtype=float)
                    o = df["Open"].to_numpy(dtype=float)
                    picks = rng.choice(pool, size=count, replace=True)
                    vals.extend(c[picks + h] / c[picks] - 1)
                    next_vals.extend(c[picks + h + 1] / o[picks + 1] - 1)
                if vals:
                    arr = np.asarray(vals)
                    means.append(arr.mean())
                    hits.append((arr > 0).mean())
                    next_means.append(np.asarray(next_vals).mean())
            rows.append(
                {
                    "variant": variant,
                    "setup": setup,
                    "horizon": h,
                    "random_mean": float(np.mean(means)) if means else np.nan,
                    "random_hit": float(np.mean(hits)) if hits else np.nan,
                    "random_nextopen_mean": float(np.mean(next_means)) if next_means else np.nan,
                }
            )
    return pd.DataFrame(rows)


def add_table(lines: list[str], df: pd.DataFrame, pct_cols: list[str], cols: list[str] | None = None) -> None:
    if df.empty:
        lines.append("No rows.")
        return
    disp = df.copy()
    if cols is not None:
        disp = disp[cols]
    for col in pct_cols:
        if col in disp.columns:
            disp[col] = disp[col].map(pct)
    lines.append(disp.to_string(index=False))


def main() -> int:
    stocks, universe_failures = get_index_universe()
    frames, data_failures = fetch_all(stocks)
    market = add_market_features(frames)
    bench = frames.get(BENCHMARK)

    events: list[dict] = []
    current: list[dict] = []
    features: dict[str, pd.DataFrame] = {}
    used_stocks: list[Stock] = []

    for stock in stocks:
        df = frames.get(stock.ticker)
        if df is None or len(df) < MIN_HISTORY:
            continue
        feat = add_features(df, bench, market)
        features[stock.ticker] = feat
        used_stocks.append(stock)
        for ma in (21, 50, 126):
            for variant in ("BASIC", "CONFIRM", "QUALITY", "STRICT"):
                events.extend(event_rows(feat, stock, f"MLPB{ma}", ma, variant, signal_mask(feat, ma, variant)))
        current.extend(current_rows(feat, stock))

    events_df = pd.DataFrame(events)
    current_df = pd.DataFrame(current)
    baseline = matched_random_baseline(events_df, features) if not events_df.empty else pd.DataFrame()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_count": len(stocks),
        "used_count": len(used_stocks),
        "events_count": int(len(events_df)),
        "universe_failures": universe_failures,
        "data_failures": data_failures,
        "events": events,
        "current_candidates": current,
    }
    JSON_OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# MLPB final falsification research")
    lines.append("")
    lines.append(f"Generated: {payload['generated_at']}")
    lines.append(f"Universe candidates: {len(stocks)}; usable price histories: {len(used_stocks)}; events: {len(events_df)}")
    lines.append(f"Baseline iterations: {BASELINE_ITERS}; benchmark: {BENCHMARK}")
    if universe_failures:
        lines.append(f"Universe source warnings: {' | '.join(universe_failures)}")
    if data_failures:
        lines.append(f"Data failures/too short: {', '.join(sorted(set(data_failures))[:80])}")
    lines.append("")
    lines.append("## Method caveats")
    lines.append("")
    lines.append("- Uses current S&P 500/Nasdaq 100 membership plus explicit theme tickers, so survivorship bias is present.")
    lines.append("- Uses adjusted OHLCV from Yahoo Finance via yfinance. This is good enough for research direction, not final execution.")
    lines.append("- Earnings/news history is not fully backtested here; the live scanner must keep an explicit earnings/news blackout gate.")
    lines.append("- Same-day close returns are research-only; next-open returns are the more realistic EOD-signal execution check.")
    lines.append("")

    if events_df.empty:
        lines.append("No events produced.")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {OUT}")
        print(f"Wrote {JSON_OUT}")
        return 0

    lines.append("## Aggregate setup performance")
    rows = []
    for (variant, setup), g in events_df.groupby(["variant", "setup"]):
        row = {"variant": variant, "setup": setup}
        row.update(summarize_group(g))
        rows.append(row)
    agg = pd.DataFrame(rows).sort_values(["variant", "setup"])
    add_table(
        lines,
        agg,
        ["fwd21", "nextopen21", "median21", "winsor21", "hit21", "mae21_p10", "fwd42", "gap_next_open"],
        ["variant", "setup", "n", "fwd21", "nextopen21", "median21", "winsor21", "hit21", "mae21_p10", "fwd42", "gap_next_open"],
    )
    lines.append("")

    lines.append("## Lift vs matched random leader-days")
    rows = []
    for _, row in agg.iterrows():
        b = baseline[(baseline["variant"] == row["variant"]) & (baseline["setup"] == row["setup"]) & (baseline["horizon"] == 21)]
        if b.empty:
            continue
        rows.append(
            {
                "variant": row["variant"],
                "setup": row["setup"],
                "n": int(row["n"]),
                "fwd21": row["fwd21"],
                "random21": float(b.iloc[0]["random_mean"]),
                "lift": row["fwd21"] - float(b.iloc[0]["random_mean"]),
                "nextopen21": row["nextopen21"],
                "random_nextopen21": float(b.iloc[0]["random_nextopen_mean"]),
                "nextopen_lift": row["nextopen21"] - float(b.iloc[0]["random_nextopen_mean"]),
                "hit21": row["hit21"],
                "random_hit21": float(b.iloc[0]["random_hit"]),
            }
        )
    lift = pd.DataFrame(rows).sort_values(["variant", "lift"], ascending=[True, False])
    add_table(lines, lift, ["fwd21", "random21", "lift", "nextopen21", "random_nextopen21", "nextopen_lift", "hit21", "random_hit21"])
    lines.append("")

    focus = events_df[(events_df["variant"] == "STRICT") & (events_df["setup"].isin(["MLPB21", "MLPB50"]))].copy()
    lines.append("## Strict MLPB21/50 regime split")
    rows = []
    for (setup, regime), g in focus.groupby(["setup", "market_regime"]):
        if len(g) < 20:
            continue
        lo, hi = bootstrap_ci(g["nextopen_fwd21"])
        row = {"setup": setup, "market_regime": regime, "ci95_nextopen21": f"[{pct(lo)}, {pct(hi)}]"}
        row.update(summarize_group(g))
        rows.append(row)
    regime_df = pd.DataFrame(rows).sort_values(["setup", "nextopen21"], ascending=[True, False])
    add_table(lines, regime_df, ["fwd21", "nextopen21", "median21", "winsor21", "hit21", "mae21_p10", "fwd42", "gap_next_open"])
    lines.append("")

    lines.append("## Strict MLPB21/50 time split")
    focus["date_dt"] = pd.to_datetime(focus["date"])
    focus["split"] = np.select(
        [focus["date_dt"] < "2022-01-01", focus["date_dt"] < "2025-01-01"],
        ["2019-2021", "2022-2024"],
        default="2025+",
    )
    rows = []
    for (setup, split), g in focus.groupby(["setup", "split"]):
        if len(g) < 15:
            continue
        row = {"setup": setup, "split": split}
        row.update(summarize_group(g))
        rows.append(row)
    split_df = pd.DataFrame(rows).sort_values(["setup", "split"])
    add_table(lines, split_df, ["fwd21", "nextopen21", "median21", "winsor21", "hit21", "mae21_p10", "fwd42", "gap_next_open"])
    lines.append("")

    lines.append("## Filter stress: next-open fwd21")
    stress_frames = []
    bucket_specs = [
        ("dist_bucket", pd.cut(focus["dist_sma"], bins=[-1, 0.04, 0.08, 0.12, 9], labels=["<=4%", "4-8%", "8-12%", ">12%"])),
        ("atr_bucket", pd.cut(focus["atr14_pct"], bins=[0, 0.05, 0.08, 0.12, 9], labels=["<=5%", "5-8%", "8-12%", ">12%"])),
        ("rsi_bucket", pd.cut(focus["rsi14"], bins=[0, 48, 58, 68, 76, 100], labels=["<48", "48-58", "58-68", "68-76", ">76"])),
        (
            "dvol_bucket",
            pd.cut(
                focus["dollar_vol20"],
                bins=[0, 20_000_000, 100_000_000, 500_000_000, math.inf],
                labels=["<$20M", "$20-100M", "$100-500M", ">$500M"],
            ),
        ),
    ]
    for label, buckets in bucket_specs:
        temp = focus.copy()
        temp[label] = buckets
        for (setup, bucket), g in temp.groupby(["setup", label], observed=True):
            if len(g) < 15:
                continue
            stress_frames.append(
                {
                    "filter": label,
                    "bucket": str(bucket),
                    "setup": setup,
                    "n": len(g),
                    "nextopen21": g["nextopen_fwd21"].mean(),
                    "winsor21": winsor_mean(g["nextopen_fwd21"]),
                    "hit21": (g["nextopen_fwd21"] > 0).mean(),
                    "mae21_p10": g["nextopen_mae21"].quantile(0.10),
                }
            )
    stress = pd.DataFrame(stress_frames).sort_values(["filter", "setup", "bucket"])
    add_table(lines, stress, ["nextopen21", "winsor21", "hit21", "mae21_p10"])
    lines.append("")

    lines.append("## Group/theme performance: strict MLPB21/50")
    rows = []
    for group, g in focus.groupby("group"):
        if len(g) < 12:
            continue
        lo, hi = bootstrap_ci(g["nextopen_fwd21"])
        rows.append(
            {
                "group": group,
                "n": len(g),
                "nextopen21": g["nextopen_fwd21"].mean(),
                "winsor21": winsor_mean(g["nextopen_fwd21"]),
                "hit21": (g["nextopen_fwd21"] > 0).mean(),
                "ci95": f"[{pct(lo)}, {pct(hi)}]",
                "mae21_p10": g["nextopen_mae21"].quantile(0.10),
                "top_ticker_share": g.groupby("ticker").size().max() / len(g),
            }
        )
    group_df = pd.DataFrame(rows).sort_values("nextopen21", ascending=False)
    add_table(lines, group_df, ["nextopen21", "winsor21", "hit21", "mae21_p10", "top_ticker_share"])
    lines.append("")

    lines.append("## Worst strict failures")
    worst = focus.sort_values("nextopen_fwd21").head(25).copy()
    add_table(
        lines,
        worst[["ticker", "date", "group", "setup", "market_regime", "ret63", "rs63", "rsi14", "atr14_pct", "dist_sma", "nextopen_fwd21", "nextopen_mae21", "fwd42"]],
        ["ret63", "rs63", "atr14_pct", "dist_sma", "nextopen_fwd21", "nextopen_mae21", "fwd42"],
    )
    lines.append("")

    lines.append("## Current broad-universe MLPB candidates")
    if current_df.empty:
        lines.append("No current candidates in the last 7 trading bars.")
    else:
        cur = current_df.sort_values(["date", "variant", "setup", "ticker"])
        for col in ["ret63", "rs63", "rsi14", "atr14_pct", "dist_sma", "dollar_vol20"]:
            if col not in cur.columns:
                cur[col] = np.nan
        display = cur[["date", "ticker", "group", "setup", "variant", "market_regime", "ret63", "rs63", "rsi14", "atr14_pct", "dist_sma", "dollar_vol20"]].copy()
        for col in ["ret63", "rs63", "atr14_pct", "dist_sma"]:
            display[col] = display[col].map(pct)
        display["rsi14"] = display["rsi14"].map(lambda x: num(x, 1))
        display["dollar_vol20"] = display["dollar_vol20"].map(lambda x: f"${x/1_000_000:,.0f}M" if np.isfinite(x) else "n/a")
        lines.append(display.to_string(index=False))
    lines.append("")

    # Decision rules from this falsification pass.
    strict50 = focus[focus["setup"] == "MLPB50"]
    strict21 = focus[focus["setup"] == "MLPB21"]
    strict50_next = strict50["nextopen_fwd21"].mean() if len(strict50) else np.nan
    strict21_next = strict21["nextopen_fwd21"].mean() if len(strict21) else np.nan
    lines.append("## Final falsification verdict")
    lines.append("")
    lines.append(f"- Strict MLPB50 next-open fwd21: {pct(strict50_next)} over n={len(strict50)}.")
    lines.append(f"- Strict MLPB21 next-open fwd21: {pct(strict21_next)} over n={len(strict21)}.")
    lines.append("- A live scanner should prefer MLPB50, require EOD confirmation, and rank MLPB21 below MLPB50 unless theme heat is exceptional.")
    lines.append("- Same-day-close backtests are useful for research but too optimistic operationally; next-open is the live reference.")
    lines.append("- Keep earnings/news and broker spread/depth as hard manual blockers.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Wrote {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
