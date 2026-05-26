#!/usr/bin/env python3
"""
daily_scan.py — Multi-region ETF Scanner (Framework-v2)

VIKTIGT: Scanner använder INTE VIX. Signaler baseras enbart på
RankINT(DirRVOL63, RSI9, dist_sma252/21/126) + Bull-stack-regimkrav.
VIX hör till FX-cross-systemet — får ej blandas in här (brain #1941).

Beräknar RankINT-komposit (capitulation Z_P85 + pullback Z_P70 + Bull-stack)
för varje instrument i universe-filen och skriver dagsrapport + watchlist.

Körväg:
  cd "/Users/bobbo/Desktop/Finans Projects"
  .venv/bin/python scripts/daily_scan.py

Argument:
  --universe-file PATH     (default: data/scanner_universe.json)
  --date YYYY-MM-DD        (default: today)
  --output-dir PATH        (default: data/)
  --portfolio SEK          (default: 60000, sätt 0 för att skippa position sizing)

Signaler (Framework-v2, 2026-05-17):
  Capitulation: RankINT(DirRVOL63) ≤ -Z_P85 AND RankINT(RSI9) ≤ -Z_P85
                AND RankINT(dist_sma252) ≤ -Z_P85
  Pullback:     Bull-stack (Close>SMA21>SMA52>SMA126>SMA252) AND
                RankINT(dist_sma21) ≤ -Z_P70 AND RankINT(RSI9) ≤ -Z_P70
                AND RankINT(DirRVOL63) ≥ +Z_P70
                Whitelist: EWG, 2800.HK, ^HSI (§ 5b PROBATIONÄR)
  PB SMA126:    SMA126>SMA252 AND RankINT(dist_sma126) ≤ -Z_P70
                AND RankINT(RSI9) ≤ -Z_P70 AND RankINT(DirRVOL63) ≥ +Z_P70
                Universellt operativ (§ 5b.2 VALIDERAD 5/5 W2)

Watchlist-output per trigger:
  ticker | signal_type | entry_zone | no-hard-stop-policy | position_units | smärttröskel_SEK | smärttröskel_pct

Beroenden: yfinance, pandas, numpy, scipy (rank_int.py från samma katalog)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))

try:
    import yfinance as yf
except ImportError:
    sys.exit("ERROR: yfinance ej installerat. Kör: .venv/bin/pip install yfinance")

from rank_int import rank_int, Z_P70, Z_P85

try:
    from position_size import size_position_conviction, get_fx_rate_to_sek, get_currency
    _SIZING_AVAILABLE = True
except ImportError:
    _SIZING_AVAILABLE = False

# ---------------------------------------------------------------------------
# Konstanter
# ---------------------------------------------------------------------------

RSI_PERIOD   = 9
VOL_WINDOW   = 63
SMA_PERIODS  = [21, 52, 126, 252]
RANK_WINDOW  = 1260   # ~5y trading days
MIN_HISTORY  = 1240   # skippa tickers med färre bars (~4.9y, tolererar yfinance-gräns)
MIN_PERIODS  = 252    # min giltiga värden för RankINT
ANALOG_FWD_DAYS = (5, 21, 63)
ANALOG_TOP_N = 25
ANALOG_MIN_N = 10
SIGNAL_FRESHNESS_LOOKBACK = 21

# Tröskelvärdestabellen (Z-score från standard normal CDF)
CAP_THRESHOLD = -Z_P85  # -1.036 (p15, alla 3 villkor ≤ detta vid capitulation)
PB_THRESHOLD  = -Z_P70  # -0.524 (p30, dist_sma21 + RSI9 vid pullback)
PB_VOL_THRESH =  Z_P70  # +0.524 (p70, DirRVOL vid pullback, köpintresse)
SIGNAL_JOURNAL_LOOKBACK_DAYS = 45
SIGNAL_DEFS = (
    ("cap_trigger", "cap", "capitulation", "CAP"),
    ("pb126_trigger", "pb126", "pullback-sma126", "PB126"),
    ("pb_trigger", "pb", "pullback", "PB"),
)
MANUAL_TRADE_CHECKS = (
    "broker_fill_confirmed",
    "spread_liquidity_checked",
    "news_event_blackout_checked",
)
PRICE_GAP_REVIEW_PCT = 3.0
PRICE_GAP_BLOCK_PCT = 7.0

# PB SMA21 whitelist: operativt validerade per § 5b (PROBATIONÄR, post-bias-fix 2026-05-17)
# ^HSI körs med 2800.HK proxy-volym i scannern; egen index-volym används inte.
# DROPPADE: KOSDAQ, OMXS30, Nikkei.
PB_SMA21_WHITELIST: frozenset = frozenset(["EWG", "2800.HK", "^HSI"])

# ---------------------------------------------------------------------------
# Legacy bootstrap universe — do not auto-run from this in production.
# The operative scanner source is data/scanner_universe.json because it carries
# per-instrument thresholds and current ticker/proxy decisions.
# ---------------------------------------------------------------------------

DEFAULT_UNIVERSE: dict = {
    "instruments": [
        {"ticker": "XDJP.DE",        "name": "Xtrackers Nikkei 225 1D",        "region": "Asien",  "currency": "EUR", "isin": "LU0839027447"},
        {"ticker": "FLXC.DE",        "name": "Franklin FTSE China",             "region": "Asien",  "currency": "EUR", "isin": "IE00BHZRR147"},
        {"ticker": "FLXK.DE",        "name": "Franklin FTSE Korea",             "region": "Asien",  "currency": "EUR", "isin": "IE00BHZRQZ17"},
        {"ticker": "FLXI.DE",        "name": "Franklin FTSE India",             "region": "Asien",  "currency": "EUR", "isin": "IE00BHZRQY08"},
        {"ticker": "H4ZX.DE",        "name": "HSBC Hang Seng Tech",             "region": "Asien",  "currency": "EUR", "isin": "IE00BMWXKN31"},
        {"ticker": "VGEK.DE",        "name": "Vanguard Dev Asia Pac ex JP",     "region": "Asien",  "currency": "EUR", "isin": "IE00B9F5YL18"},
        {"ticker": "XMID.DE",        "name": "iShares MSCI Indonesia",          "region": "Asien",  "currency": "EUR", "isin": "IE00B3T9LM79"},
        {"ticker": "EWG",            "name": "iShares MSCI Germany",            "region": "Europa", "currency": "USD", "isin": "US4642865186"},
        {"ticker": "DBXD.DE",        "name": "Xtrackers DAX",                   "region": "Europa", "currency": "EUR", "isin": "LU0274211480"},
        {"ticker": "EXSA.DE",        "name": "iShares STOXX Europe 600",        "region": "Europa", "currency": "EUR", "isin": "DE0002635307"},
        {"ticker": "XACT-OMXS30.ST", "name": "XACT OMXS30",                    "region": "Europa", "currency": "SEK", "isin": "SE0000671738"},
        {"ticker": "AMES.DE",        "name": "Amundi IBEX 35 Acc",              "region": "Europa", "currency": "EUR", "isin": ""},
    ]
}


def load_execution_map(path: Path) -> dict[str, dict]:
    """Optional signal-instrument -> broker execution instrument mapping."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    mappings = payload.get("mappings", payload if isinstance(payload, dict) else {})
    if not isinstance(mappings, dict):
        return {}
    return {
        str(k): v for k, v in mappings.items()
        if isinstance(v, dict)
    }


def price_gap_state(open_gap_pct: float) -> str:
    if open_gap_pct is None or np.isnan(open_gap_pct):
        return "UNKNOWN"
    abs_gap = abs(open_gap_pct)
    if abs_gap >= PRICE_GAP_BLOCK_PCT:
        return "GAP_BLOCK_REVIEW"
    if abs_gap >= PRICE_GAP_REVIEW_PCT:
        return "GAP_REVIEW"
    return "OK"

# ---------------------------------------------------------------------------
# Indikatorhjälpare (identisk logik med pullback_validation_unbiased.py)
# ---------------------------------------------------------------------------

def wilder_rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_clv(df: pd.DataFrame) -> pd.Series:
    rng = df["High"] - df["Low"]
    clv = np.where(rng == 0, 0.0, (2 * df["Close"] - df["High"] - df["Low"]) / rng)
    return pd.Series(clv, index=df.index, name="CLV")


def compute_dir_rvol63(df: pd.DataFrame, clv: pd.Series) -> pd.Series:
    sma_vol = df["Volume"].rolling(window=VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    rvol    = df["Volume"] / sma_vol
    return (rvol * clv).rename("DirRVOL63")


def compute_dir_logvolz63(df: pd.DataFrame, clv: pd.Series) -> pd.Series:
    log_vol = np.log(df["Volume"].replace(0, np.nan))
    sma_log = log_vol.rolling(window=VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    std_log = log_vol.rolling(window=VOL_WINDOW, min_periods=VOL_WINDOW).std()
    logvolz = ((log_vol - sma_log) / std_log.replace(0, np.nan)).clip(lower=0)
    return (logvolz * clv).rename("DirLogVolZ63")


def _volume_block_reason(ticker: str, inst_thresholds: dict | None = None) -> str | None:
    """Return reason when DirRVOL must not drive live signals for this ticker."""
    if inst_thresholds and inst_thresholds.get("fetch_fail"):
        return "volume fetch failed"
    if inst_thresholds and inst_thresholds.get("vol_fail"):
        return "volume failed calibration"
    if inst_thresholds and inst_thresholds.get("volume_source") == "proxy":
        return None
    if ticker.startswith("^"):
        if inst_thresholds and inst_thresholds.get("allow_index_volume") is True:
            return None
        return "^ index volume blocked; requires verified ETF proxy"
    return None


def _normalise_datetime_index(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.to_datetime(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    return idx.normalize()


def _align_series_by_date(series: pd.Series, target_index: pd.Index) -> pd.Series:
    """Align proxy-derived daily features to the price instrument's date index."""
    src = series.copy()
    src.index = _normalise_datetime_index(src.index)
    src = src[~src.index.duplicated(keep="last")].sort_index()
    target_dates = _normalise_datetime_index(target_index)
    aligned = src.reindex(target_dates)
    return pd.Series(aligned.to_numpy(dtype=float), index=target_index, name=series.name)


def compute_atr20(df: pd.DataFrame) -> pd.Series:
    """20-dagars Average True Range (absolut pris, ej %)."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(20, min_periods=20).mean().rename("ATR20")

# ---------------------------------------------------------------------------
# Datahämtning
# ---------------------------------------------------------------------------

_FETCH_RETRY_DELAYS = (2, 5, 10)  # sekunder mellan försök 1→2, 2→3, 3→4


def fetch_ohlcv(ticker: str) -> Optional[pd.DataFrame]:
    """Hämtar 5y daglig OHLCV via yfinance med retry+backoff. Returnerar DataFrame eller None.

    Retry-policy: 4 försök totalt, explicit backoff 2/5/10s.
    Skiljer på yfinance-exception (nätverk/rate-limit, ska retrya) och
    tom DataFrame (ticker existerar inte, ingen retry).
    """
    last_err: Optional[str] = None
    max_attempts = len(_FETCH_RETRY_DELAYS) + 1
    for attempt in range(max_attempts):
        try:
            raw = yf.download(
                ticker, period="6y", interval="1d",
                progress=False, auto_adjust=True
            )
            if raw.empty:
                return None

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0] for c in raw.columns]

            required = {"Open", "High", "Low", "Close", "Volume"}
            if required - set(raw.columns):
                return None

            df = raw[list(required)].dropna(subset=["Close", "High", "Low", "Volume"])
            df = df[df["Volume"] > 0].copy()
            return df if not df.empty else None

        except Exception as e:
            last_err = str(e)
            if attempt < max_attempts - 1:
                delay = _FETCH_RETRY_DELAYS[attempt]
                print(f"    WARN {ticker} attempt {attempt+1}/{max_attempts}: {e} — retry om {delay}s", flush=True)
                time.sleep(delay)

    print(f"    ERROR {ticker}: {last_err} ({max_attempts} attempts)", flush=True)
    return None


def _latency_profile(ticker: str) -> dict:
    """Best-effort Yahoo latency profile for UI/audit labels."""
    t = ticker.upper()
    if t.endswith(".ST"):
        return {
            "latency": "REAL_TIME",
            "latency_label": "RT",
            "delay_minutes": 0,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if t.endswith(".DE") or t.endswith(".HK") or t == "^HSI":
        return {
            "latency": "DELAY_15M",
            "latency_label": "15m",
            "delay_minutes": 15,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if t.endswith(".AS") or t.endswith(".OL") or t.endswith(".PA") or t.endswith(".MC"):
        return {
            "latency": "DELAY_15M",
            "latency_label": "15m",
            "delay_minutes": 15,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if t.endswith(".L") or t.endswith(".MI"):
        return {
            "latency": "DELAY_20M",
            "latency_label": "20m",
            "delay_minutes": 20,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if t.endswith(".SW"):
        return {
            "latency": "DELAY_30M",
            "latency_label": "30m",
            "delay_minutes": 30,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if t.endswith(".CO") or t.endswith(".HE"):
        return {
            "latency": "REAL_TIME",
            "latency_label": "RT",
            "delay_minutes": 0,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    if "." not in t:
        return {
            "latency": "US_BEST_EFFORT_RT",
            "latency_label": "US RT",
            "delay_minutes": 0,
            "provider": "Yahoo Finance / ICE Data Services",
        }
    return {
        "latency": "UNKNOWN",
        "latency_label": "?",
        "delay_minutes": None,
        "provider": "Yahoo Finance / yfinance",
    }


def _empty_quote_meta(ticker: str, status: str = "daily") -> dict:
    meta = _latency_profile(ticker)
    meta.update({
        "price_source": "yfinance:1d",
        "intraday_overlay": False,
        "intraday_status": status,
        "quote_time": None,
        "quote_date": None,
    })
    return meta


def _flatten_yfinance_columns(raw: pd.DataFrame) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = [c[0] for c in raw.columns]
    return raw


def _overlay_intraday(ticker: str, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Overlay latest 15m Yahoo bar on top of the 6y daily history.

    RankINT still uses the same daily history. The only mutation is the current
    session bar, so Reload shows the freshest available price while preserving
    the framework's daily-bar discipline.
    """
    meta = _empty_quote_meta(ticker)
    if df.empty:
        meta["intraday_status"] = "empty_daily"
        return df, meta

    try:
        intra = yf.download(
            ticker,
            period="1d",
            interval="15m",
            progress=False,
            auto_adjust=True,
            prepost=False,
        )
        if intra is None or intra.empty:
            meta["intraday_status"] = "no_intraday"
            return df, meta

        intra = _flatten_yfinance_columns(intra)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if set(required) - set(intra.columns):
            meta["intraday_status"] = "intraday_missing_ohlcv"
            return df, meta

        intra = intra[required].copy()
        for col in required:
            intra[col] = pd.to_numeric(intra[col], errors="coerce")
        intra = intra.dropna(subset=["Open", "High", "Low", "Close"])
        if intra.empty:
            meta["intraday_status"] = "intraday_no_valid_rows"
            return df, meta

        last_ts = pd.Timestamp(intra.index[-1])
        last_date = last_ts.date()
        df_last_date = pd.Timestamp(df.index[-1]).date()
        day = intra[[pd.Timestamp(idx).date() == last_date for idx in intra.index]]
        if day.empty:
            meta["intraday_status"] = "intraday_no_session_rows"
            return df, meta

        new_open = float(day["Open"].iloc[0])
        new_high = float(day["High"].max())
        new_low = float(day["Low"].min())
        new_close = float(day["Close"].iloc[-1])
        new_volume = float(day["Volume"].fillna(0).sum())
        out = df.copy()

        if last_date > df_last_date:
            row = pd.DataFrame(
                {
                    "Open": [new_open],
                    "High": [new_high],
                    "Low": [new_low],
                    "Close": [new_close],
                    "Volume": [new_volume],
                },
                index=[pd.Timestamp(last_date)],
            )
            out = pd.concat([out, row])
            status = "appended_intraday"
        elif last_date == df_last_date:
            idx = out.index[-1]
            out.loc[idx, "Close"] = new_close
            out.loc[idx, "High"] = max(float(out.loc[idx, "High"]), new_high)
            out.loc[idx, "Low"] = min(float(out.loc[idx, "Low"]), new_low)
            out.loc[idx, "Volume"] = new_volume
            status = "replaced_latest_daily"
        else:
            meta["intraday_status"] = "intraday_older_than_daily"
            meta["quote_time"] = last_ts.isoformat()
            meta["quote_date"] = last_date.isoformat()
            return out, meta

        meta.update({
            "price_source": "yfinance:1d+15m-overlay",
            "intraday_overlay": True,
            "intraday_status": status,
            "quote_time": last_ts.isoformat(),
            "quote_date": last_date.isoformat(),
        })
        return out, meta
    except Exception as e:
        meta["intraday_status"] = f"intraday_error: {e}"
        print(f"    WARN {ticker} intraday overlay failed: {e}", flush=True)
        return df, meta


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday, used for Good Friday market holiday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:  # Saturday observed Friday
        return actual - timedelta(days=1)
    if actual.weekday() == 6:  # Sunday observed Monday
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _us_market_holidays(year: int) -> set[date]:
    """NYSE full-day holidays needed for stale-close checks.

    Early closes are intentionally ignored; the stale guard only needs to know
    whether a normal daily close should have existed between two dates.
    """
    holidays = {
        _observed_fixed_holiday(year, 1, 1),       # New Year's Day
        _nth_weekday(year, 1, 0, 3),               # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),               # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),                 # Memorial Day
        _observed_fixed_holiday(year, 6, 19),      # Juneteenth
        _observed_fixed_holiday(year, 7, 4),       # Independence Day
        _nth_weekday(year, 9, 0, 1),               # Labor Day
        _nth_weekday(year, 11, 3, 4),              # Thanksgiving Day
        _observed_fixed_holiday(year, 12, 25),     # Christmas Day
    }
    # If next New Year's Day is observed on Dec 31 this year.
    next_new_year_observed = _observed_fixed_holiday(year + 1, 1, 1)
    if next_new_year_observed.year == year:
        holidays.add(next_new_year_observed)
    return holidays


def _uses_us_market_calendar(ticker: str | None = None) -> bool:
    t = (ticker or "").upper()
    if not t:
        return False
    if t in {"^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX"}:
        return True
    return "." not in t and not t.startswith("^")


def _expected_trading_day(day: date, ticker: str | None = None) -> bool:
    if day.weekday() >= 5:
        return False
    if _uses_us_market_calendar(ticker) and day in _us_market_holidays(day.year):
        return False
    return True


def _expected_trading_days_after(last_close: date, runtime_date: date, ticker: str | None = None) -> int:
    if runtime_date <= last_close:
        return 0
    count = 0
    day = last_close + timedelta(days=1)
    while day <= runtime_date:
        if _expected_trading_day(day, ticker):
            count += 1
        day += timedelta(days=1)
    return count


def _is_stale_close(last_close: date, runtime_date: date, ticker: str | None = None) -> bool:
    """Conservative trading-calendar-aware stale-data guard.

    The current runtime date is allowed as one pending trading session because
    scans can run before the official daily close exists in Yahoo/yfinance.
    Data is stale only when more than one expected trading session has passed
    after the latest close. US-listed tickers use a NYSE full-day holiday
    calendar; other tickers use weekday-only fallback unless a better calendar
    is added.
    """
    return _expected_trading_days_after(last_close, runtime_date, ticker) > 1


def _recent_split_event(ticker: str, runtime_date: date, lookback_days: int = 90) -> Optional[dict]:
    """Return recent stock split metadata if yfinance reports one.

    Split windows are excluded because volume is not reliably split-adjusted,
    which can create false DirRVOL spikes. If the actions endpoint fails we do
    not block the scan; yfinance outages are handled by the existing fetch guard.
    """
    try:
        actions = yf.Ticker(ticker).actions
        if actions is None or actions.empty or "Stock Splits" not in actions.columns:
            return None
        recent = actions.copy()
        recent.index = pd.to_datetime(recent.index).tz_localize(None)
        cutoff = pd.Timestamp(runtime_date - timedelta(days=lookback_days))
        recent = recent[recent.index >= cutoff]
        splits = recent[pd.to_numeric(recent["Stock Splits"], errors="coerce").fillna(0) != 0]
        if splits.empty:
            return None
        last = splits.iloc[-1]
        return {
            "date": splits.index[-1].date().isoformat(),
            "factor": float(last["Stock Splits"]),
        }
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Indikatorberäkning
# ---------------------------------------------------------------------------

def build_features(df: pd.DataFrame, dir_rvol_source: pd.DataFrame | None = None) -> pd.DataFrame:
    """Beräknar SMA/RSI på prisinstrumentet och DirRVOL på eventuell proxy."""
    close = df["Close"]
    for w in SMA_PERIODS:
        # center=False säkerställer inga look-ahead (forward rolling)
        df[f"SMA{w}"] = close.rolling(window=w, min_periods=w, center=False).mean()
    df["RSI9"]        = wilder_rsi(close, RSI_PERIOD)
    df["RSI21"]       = wilder_rsi(close, 21)
    df["RSI63"]       = wilder_rsi(close, 63)
    vol_df            = dir_rvol_source if dir_rvol_source is not None else df
    clv               = compute_clv(vol_df)
    dir_rvol          = compute_dir_rvol63(vol_df, clv)
    dir_logvolz       = compute_dir_logvolz63(vol_df, clv)
    df["DirRVOL63"]   = (
        _align_series_by_date(dir_rvol, df.index)
        if dir_rvol_source is not None else
        dir_rvol
    )
    df["DirLogVolZ63"] = (
        _align_series_by_date(dir_logvolz, df.index)
        if dir_rvol_source is not None else
        dir_logvolz
    )
    df["DirRVOL_d1"]  = df["DirRVOL63"].diff(1)
    df["DirRVOL_d5"]  = df["DirRVOL63"].diff(5)
    df["dist_sma21"]  = close / df["SMA21"] - 1
    df["dist_sma126"] = close / df["SMA126"] - 1
    df["dist_sma252"] = close / df["SMA252"] - 1
    df["ATR20"]       = compute_atr20(df)
    return df

# ---------------------------------------------------------------------------
# RankINT för senaste baren — ingen look-ahead
# ---------------------------------------------------------------------------

def rank_int_today(series: pd.Series) -> float:
    """
    RankINT z-score för senaste baren mot RANK_WINDOW-historik.

    Tar de sista RANK_WINDOW värdena (inklusive eventuella NaN vid starten
    pga warmup). rank_int() ignorerar NaN vid rankning. Returnerar NaN om
    senaste värdet är NaN eller om färre än MIN_PERIODS giltiga värden finns.
    """
    arr = series.values[-RANK_WINDOW:]
    if len(arr) == 0 or np.isnan(arr[-1]):
        return float("nan")
    valid_count = int(np.sum(~np.isnan(arr)))
    if valid_count < MIN_PERIODS:
        return float("nan")
    z = rank_int(arr)
    return float(z[-1])


def rank_int_at(series: pd.Series, pos: int) -> float:
    """RankINT z-score för bar `pos` mot historik fram till och med `pos`."""
    if pos < 0 or pos >= len(series):
        return float("nan")
    arr = series.iloc[max(0, pos - RANK_WINDOW + 1): pos + 1].to_numpy(dtype=float)
    if len(arr) == 0 or np.isnan(arr[-1]):
        return float("nan")
    valid_count = int(np.sum(~np.isnan(arr)))
    if valid_count < MIN_PERIODS:
        return float("nan")
    z = rank_int(arr)
    return float(z[-1])


def _freshness_state(age: int | None) -> tuple[str, int]:
    if age is None:
        return "NO_RECENT", 0
    if age <= 0:
        return "LIVE", 100
    if age <= 2:
        return "FRESH", 98 - age * 8
    if age <= 5:
        return "USABLE", 82 - age * 6
    if age <= 10:
        return "AGING", 58 - age * 3
    return "STALE", max(5, 28 - (age - 10) * 2)


def _signal_flags_at(
    df: pd.DataFrame,
    pos: int,
    inst_thresholds: dict | None = None,
    ticker: str = "",
) -> dict:
    """Historisk signalstatus utan look-ahead, beräknad endast med data ≤ `pos`."""
    row = df.iloc[pos]
    ri_rvol = rank_int_at(df["DirRVOL63"], pos)
    ri_rsi = rank_int_at(df["RSI9"], pos)
    ri_dist252 = rank_int_at(df["dist_sma252"], pos)
    ri_dist21 = rank_int_at(df["dist_sma21"], pos)
    ri_dist126 = rank_int_at(df["dist_sma126"], pos)

    sma_vals = {}
    for w in SMA_PERIODS:
        raw = row.get(f"SMA{w}", float("nan"))
        sma_vals[w] = float(raw) if not pd.isna(raw) else float("nan")
    close_raw = row.get("Close", float("nan"))
    close_val = float(close_raw) if not pd.isna(close_raw) else float("nan")
    bull_stack = (
        not any(np.isnan(v) for v in sma_vals.values()) and
        not np.isnan(close_val) and
        close_val > sma_vals[21] > sma_vals[52] > sma_vals[126] > sma_vals[252]
    )
    sma126_above_sma252 = (
        not (np.isnan(sma_vals[126]) or np.isnan(sma_vals[252])) and
        sma_vals[126] > sma_vals[252]
    )

    raw_rvol_raw = row.get("DirRVOL63", float("nan"))
    raw_rvol = float(raw_rvol_raw) if not pd.isna(raw_rvol_raw) else float("nan")
    raw_rvol_nan = np.isnan(raw_rvol)
    volume_block_reason = _volume_block_reason(ticker, inst_thresholds)
    use_per_inst = (
        volume_block_reason is None
        and
        inst_thresholds is not None
        and not inst_thresholds.get("fetch_fail")
        and not inst_thresholds.get("vol_fail")
        and inst_thresholds.get("rvol_p85") is not None
        and inst_thresholds.get("rvol_p70") is not None
    )
    if volume_block_reason is not None:
        cap_rvol_ok = False
        pb_rvol_ok = False
    elif use_per_inst:
        rvol_p85 = float(inst_thresholds["rvol_p85"])
        rvol_p70 = float(inst_thresholds["rvol_p70"])
        cap_rvol_ok = (not raw_rvol_nan) and raw_rvol <= -rvol_p85
        pb_rvol_ok = (not raw_rvol_nan) and raw_rvol >= rvol_p70
    else:
        cap_rvol_ok = (not np.isnan(ri_rvol)) and ri_rvol <= CAP_THRESHOLD
        pb_rvol_ok = (not np.isnan(ri_rvol)) and ri_rvol >= PB_VOL_THRESH

    cap = (
        cap_rvol_ok and
        (not np.isnan(ri_rsi)) and ri_rsi <= CAP_THRESHOLD and
        (not np.isnan(ri_dist252)) and ri_dist252 <= CAP_THRESHOLD
    )
    pb = (
        bull_stack and
        pb_rvol_ok and
        (not np.isnan(ri_rsi)) and ri_rsi <= PB_THRESHOLD and
        (not np.isnan(ri_dist21)) and ri_dist21 <= PB_THRESHOLD and
        ticker in PB_SMA21_WHITELIST
    )
    pb126 = (
        sma126_above_sma252 and
        pb_rvol_ok and
        (not np.isnan(ri_rsi)) and ri_rsi <= PB_THRESHOLD and
        (not np.isnan(ri_dist126)) and ri_dist126 <= PB_THRESHOLD
    )
    return {"cap": bool(cap), "pb": bool(pb), "pb126": bool(pb126)}


def compute_signal_freshness(
    df: pd.DataFrame,
    inst_thresholds: dict | None = None,
    ticker: str = "",
    *,
    lookback: int = SIGNAL_FRESHNESS_LOOKBACK,
) -> dict:
    """Senaste signalålder i handelsdagar. Kontextlager, inte nytt filter."""
    labels = {"cap": "CAP", "pb": "PB", "pb126": "PB126"}
    latest: dict[str, int | None] = {k: None for k in labels}
    end_pos = len(df) - 1
    start_pos = max(0, end_pos - lookback)
    for pos in range(end_pos, start_pos - 1, -1):
        flags = _signal_flags_at(df, pos, inst_thresholds=inst_thresholds, ticker=ticker)
        age = end_pos - pos
        for key, active in flags.items():
            if active and latest[key] is None:
                latest[key] = age
        if all(v is not None for v in latest.values()):
            break

    per_signal = {}
    for key, age in latest.items():
        state, score = _freshness_state(age)
        per_signal[key] = {
            "type": labels[key],
            "age": age,
            "state": state,
            "score": score,
            "label": f"{labels[key]} D+{age}" if age is not None else f"{labels[key]} none",
        }

    best = max(
        per_signal.values(),
        key=lambda x: (int(x["score"]), -999 if x["age"] is None else -int(x["age"])),
    )
    if best["age"] is None:
        return {
            "status": "NO_RECENT",
            "lookback": lookback,
            "best_type": None,
            "best_age": None,
            "best_state": "NO_RECENT",
            "score": 0,
            "signals": per_signal,
        }

    return {
        "status": "OK",
        "lookback": lookback,
        "best_type": best["type"],
        "best_age": best["age"],
        "best_state": best["state"],
        "score": best["score"],
        "signals": per_signal,
    }

# ---------------------------------------------------------------------------
# Signalevaluering — idag's bar
# ---------------------------------------------------------------------------

def eval_signals(df: pd.DataFrame, inst_thresholds: dict | None = None, ticker: str = "") -> dict:
    """
    Beräknar capitulation-, pullback- och PB SMA126-signaler för senaste baren.
    Alla beslut baseras enbart på data ≤ idag (inga look-ahead-buggar).

    inst_thresholds: per-instrument kalibrerade trösklar från scanner_universe.json.
      Om tillgängliga används dessa för DirRVOL63 (raw value vs calibrated percentile).
      RSI9 och dist_sma* använder alltid rank_int + universella Z_P70/Z_P85.
      Om inst_thresholds saknas eller är ofullständiga: fall back till rank_int universal.
    ticker: används för PB SMA21 whitelist-kontroll (§ 5b).
    """
    today = df.iloc[-1]

    ri_rvol    = rank_int_today(df["DirRVOL63"])
    ri_logvolz = rank_int_today(df["DirLogVolZ63"]) if "DirLogVolZ63" in df.columns else float("nan")
    ri_rvol_delta_1d = rank_int_today(df["DirRVOL_d1"]) if "DirRVOL_d1" in df.columns else float("nan")
    ri_rvol_delta_5d = rank_int_today(df["DirRVOL_d5"]) if "DirRVOL_d5" in df.columns else float("nan")
    ri_rsi     = rank_int_today(df["RSI9"])
    ri_dist252 = rank_int_today(df["dist_sma252"])
    ri_dist21  = rank_int_today(df["dist_sma21"])
    ri_dist126 = rank_int_today(df["dist_sma126"])

    # Bull-stack: Close > SMA21 > SMA52 > SMA126 > SMA252 (Close-check tillagd 2026-05-19, Bug #1-fix)
    sma_vals = {w: float(today[f"SMA{w}"]) for w in SMA_PERIODS}
    close_for_bull = float(today["Close"])
    bull_stack = (
        not any(np.isnan(v) for v in sma_vals.values()) and
        not np.isnan(close_for_bull) and
        close_for_bull > sma_vals[21] > sma_vals[52] > sma_vals[126] > sma_vals[252]
    )
    # SMA126 > SMA252: regim-filter för PB SMA126 (§ 5b.2) — svagare än full bull-stack
    sma126_above_sma252 = (
        not (np.isnan(sma_vals[126]) or np.isnan(sma_vals[252]))
        and sma_vals[126] > sma_vals[252]
    )

    # DirRVOL volume signal: per-instrument calibrated (raw value) or universal fallback
    raw_rvol = float(today.get("DirRVOL63", float("nan")))
    raw_rvol_nan = np.isnan(raw_rvol)
    raw_logvolz = float(today.get("DirLogVolZ63", float("nan")))
    raw_logvolz_nan = np.isnan(raw_logvolz)
    raw_rvol_delta_1d = float(today.get("DirRVOL_d1", float("nan")))
    raw_rvol_delta_5d = float(today.get("DirRVOL_d5", float("nan")))

    volume_block_reason = _volume_block_reason(ticker, inst_thresholds)
    use_per_inst = (
        volume_block_reason is None
        and
        inst_thresholds is not None
        and not inst_thresholds.get("fetch_fail")
        and not inst_thresholds.get("vol_fail")
        and inst_thresholds.get("rvol_p85") is not None
        and inst_thresholds.get("rvol_p70") is not None
    )

    if volume_block_reason is not None:
        cap_rvol_ok = False
        pb_rvol_ok = False
        cap_logvolz_ok = False
        pb_logvolz_ok = False
        threshold_mode = "volume-blocked"
    elif use_per_inst:
        rvol_p85 = float(inst_thresholds["rvol_p85"])
        rvol_p70 = float(inst_thresholds["rvol_p70"])
        logvolz_p85 = float(inst_thresholds.get("logvolz_p85", np.nan))
        logvolz_p70 = float(inst_thresholds.get("logvolz_p70", np.nan))
        cap_rvol_ok = (not raw_rvol_nan) and raw_rvol <= -rvol_p85
        pb_rvol_ok  = (not raw_rvol_nan) and raw_rvol >= rvol_p70
        cap_logvolz_ok = (
            np.isfinite(logvolz_p85) and
            (not raw_logvolz_nan) and
            raw_logvolz <= -logvolz_p85
        )
        pb_logvolz_ok = (
            np.isfinite(logvolz_p70) and
            (not raw_logvolz_nan) and
            raw_logvolz >= logvolz_p70
        )
        threshold_mode = "proxy-volume" if inst_thresholds.get("volume_source") == "proxy" else "per-instrument"
    else:
        # Fall back to rank_int universal thresholds
        cap_rvol_ok = (not np.isnan(ri_rvol)) and ri_rvol <= CAP_THRESHOLD
        pb_rvol_ok  = (not np.isnan(ri_rvol)) and ri_rvol >= PB_VOL_THRESH
        cap_logvolz_ok = (not np.isnan(ri_logvolz)) and ri_logvolz <= CAP_THRESHOLD
        pb_logvolz_ok = (not np.isnan(ri_logvolz)) and ri_logvolz >= PB_VOL_THRESH
        threshold_mode = "universal-fallback"

    # Capitulation: DirRVOL(per-inst) + RSI9(rank_int) + dist_sma252(rank_int) ≤ -Z_P85
    cap_rsi_ok  = (not np.isnan(ri_rsi)) and ri_rsi <= CAP_THRESHOLD
    cap_dist_ok = (not np.isnan(ri_dist252)) and ri_dist252 <= CAP_THRESHOLD
    cap_trigger = cap_rvol_ok and cap_rsi_ok and cap_dist_ok
    cap_logvolz_trigger = cap_logvolz_ok and cap_rsi_ok and cap_dist_ok

    # Pullback SMA21: Bull-stack + DirRVOL(per-inst) + dist_sma21(rank_int) + RSI9(rank_int) ≤ -Z_P70
    pb_dist_ok = (not np.isnan(ri_dist21)) and ri_dist21 <= PB_THRESHOLD
    pb_rsi_ok  = (not np.isnan(ri_rsi)) and ri_rsi <= PB_THRESHOLD
    # § 5b whitelist: PB SMA21 operativ bara på validerade instrument (PROBATIONÄR)
    pb_trigger = bull_stack and pb_rvol_ok and pb_dist_ok and pb_rsi_ok and (ticker in PB_SMA21_WHITELIST)

    # PB SMA126 (§ 5b.2 VALIDERAD 5/5): SMA126>SMA252 + dist_sma126 ≤ -Z_P70 + RSI9 ≤ -Z_P70 + DirRVOL ≥ +Z_P70
    pb126_dist_ok = (not np.isnan(ri_dist126)) and ri_dist126 <= PB_THRESHOLD
    pb126_rsi_ok  = pb_rsi_ok   # samma ri_rsi-kontroll
    pb126_rvol_ok = pb_rvol_ok  # samma DirRVOL-kontroll (per-inst eller universal fallback)
    pb126_trigger = sma126_above_sma252 and pb126_dist_ok and pb126_rsi_ok and pb126_rvol_ok
    pb126_logvolz_trigger = sma126_above_sma252 and pb126_dist_ok and pb126_rsi_ok and pb_logvolz_ok

    # Near-miss-räkning per signal (tillagt 2026-05-19 — så scanner exponerar
    # närmast-grön per ticker, inte bara FULL signal-aktivering).
    # Gap är hur långt z-värdet är fel sida om tröskeln (0 om uppfyllt).
    def _gap_under(v: float, thr: float) -> float:
        if np.isnan(v): return float("nan")
        return 0.0 if v <= thr else (v - thr)
    def _gap_over(v: float, thr: float) -> float:
        if np.isnan(v): return float("nan")
        return 0.0 if v >= thr else (thr - v)
    cap_count = int(cap_rvol_ok) + int(cap_rsi_ok) + int(cap_dist_ok)
    pb_count  = int(pb_rvol_ok)  + int(pb_rsi_ok)  + int(pb_dist_ok)
    pb126_count = int(pb126_rvol_ok) + int(pb126_rsi_ok) + int(pb126_dist_ok)
    if volume_block_reason is not None:
        cap_rvol_score = float("nan")
        pb_rvol_score = float("nan")
        cap_rvol_gap = float("nan")
        pb_rvol_gap = float("nan")
    elif use_per_inst:
        cap_rvol_score = (-raw_rvol / max(abs(rvol_p85), 1e-9)) if not raw_rvol_nan else float("nan")
        pb_rvol_score = (raw_rvol / max(abs(rvol_p70), 1e-9)) if not raw_rvol_nan else float("nan")
        cap_rvol_gap = _gap_over(cap_rvol_score, 1.0)
        pb_rvol_gap = _gap_over(pb_rvol_score, 1.0)
    else:
        cap_rvol_score = ri_rvol
        pb_rvol_score = ri_rvol
        cap_rvol_gap = _gap_under(ri_rvol, CAP_THRESHOLD)
        pb_rvol_gap = _gap_over(ri_rvol, PB_VOL_THRESH)

    cap_gap_total = sum(g for g in [
        cap_rvol_gap,
        _gap_under(ri_rsi, CAP_THRESHOLD),
        _gap_under(ri_dist252, CAP_THRESHOLD),
    ] if not np.isnan(g))
    pb126_gap_total = sum(g for g in [
        pb_rvol_gap,
        _gap_under(ri_rsi, PB_THRESHOLD),
        _gap_under(ri_dist126, PB_THRESHOLD),
    ] if not np.isnan(g))
    pb_gap_total = sum(g for g in [
        pb_rvol_gap,
        _gap_under(ri_rsi, PB_THRESHOLD),
        _gap_under(ri_dist21, PB_THRESHOLD),
    ] if not np.isnan(g))

    def safe(v: float, key: str) -> float:
        raw = today.get(key, float("nan"))
        return float(raw) if not pd.isna(raw) else float("nan")

    atr20_raw = today.get("ATR20", float("nan"))
    atr20     = float(atr20_raw) if not pd.isna(atr20_raw) else float("nan")
    close_val = float(today["Close"])
    atr20_pct = atr20 / close_val if (not np.isnan(atr20) and close_val > 0) else float("nan")
    open_val_raw = today.get("Open", float("nan"))
    open_val = float(open_val_raw) if not pd.isna(open_val_raw) else float("nan")
    prev_close = (
        float(df["Close"].iloc[-2])
        if len(df) >= 2 and not pd.isna(df["Close"].iloc[-2])
        else float("nan")
    )
    open_gap_pct = (
        ((open_val / prev_close) - 1) * 100
        if not (np.isnan(open_val) or np.isnan(prev_close) or prev_close == 0)
        else float("nan")
    )
    gap_state = price_gap_state(open_gap_pct)

    # RSI är momentum (Cardwell), inte mean-reversion. Delta visar
    # accelererande vs avtagande momentum mellan barer. d1 = dag-till-dag,
    # d5 = vecka-till-vecka (≈ 5 handelsdagar).
    rsi9_today = safe(0.0, "RSI9")
    rsi9_series = df["RSI9"]
    rsi21_today = safe(0.0, "RSI21")
    rsi21_series = df["RSI21"] if "RSI21" in df.columns else pd.Series(dtype=float)
    rsi63_today = safe(0.0, "RSI63")
    rsi63_series = df["RSI63"] if "RSI63" in df.columns else pd.Series(dtype=float)
    rsi9_d1_prev = float(rsi9_series.iloc[-2]) if len(rsi9_series) >= 2 and not pd.isna(rsi9_series.iloc[-2]) else float("nan")
    rsi9_d5_prev = float(rsi9_series.iloc[-6]) if len(rsi9_series) >= 6 and not pd.isna(rsi9_series.iloc[-6]) else float("nan")
    rsi9_delta_1d = rsi9_today - rsi9_d1_prev if not (np.isnan(rsi9_today) or np.isnan(rsi9_d1_prev)) else float("nan")
    rsi9_delta_5d = rsi9_today - rsi9_d5_prev if not (np.isnan(rsi9_today) or np.isnan(rsi9_d5_prev)) else float("nan")
    rsi21_delta_5d = _series_last_delta(rsi21_series, 5)
    rsi21_delta_21d = _series_last_delta(rsi21_series, 21)
    rsi63_delta_21d = _series_last_delta(rsi63_series, 21)
    rsi63_delta_42d = _series_last_delta(rsi63_series, 42)
    ri_rsi21 = rank_int_today(rsi21_series) if len(rsi21_series) else float("nan")
    ri_rsi63 = rank_int_today(rsi63_series) if len(rsi63_series) else float("nan")
    ri_rsi63_delta_42d = rank_int_today(rsi63_series.diff(42)) if len(rsi63_series) else float("nan")
    freshness = compute_signal_freshness(df, inst_thresholds=inst_thresholds, ticker=ticker)

    def _num(v: float, d: int = 2) -> str:
        return "N/A" if np.isnan(v) else f"{v:.{d}f}"

    if use_per_inst and inst_thresholds and inst_thresholds.get("volume_source") == "proxy":
        volume_mode = f"proxy DirRVOL({inst_thresholds.get('volume_source_ticker', '?')})"
    else:
        volume_mode = "raw DirRVOL" if use_per_inst else "ri_RVOL"
    cap_volume_value = (
        f"blocked: {volume_block_reason}"
        if volume_block_reason is not None else
        f"{volume_mode} {_num(raw_rvol)} vs <= -p85"
        if use_per_inst else f"ri_RVOL {_num(ri_rvol)} <= {CAP_THRESHOLD:.2f}"
    )
    pb_volume_value = (
        f"blocked: {volume_block_reason}"
        if volume_block_reason is not None else
        f"{volume_mode} {_num(raw_rvol)} vs >= p70"
        if use_per_inst else f"ri_RVOL {_num(ri_rvol)} >= {PB_VOL_THRESH:.2f}"
    )

    def _alignment_entry(key: str, label: str, trigger: bool, gap: float, criteria: list[dict]) -> dict:
        count = sum(1 for c in criteria if c["ok"])
        total = len(criteria)
        missing = [c["label"] for c in criteria if not c["ok"]]
        if trigger:
            state = "GREEN"
            action = "Review long"
        elif count >= max(1, total - 1):
            state = "NEAR"
            action = f"Wait for {missing[0]}" if missing else "Review"
        elif count >= max(1, total // 2):
            state = "WATCH"
            action = "Not mature"
        else:
            state = "OFF"
            action = "No setup"
        return {
            "key": key,
            "label": label,
            "side": "LONG_WATCH",
            "state": state,
            "action": action,
            "trigger": bool(trigger),
            "count": int(count),
            "total": int(total),
            "gap": float(gap) if not np.isnan(gap) else None,
            "missing": missing,
            "criteria": criteria,
        }

    alignment = {
        "cap": _alignment_entry("cap", "CAP", cap_trigger, cap_gap_total, [
            {"label": "Volume flush", "ok": bool(cap_rvol_ok), "value": cap_volume_value},
            {"label": "RSI washout", "ok": bool(cap_rsi_ok), "value": f"ri_RSI {_num(ri_rsi)} <= {CAP_THRESHOLD:.2f}"},
            {"label": "SMA252 stretch", "ok": bool(cap_dist_ok), "value": f"ri_d252 {_num(ri_dist252)} <= {CAP_THRESHOLD:.2f}"},
        ]),
        "pb126": _alignment_entry("pb126", "PB126", pb126_trigger, pb126_gap_total, [
            {"label": "126 regime", "ok": bool(sma126_above_sma252), "value": "SMA126 > SMA252"},
            {"label": "Buy volume", "ok": bool(pb126_rvol_ok), "value": pb_volume_value},
            {"label": "RSI pullback", "ok": bool(pb126_rsi_ok), "value": f"ri_RSI {_num(ri_rsi)} <= {PB_THRESHOLD:.2f}"},
            {"label": "SMA126 pullback", "ok": bool(pb126_dist_ok), "value": f"ri_d126 {_num(ri_dist126)} <= {PB_THRESHOLD:.2f}"},
        ]),
        "pb": _alignment_entry("pb", "PB", pb_trigger, pb_gap_total, [
            {"label": "Whitelist", "ok": ticker in PB_SMA21_WHITELIST, "value": "PB SMA21 operativ"},
            {"label": "Bull stack", "ok": bool(bull_stack), "value": "Close > 21 > 52 > 126 > 252"},
            {"label": "Buy volume", "ok": bool(pb_rvol_ok), "value": pb_volume_value},
            {"label": "RSI pullback", "ok": bool(pb_rsi_ok), "value": f"ri_RSI {_num(ri_rsi)} <= {PB_THRESHOLD:.2f}"},
            {"label": "SMA21 pullback", "ok": bool(pb_dist_ok), "value": f"ri_d21 {_num(ri_dist21)} <= {PB_THRESHOLD:.2f}"},
        ]),
    }
    best_alignment = sorted(
        alignment.values(),
        key=lambda x: (
            0 if x["trigger"] else 1,
            -(x["count"] / max(1, x["total"])),
            x["gap"] if x["gap"] is not None else 999.0,
        ),
    )[0]
    alignment["best"] = {
        "key": best_alignment["key"],
        "label": best_alignment["label"],
        "state": best_alignment["state"],
        "action": best_alignment["action"],
        "count": best_alignment["count"],
        "total": best_alignment["total"],
        "gap": best_alignment["gap"],
        "missing": best_alignment["missing"],
    }

    return {
        "close":               close_val,
        "open":                open_val,
        "prev_close":          prev_close,
        "open_gap_pct":        open_gap_pct,
        "gap_risk":            gap_state,
        "sma21":               sma_vals[21],
        "sma52":               sma_vals[52],
        "sma126":              sma_vals[126],
        "sma252":              sma_vals[252],
        "rsi9":                rsi9_today,
        "rsi21":               rsi21_today,
        "rsi63":               rsi63_today,
        "rsi9_delta_1d":       rsi9_delta_1d,
        "rsi9_delta_5d":       rsi9_delta_5d,
        "rsi21_delta_5d":      rsi21_delta_5d,
        "rsi21_delta_21d":     rsi21_delta_21d,
        "rsi63_delta_21d":     rsi63_delta_21d,
        "rsi63_delta_42d":     rsi63_delta_42d,
        "dir_rvol63":          raw_rvol,
        "dir_logvolz63":       raw_logvolz,
        "dir_rvol_delta_1d":   raw_rvol_delta_1d,
        "dir_rvol_delta_5d":   raw_rvol_delta_5d,
        "dist_sma21":          safe(0.0, "dist_sma21"),
        "dist_sma126":         safe(0.0, "dist_sma126"),
        "dist_sma252":         safe(0.0, "dist_sma252"),
        "ri_rvol":             ri_rvol,
        "ri_logvolz":          ri_logvolz,
        "ri_rvol_delta_1d":    ri_rvol_delta_1d,
        "ri_rvol_delta_5d":    ri_rvol_delta_5d,
        "ri_rsi":              ri_rsi,
        "ri_rsi21":            ri_rsi21,
        "ri_rsi63":            ri_rsi63,
        "ri_rsi63_delta_42d":  ri_rsi63_delta_42d,
        "ri_dist252":          ri_dist252,
        "ri_dist21":           ri_dist21,
        "ri_dist126":          ri_dist126,
        "cap_rvol_score":      cap_rvol_score,
        "pb_rvol_score":       pb_rvol_score,
        "bull_stack":          bull_stack,
        "sma126_above_sma252": sma126_above_sma252,
        "cap_trigger":         cap_trigger,
        "cap_logvolz_trigger": cap_logvolz_trigger,
        "pb_trigger":          pb_trigger,
        "pb126_trigger":       pb126_trigger,
        "pb126_logvolz_trigger": pb126_logvolz_trigger,
        "cap_logvolz_ok":      cap_logvolz_ok,
        "pb_logvolz_ok":       pb_logvolz_ok,
        "cap_count":           cap_count,
        "pb_count":            pb_count,
        "pb126_count":         pb126_count,
        "cap_gap_total":       cap_gap_total,
        "pb_gap_total":        pb_gap_total,
        "pb126_gap_total":     pb126_gap_total,
        "bull_count":          int(bull_stack),  # binär — för UI completeness
        "threshold_mode":      threshold_mode,
        "volume_trust":        (
            "BLOCKED" if volume_block_reason is not None else
            "PROXY" if inst_thresholds and inst_thresholds.get("volume_source") == "proxy" else
            "OK"
        ),
        "volume_reason":       (
            volume_block_reason or
            (
                f"DirRVOL from {(inst_thresholds or {}).get('volume_source_ticker')}; price from {ticker}"
                if inst_thresholds and inst_thresholds.get("volume_source") == "proxy" else
                ""
            )
        ),
        "volume_source_ticker": (inst_thresholds or {}).get("volume_source_ticker"),
        "atr20":               atr20,
        "atr20_pct":           atr20_pct,
        "freshness":           freshness,
        "alignment":           alignment,
        "manual_checks":       list(MANUAL_TRADE_CHECKS),
        "trade_readiness":     "MANUAL_CHECK_REQUIRED" if (cap_trigger or pb_trigger or pb126_trigger) else "WATCH_ONLY",
    }


# ---------------------------------------------------------------------------
# Setup Genome / Historical Analog Engine
# ---------------------------------------------------------------------------

def compute_analog_expectancy(
    df: pd.DataFrame,
    signals: dict,
    inst_thresholds: dict | None = None,
    ticker: str = "",
    *,
    top_n: int = ANALOG_TOP_N,
    min_n: int = ANALOG_MIN_N,
) -> dict:
    """
    Hitta historiska bars som liknar dagens setup och räkna forward-expectancy.

    Viktigt: kandidater begränsas till bars där 63d forward-return redan är känd
    (`iloc[:-63]`). Dagens bar och de senaste 63 bars används aldrig som analog-
    outcomes. Analogmotorn är därför ett decision-support-lager, inte look-ahead.
    """
    max_h = max(ANALOG_FWD_DAYS)
    if len(df) <= max_h + MIN_PERIODS:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "n": 0,
            "candidate_pool": 0,
            "verdict": "NO_SAMPLE",
            "confidence": "LOW",
        }

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    work = pd.DataFrame(index=df.index)
    work["close"] = close
    work["rsi9"] = df["RSI9"].astype(float)
    work["rsi_delta_1d"] = work["rsi9"].diff(1)
    work["rsi_delta_5d"] = work["rsi9"].diff(5)
    work["dirvol63"] = df["DirRVOL63"].astype(float)
    work["dist_sma21"] = df["dist_sma21"].astype(float)
    work["dist_sma126"] = df["dist_sma126"].astype(float)
    work["dist_sma252"] = df["dist_sma252"].astype(float)
    work["atr20_pct"] = (df["ATR20"].astype(float) / close).replace([np.inf, -np.inf], np.nan)
    work["bull_stack"] = (
        (close > df["SMA21"]) &
        (df["SMA21"] > df["SMA52"]) &
        (df["SMA52"] > df["SMA126"]) &
        (df["SMA126"] > df["SMA252"])
    )
    work["sma126_above_sma252"] = df["SMA126"] > df["SMA252"]

    volume_block_reason = _volume_block_reason(ticker, inst_thresholds)
    feature_cols = [
        "rsi9", "rsi_delta_1d", "rsi_delta_5d", "dirvol63",
        "dist_sma21", "dist_sma126", "dist_sma252", "atr20_pct",
    ]
    if volume_block_reason is not None:
        feature_cols.remove("dirvol63")
    current = work.iloc[-1]
    if current[feature_cols].isna().any() or pd.isna(current["close"]):
        return {
            "status": "INSUFFICIENT_FEATURES",
            "n": 0,
            "candidate_pool": 0,
            "verdict": "NO_SAMPLE",
            "confidence": "LOW",
        }

    candidates = work.iloc[:-max_h].dropna(subset=feature_cols + ["close"]).copy()
    candidate_pool = int(len(candidates))
    if candidate_pool < min_n:
        return {
            "status": "INSUFFICIENT_ANALOGS",
            "n": 0,
            "candidate_pool": candidate_pool,
            "verdict": "NO_SAMPLE",
            "confidence": "LOW",
        }

    rvol_scale = 1.0
    if inst_thresholds and volume_block_reason is None:
        vals = [
            abs(float(inst_thresholds.get(k, np.nan)))
            for k in ("rvol_p70", "rvol_p85", "rvol_p95")
        ]
        vals = [v for v in vals if np.isfinite(v) and v > 0]
        if vals:
            rvol_scale = max(0.5, float(np.median(vals)))

    scales = {
        "rsi9": 18.0,
        "rsi_delta_1d": 8.0,
        "rsi_delta_5d": 16.0,
        "dirvol63": rvol_scale,
        "dist_sma21": 0.05,
        "dist_sma126": 0.12,
        "dist_sma252": 0.22,
        "atr20_pct": 0.04,
    }
    weights = {
        "rsi9": 1.00,
        "rsi_delta_1d": 0.35,
        "rsi_delta_5d": 0.75,
        "dirvol63": 1.00,
        "dist_sma21": 0.45,
        "dist_sma126": 1.00,
        "dist_sma252": 0.85,
        "atr20_pct": 0.30,
    }
    if volume_block_reason is not None:
        scales.pop("dirvol63", None)
        weights.pop("dirvol63", None)

    total_w = sum(weights.values())
    dist_sq = pd.Series(0.0, index=candidates.index)
    for col in feature_cols:
        delta = (candidates[col] - float(current[col])) / scales[col]
        dist_sq = dist_sq + weights[col] * (delta ** 2)
    distance = np.sqrt(dist_sq / total_w)
    distance = distance + np.where(candidates["bull_stack"] != bool(current["bull_stack"]), 0.55, 0.0)
    distance = distance + np.where(
        candidates["sma126_above_sma252"] != bool(current["sma126_above_sma252"]),
        0.25,
        0.0,
    )

    top_idx = pd.Series(distance, index=candidates.index).sort_values().head(top_n).index
    selected = candidates.loc[top_idx].copy()
    selected["distance"] = pd.Series(distance, index=candidates.index).loc[top_idx]

    close_arr = close.to_numpy(dtype=float)
    high_arr = high.to_numpy(dtype=float)
    low_arr = low.to_numpy(dtype=float)
    idx_to_pos = {idx: pos for pos, idx in enumerate(df.index)}
    rows = []
    for idx, row in selected.iterrows():
        pos = idx_to_pos[idx]
        entry = close_arr[pos]
        if not np.isfinite(entry) or entry <= 0:
            continue
        outcome = {"distance": float(row["distance"])}
        valid = True
        for h in ANALOG_FWD_DAYS:
            fwd = close_arr[pos + h] / entry - 1.0
            if not np.isfinite(fwd):
                valid = False
                break
            outcome[f"fwd{h}"] = float(fwd)
        if not valid:
            continue
        path_lows = low_arr[pos + 1: pos + 22]
        finite_lows = path_lows[np.isfinite(path_lows)]
        path_low = float(np.min(finite_lows)) if len(finite_lows) else float("nan")
        outcome["mae21"] = float(path_low / entry - 1.0) if np.isfinite(path_low) else float("nan")

        path_highs = high_arr[pos + 1: pos + 22]
        finite_highs = path_highs[np.isfinite(path_highs)]
        path_high = float(np.max(finite_highs)) if len(finite_highs) else float("nan")
        outcome["mfe21"] = float(path_high / entry - 1.0) if np.isfinite(path_high) else float("nan")

        five_lows = low_arr[pos + 1: pos + 6]
        finite_five_lows = five_lows[np.isfinite(five_lows)]
        outcome["best_entry_delay"] = int(np.nanargmin(five_lows) + 1) if len(finite_five_lows) else None
        rows.append(outcome)

    n = len(rows)
    if n < min_n:
        return {
            "status": "INSUFFICIENT_ANALOGS",
            "n": n,
            "candidate_pool": candidate_pool,
            "verdict": "NO_SAMPLE",
            "confidence": "LOW",
        }

    def _vals(key: str) -> np.ndarray:
        return np.array([r[key] for r in rows if r.get(key) is not None and np.isfinite(r[key])], dtype=float)

    fwd5 = _vals("fwd5")
    fwd21 = _vals("fwd21")
    fwd63 = _vals("fwd63")
    mae21 = _vals("mae21")
    mfe21 = _vals("mfe21")
    distances = _vals("distance")
    delays = np.array([r["best_entry_delay"] for r in rows if r.get("best_entry_delay") is not None], dtype=float)

    fwd21_mean = float(np.mean(fwd21))
    fwd21_hit = float(np.mean(fwd21 > 0))
    median_distance = float(np.median(distances))
    mae21_p10 = float(np.percentile(mae21, 10)) if len(mae21) else float("nan")
    fwd21_p75 = float(np.percentile(fwd21, 75))
    shakeout_5pct = float(np.mean(mae21 <= -0.05)) if len(mae21) else float("nan")
    shakeout_10pct = float(np.mean(mae21 <= -0.10)) if len(mae21) else float("nan")
    reward_to_pain = (
        float(fwd21_p75 / abs(mae21_p10))
        if np.isfinite(fwd21_p75) and fwd21_p75 > 0 and np.isfinite(mae21_p10) and mae21_p10 < 0
        else None
    )
    if not np.isfinite(mae21_p10):
        path_risk = "UNKNOWN"
    elif mae21_p10 <= -0.12 or (np.isfinite(shakeout_10pct) and shakeout_10pct >= 0.25):
        path_risk = "EXTREME"
    elif mae21_p10 <= -0.08 or (np.isfinite(shakeout_5pct) and shakeout_5pct >= 0.45):
        path_risk = "HIGH"
    elif mae21_p10 <= -0.045 or (np.isfinite(shakeout_5pct) and shakeout_5pct >= 0.25):
        path_risk = "MEDIUM"
    else:
        path_risk = "LOW"
    path_risk_score = {
        "LOW": 95,
        "MEDIUM": 70,
        "HIGH": 42,
        "EXTREME": 18,
        "UNKNOWN": 0,
    }[path_risk]

    if n >= 20 and median_distance <= 1.15:
        confidence = "HIGH"
    elif n >= 15 and median_distance <= 1.45:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    if fwd21_mean >= 0.03 and fwd21_hit >= 0.62 and (np.isnan(mae21_p10) or mae21_p10 > -0.10):
        verdict = "TAILWIND"
    elif fwd21_mean >= 0.012 and fwd21_hit >= 0.55:
        verdict = "POSITIVE"
    elif fwd21_mean <= -0.012 or fwd21_hit <= 0.45:
        verdict = "HEADWIND"
    else:
        verdict = "MIXED"

    current_regime = (
        "BULL" if bool(current["bull_stack"]) else
        "126+" if bool(current["sma126_above_sma252"]) else
        "BEAR"
    )

    return {
        "status": "OK",
        "verdict": verdict,
        "confidence": confidence,
        "n": int(n),
        "candidate_pool": candidate_pool,
        "current_regime": current_regime,
        "median_distance": median_distance,
        "fwd5_mean": float(np.mean(fwd5)),
        "fwd21_mean": fwd21_mean,
        "fwd21_median": float(np.median(fwd21)),
        "fwd21_hit_rate": fwd21_hit,
        "fwd21_p25": float(np.percentile(fwd21, 25)),
        "fwd21_p75": fwd21_p75,
        "fwd63_mean": float(np.mean(fwd63)),
        "mae21_median": float(np.median(mae21)) if len(mae21) else None,
        "mae21_p10": mae21_p10 if len(mae21) else None,
        "mfe21_median": float(np.median(mfe21)) if len(mfe21) else None,
        "mfe21_p90": float(np.percentile(mfe21, 90)) if len(mfe21) else None,
        "shakeout_5pct_rate": shakeout_5pct if np.isfinite(shakeout_5pct) else None,
        "shakeout_10pct_rate": shakeout_10pct if np.isfinite(shakeout_10pct) else None,
        "reward_to_pain": reward_to_pain,
        "path_risk": path_risk,
        "path_risk_score": path_risk_score,
        "best_entry_delay_median": float(np.median(delays)) if len(delays) else None,
        "volume_mode": (
            "price-only" if volume_block_reason is not None else
            "proxy-volume" if inst_thresholds and inst_thresholds.get("volume_source") == "proxy" else
            "with-volume"
        ),
        "volume_reason": volume_block_reason or "",
    }


# ---------------------------------------------------------------------------
# 95% regime research overlay
# ---------------------------------------------------------------------------

def _as_bool(value) -> bool:
    return bool(value) if value is not None and not pd.isna(value) else False


def _series_last_float(series: pd.Series, default: float = float("nan")) -> float:
    if series is None or series.empty:
        return default
    raw = series.iloc[-1]
    return float(raw) if not pd.isna(raw) else default


def _series_last_delta(series: pd.Series, horizon: int, default: float = float("nan")) -> float:
    if series is None or len(series) <= horizon:
        return default
    current = series.iloc[-1]
    prior = series.iloc[-1 - horizon]
    if pd.isna(current) or pd.isna(prior):
        return default
    return float(current) - float(prior)


def _trailing_true_run(mask: pd.Series) -> int:
    if mask is None or mask.empty:
        return 0
    run = 0
    for ok in reversed(mask.fillna(False).astype(bool).tolist()):
        if not ok:
            break
        run += 1
    return int(run)


def _normalised_series(series: pd.Series) -> pd.Series:
    out = series.copy()
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _has_calibrated_rvol(inst_thresholds: dict | None, ticker: str = "") -> bool:
    if _volume_block_reason(ticker, inst_thresholds) is not None:
        return False
    if inst_thresholds and not inst_thresholds.get("fetch_fail") and not inst_thresholds.get("vol_fail"):
        r70 = inst_thresholds.get("rvol_p70")
        r85 = inst_thresholds.get("rvol_p85")
        return r70 is not None and r85 is not None
    return False


def _sell_flush_series(df: pd.DataFrame, inst_thresholds: dict | None, ticker: str = "") -> pd.Series:
    if _volume_block_reason(ticker, inst_thresholds) is not None:
        return pd.Series(False, index=df.index)
    if _has_calibrated_rvol(inst_thresholds, ticker=ticker):
        rvol_p85 = float(inst_thresholds["rvol_p85"])
        return df["DirRVOL63"].astype(float).le(-rvol_p85).fillna(False)
    flags = [
        bool(np.isfinite(ri) and ri <= CAP_THRESHOLD)
        for ri in (rank_int_at(df["DirRVOL63"], pos) for pos in range(len(df)))
    ]
    return pd.Series(flags, index=df.index)


def _downstack_liq_at(
    df: pd.DataFrame,
    pos: int,
    atr_p85: pd.Series,
) -> bool:
    if pos < 0 or pos >= len(df):
        return False
    row = df.iloc[pos]
    vals = [row.get(f"SMA{w}", np.nan) for w in SMA_PERIODS]
    close = row.get("Close", np.nan)
    if pd.isna(close) or any(pd.isna(v) for v in vals):
        return False
    sma21, sma52, sma126, sma252 = [float(v) for v in vals]
    downstack = float(close) < sma21 < sma52 < sma126 < sma252
    if not downstack:
        return False
    atr20 = row.get("ATR20", np.nan)
    atr20_pct = float(atr20) / float(close) if not pd.isna(atr20) and float(close) > 0 else np.nan
    atr_p85_val = atr_p85.iloc[pos] if pos < len(atr_p85) else np.nan
    liquidation_risk = (
        np.isfinite(atr20_pct) and
        (atr20_pct > 0.04 or (np.isfinite(atr_p85_val) and atr20_pct >= float(atr_p85_val)))
    )
    if not liquidation_risk:
        return False
    ri_dist252 = rank_int_at(df["dist_sma252"], pos)
    return bool(np.isfinite(ri_dist252) and ri_dist252 <= CAP_THRESHOLD)


def _build_region_overlays(results: list[dict], feature_frames: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
    region_overlays: dict[str, dict] = {}
    ticker_rs: dict[str, dict] = {}

    for region in sorted({r.get("region", "?") for r in results if r.get("region")}):
        tickers = [
            r["ticker"] for r in results
            if r.get("region") == region and not r.get("error") and not r.get("skipped") and r.get("ticker") in feature_frames
        ]
        if len(tickers) < 2:
            continue

        close_series = []
        for ticker in tickers:
            s = _normalised_series(feature_frames[ticker]["Close"].astype(float)).rename(ticker)
            close_series.append(s)
        pivot = pd.concat(close_series, axis=1).sort_index()
        returns = pivot.pct_change(fill_method=None)
        min_names = max(2, min(4, int(pivot.shape[1] // 3) or 1))
        breadth = returns.notna().sum(axis=1)
        median_return = returns.median(axis=1, skipna=True).where(breadth >= min_names)
        region_close = ((1.0 + median_return.fillna(0.0)).cumprod() * 100.0).where(breadth >= min_names).ffill()
        region_close = region_close.dropna()
        if len(region_close) < max(SMA_PERIODS):
            continue

        region_sma = {w: region_close.rolling(w, min_periods=w).mean() for w in SMA_PERIODS}
        last_close = _series_last_float(region_close)
        last_sma = {w: _series_last_float(s) for w, s in region_sma.items()}
        region_bull = (
            np.isfinite(last_close) and
            not any(np.isnan(v) for v in last_sma.values()) and
            last_close > last_sma[21] > last_sma[52] > last_sma[126] > last_sma[252]
        )
        region_126 = (
            not region_bull and
            np.isfinite(last_sma[126]) and np.isfinite(last_sma[252]) and
            last_sma[126] > last_sma[252]
        )
        region_bear = not region_bull and not region_126
        label = "BULL" if region_bull else "126+" if region_126 else "BEAR"
        region_overlays[region] = {
            "label": label,
            "region_bull": bool(region_bull),
            "region_126": bool(region_126),
            "region_bear": bool(region_bear),
            "breadth": int(breadth.iloc[-1]) if len(breadth) else 0,
        }

        for ticker in tickers:
            close = _normalised_series(feature_frames[ticker]["Close"].astype(float)).rename("close")
            rs_frame = pd.concat([close, region_close.rename("region")], axis=1).dropna()
            if len(rs_frame) < 63:
                continue
            rs_ratio = rs_frame["close"] / rs_frame["region"].replace(0, np.nan)
            rs_sma21 = rs_ratio.rolling(21, min_periods=21).mean()
            rs_above21 = rs_ratio.gt(rs_sma21)
            rs_mom21 = rs_ratio / rs_ratio.shift(21) - 1.0
            ticker_rs[ticker] = {
                "rs_mom21": _series_last_float(rs_mom21),
                "rs_above21": _as_bool(rs_above21.iloc[-1]) if len(rs_above21) else False,
                "rs_reclaim21": (
                    len(rs_above21) >= 2 and
                    _as_bool(rs_above21.iloc[-1]) and
                    not _as_bool(rs_above21.iloc[-2])
                ),
            }

    return region_overlays, ticker_rs


def _label(
    label_id: str,
    label: str,
    verdict: str,
    tone: str,
    priority: int,
    reason: str,
    stats: str,
) -> dict:
    return {
        "id": label_id,
        "label": label,
        "verdict": verdict,
        "tone": tone,
        "priority": int(priority),
        "reason": reason,
        "stats": stats,
    }


def _empty_regime_context(reason: str = "No 95% regime label") -> dict:
    return {
        "status": "OK",
        "primary": {
            "id": "no_95_regime",
            "label": "NO 95 REGIME",
            "verdict": "NEUTRAL",
            "tone": "neutral",
            "priority": 99,
            "reason": reason,
            "stats": "",
        },
        "labels": [],
        "flags": {},
    }


def _ticker_regime_context(
    ticker: str,
    region: str,
    df: pd.DataFrame,
    signals: dict,
    inst_thresholds: dict | None,
    region_overlay: dict | None,
    rs_overlay: dict | None,
) -> dict:
    if df is None or df.empty:
        return _empty_regime_context("No feature frame")

    close = float(signals.get("close", np.nan))
    sma21 = float(signals.get("sma21", np.nan))
    sma52 = float(signals.get("sma52", np.nan))
    sma126 = float(signals.get("sma126", np.nan))
    sma252 = float(signals.get("sma252", np.nan))
    bull = bool(signals.get("bull_stack"))
    transition_126 = (not bull) and bool(signals.get("sma126_above_sma252"))
    downstack = (
        np.isfinite(close) and
        not any(np.isnan(v) for v in [sma21, sma52, sma126, sma252]) and
        close < sma21 < sma52 < sma126 < sma252
    )

    atr_pct_series = (df["ATR20"].astype(float) / df["Close"].astype(float).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    atr_p85 = atr_pct_series.rolling(RANK_WINDOW, min_periods=MIN_PERIODS).quantile(0.85)
    atr20_pct = float(signals.get("atr20_pct", np.nan))
    atr_p85_today = _series_last_float(atr_p85)
    liquidation_risk = (
        np.isfinite(atr20_pct) and
        (atr20_pct > 0.04 or (np.isfinite(atr_p85_today) and atr20_pct >= atr_p85_today))
    )

    sell_flush = _sell_flush_series(df, inst_thresholds, ticker=ticker)
    flush5 = bool(sell_flush.tail(5).fillna(False).max())
    flush10 = bool(sell_flush.tail(10).fillna(False).max())

    ri_rsi = float(signals.get("ri_rsi", np.nan))
    ri_dist252 = float(signals.get("ri_dist252", np.nan))
    ri_dist126 = float(signals.get("ri_dist126", np.nan))
    rsi9 = float(signals.get("rsi9", np.nan))
    rsi_delta_5d = float(signals.get("rsi9_delta_5d", np.nan))
    rsi21_series = df["RSI21"].astype(float) if "RSI21" in df.columns else wilder_rsi(df["Close"].astype(float), 21)
    rsi63_series = df["RSI63"].astype(float) if "RSI63" in df.columns else wilder_rsi(df["Close"].astype(float), 63)
    rsi21 = float(signals.get("rsi21", _series_last_float(rsi21_series)))
    rsi63 = float(signals.get("rsi63", _series_last_float(rsi63_series)))
    rsi21_low = bool(np.isfinite(rsi21) and rsi21 <= 40)
    rsi21_low_run = _trailing_true_run(rsi21_series.le(40))
    rsi21_low_5d = bool(rsi21_low_run >= 5)
    deep_mr = bool(np.isfinite(rsi9) and np.isfinite(rsi21) and np.isfinite(rsi63) and rsi9 < 30 and rsi21 < 40 and rsi63 < 50)
    rsi63_delta_21d = float(signals.get("rsi63_delta_21d", _series_last_delta(rsi63_series, 21)))
    rsi63_delta_42d = float(signals.get("rsi63_delta_42d", _series_last_delta(rsi63_series, 42)))
    ri_rsi63_delta_42d = float(signals.get("ri_rsi63_delta_42d", rank_int_today(rsi63_series.diff(42))))
    ri_rsi63_delta_63d = rank_int_today(rsi63_series.diff(63))
    rsi63_d42_washout = bool(np.isfinite(ri_rsi63_delta_42d) and ri_rsi63_delta_42d <= -Z_P85)
    rsi63_d63_context = bool(np.isfinite(ri_rsi63_delta_63d) and ri_rsi63_delta_63d <= -Z_P85)
    oversold70 = np.isfinite(ri_rsi) and ri_rsi <= PB_THRESHOLD
    oversold85 = np.isfinite(ri_rsi) and ri_rsi <= CAP_THRESHOLD
    stretched252_85 = np.isfinite(ri_dist252) and ri_dist252 <= CAP_THRESHOLD
    pullback126 = np.isfinite(ri_dist126) and ri_dist126 <= PB_THRESHOLD
    delta5_up = np.isfinite(rsi_delta_5d) and rsi_delta_5d > 0

    end_pos = len(df) - 1
    prior_start = max(0, end_pos - 21)
    prior_liq_21 = any(
        _downstack_liq_at(df, pos, atr_p85)
        for pos in range(prior_start, end_pos)
    )

    downstack_liq = bool(downstack and liquidation_risk and stretched252_85)
    liq_flush = bool(liquidation_risk and flush5)
    downstack_drift = bool(downstack and not liquidation_risk and not stretched252_85)
    r126_pullback = bool(transition_126 and pullback126 and oversold70)

    region_overlay = region_overlay or {}
    rs_overlay = rs_overlay or {}
    region_label = region_overlay.get("label")
    region_126 = bool(region_overlay.get("region_126"))
    region_bear = bool(region_overlay.get("region_bear"))
    rs_above21 = bool(rs_overlay.get("rs_above21"))
    rs_reclaim21 = bool(rs_overlay.get("rs_reclaim21"))
    rs_mom21 = rs_overlay.get("rs_mom21")
    rs_strength = bool(rs_above21 and isinstance(rs_mom21, (int, float)) and np.isfinite(rs_mom21) and rs_mom21 > 0)
    rs_weak = bool((not rs_above21) and isinstance(rs_mom21, (int, float)) and np.isfinite(rs_mom21) and rs_mom21 < 0)

    post_liq_rsi50 = bool(prior_liq_21 and np.isfinite(rsi9) and rsi9 > 50 and delta5_up)
    post_liq_rs_reclaim = bool(prior_liq_21 and rs_reclaim21 and delta5_up)
    parent_bear_liq = bool(downstack_liq and region_bear)
    parent_126_pb = bool(r126_pullback and region_126)
    liq_rs_weak = bool(downstack_liq and rs_weak)
    liquidation_rsi21_low = bool(liquidation_risk and rsi21_low)
    transition126_rsi21_low = bool(transition_126 and rsi21_low)
    transition126_deep_mr = bool(transition_126 and deep_mr)
    region_126_rsi21_low = bool(region_126 and rsi21_low)
    rs_weak_rsi21_low = bool(rs_weak and rsi21_low)
    rs_strength_rsi21_low = bool(rs_strength and rsi21_low)

    labels: list[dict] = []
    cap_trigger = bool(signals.get("cap_trigger"))
    pb126_trigger = bool(signals.get("pb126_trigger"))

    if pb126_trigger and region_bear:
        labels.append(_label(
            "pb126_parent_bear_blocker", "PB126 PARENT BEAR BLOCKER", "BLOCKER", "blocker", 0,
            "PB126 i parent bear var historiskt sämre än PB126-baslinjen.",
            "PB126 parent bear: fwd21 -0.43%, hit 47.5%",
        ))
    if pb126_trigger and np.isfinite(rsi63_delta_21d) and rsi63_delta_21d > 0:
        labels.append(_label(
            "pb126_rsi63_repair_blocker", "PB126 RSI63 REPAIR BLOCKER", "BLOCKER", "blocker", 0,
            "PB126 med stigande RSI63 har historiskt försämrat signalens profil.",
            "PB126 + RSI63 repair: fwd21 -0.68%, hit 46.7%, diff -2.12%",
        ))
    if cap_trigger and liq_flush:
        labels.append(_label(
            "cap_flush_boost", "CAP FLUSH BOOST", "95_SIGNAL_BOOST", "boost", 1,
            "CAP sammanfaller med liquidation flush.",
            "CAP in liq_flush: fwd21 +6.46%, hit 82.5%",
        ))
    if cap_trigger and rsi21_low_run >= 10:
        labels.append(_label(
            "cap_rsi21_low_10d_boost", "CAP RSI21 10D BOOST", "95_SIGNAL_BOOST", "boost", 1,
            "CAP sammanfaller med ihållande RSI21-stress.",
            "CAP + RSI21<=40 10d: fwd21 +6.45%, hit 86.4%, diff +4.76%",
        ))
    if cap_trigger and downstack_liq:
        labels.append(_label(
            "cap_downstack_liq_boost", "CAP LIQ BOOST", "95_SIGNAL_BOOST", "boost", 2,
            "CAP sammanfaller med downstack liquidation.",
            "CAP in downstack_liq: fwd21 +6.08%, hit 82.8%",
        ))
    if cap_trigger and region_126_rsi21_low:
        labels.append(_label(
            "cap_region126_rsi21_boost", "CAP 126+ RSI21 BOOST", "95_SIGNAL_BOOST", "boost", 2,
            "CAP sker i parent 126+-regim med RSI21-stress.",
            "CAP + Region126 + RSI21 low: fwd21 +4.55%, hit 73.7%, diff +2.86%",
        ))
    if pb126_trigger and rs_strength_rsi21_low:
        labels.append(_label(
            "pb126_rs_strength_rsi21_boost", "PB126 RS RSI21 BOOST", "95_SIGNAL_BOOST", "boost", 2,
            "PB126 sammanfaller med relativ styrka och RSI21-stress.",
            "PB126 + RS strength + RSI21 low: fwd21 +5.53%, hit 83.3%, diff +4.09%",
        ))
    if downstack_drift:
        labels.append(_label(
            "downstack_drift_avoid", "DOWNSTACK DRIFT AVOID", "AVOID_CONTEXT", "avoid", 3,
            "Downstack utan liquidation/stretch hade svag eller negativ edge.",
            "Downstack drift: fwd21 -1.05%, hit 41.0%",
        ))
    if post_liq_rsi50:
        labels.append(_label(
            "post_liq_rsi50", "POST-LIQ RSI50", "95_CONTEXT_EDGE", "clean", 4,
            "Tidigare liquidation följt av RSI>50 och positiv veckodelta.",
            "Post-liq RSI50: fwd21 +3.28%, hit 76.9%",
        ))
    if post_liq_rs_reclaim:
        labels.append(_label(
            "post_liq_rs_reclaim", "POST-LIQ RS RECLAIM", "95_CONTEXT_EDGE", "clean", 5,
            "Tidigare liquidation följt av relativ styrka mot region.",
            "Post-liq RS reclaim: fwd21 +3.23%, hit 77.2%",
        ))
    if parent_bear_liq:
        labels.append(_label(
            "parent_bear_liq", "PARENT BEAR LIQ EDGE", "95_CONTEXT_EDGE", "boost", 6,
            "Downstack liquidation sker när regionen också är bear.",
            "Parent bear liquidation: fwd21 +3.54%, hit 69.1%",
        ))
    if downstack_liq:
        labels.append(_label(
            "downstack_liq", "DOWNSTACK LIQ EDGE", "95_CONTEXT_EDGE", "boost", 7,
            "Downstack med liquidation och SMA252-stretch.",
            "Downstack liquidation: fwd21 +3.62%, hit 69.8%",
        ))
    if liquidation_rsi21_low:
        labels.append(_label(
            "liquidation_rsi21_low", "LIQ RSI21 LOW", "95_CONTEXT_EDGE", "boost", 8,
            "Liquidation-risk sammanfaller med RSI21 under 40.",
            "Liquidation + RSI21 low: fwd21 +5.10%, hit 78.2%",
        ))
    if pb126_trigger and region_126:
        labels.append(_label(
            "pb126_parent_126_context", "PB126 126+ CONTEXT", "ROBUST_CONTEXT_ONLY", "context", 8,
            "Parent-regionen är i 126+-regim. Bra kontext, inte bevisad signalboost.",
            "PB126 parent 126+: fwd21 +2.59%, hit 73.1%",
        ))
    if pb126_trigger and rs_strength:
        labels.append(_label(
            "pb126_rs_context", "PB126 RS CONTEXT", "ROBUST_CONTEXT_ONLY", "context", 9,
            "Instrumentet visar relativ styrka mot regionen.",
            "PB126 RS strength: fwd21 +3.07%, hit 78.3%",
        ))
    if parent_126_pb:
        labels.append(_label(
            "parent_126_pb", "PARENT 126+ PB", "PROMISING", "context", 10,
            "126+-pullback i en region som också är 126+.",
            "Parent 126+ PB: fwd21 +2.25%, hit 64.9%",
        ))
    if region_126_rsi21_low:
        labels.append(_label(
            "region126_rsi21_low", "REGION 126+ RSI21 LOW", "95_CONTEXT_EDGE", "clean", 10,
            "Parent-regionen är 126+ medan instrumentets RSI21 är stressat.",
            "Region 126 + RSI21 low: fwd21 +4.35%, hit 79.7%",
        ))
    if transition126_deep_mr:
        labels.append(_label(
            "transition126_deep_mr", "126+ DEEP MR", "95_CONTEXT_EDGE", "clean", 11,
            "126+-regim med full RSI-stack washout.",
            "126+ + deep MR: fwd21 +3.82%, hit 76.8%, MAE p10 -6.42%",
        ))
    if transition126_rsi21_low:
        labels.append(_label(
            "transition126_rsi21_low", "126+ RSI21 LOW", "95_CONTEXT_EDGE", "clean", 12,
            "SMA126>SMA252 och RSI21 under 40.",
            "126+ + RSI21 low: fwd21 +3.39%, hit 73.7%",
        ))
    if liq_flush and not cap_trigger:
        labels.append(_label(
            "liq_flush_context", "LIQ FLUSH WATCH", "PROMISING", "context", 11,
            "Liquidation flush finns, men CAP-villkoren är inte komplett gröna.",
            "Liquidation flush: fwd21 +2.87%, hit 63.5%",
        ))
    if liq_rs_weak:
        labels.append(_label(
            "liq_rs_weak", "LIQ RS WEAK", "PROMISING", "context", 12,
            "Liquidation med relativ svaghet fungerade historiskt, men är smutsigare.",
            "Liquidation RS weak: fwd21 +3.30%, hit 64.8%",
        ))
    if rsi21_low_5d:
        labels.append(_label(
            "rsi21_low_5d", "RSI21 LOW 5D", "95_CONTEXT_EDGE", "clean", 13,
            "RSI21 har varit under 40 i minst fem handelsdagar.",
            "RSI21<=40 5d: fwd21 +3.59%, hit 71.0%",
        ))
    elif rsi21_low:
        labels.append(_label(
            "rsi21_low", "RSI21 LOW", "95_CONTEXT_EDGE", "clean", 14,
            "RSI21 under 40 är momentum-stress, inte en hård entry-trigger.",
            "RSI21<=40: fwd21 +3.04%, hit 70.5%",
        ))
    if deep_mr:
        labels.append(_label(
            "deep_mr_dirty", "DEEP MR DIRTY", "95_CONTEXT_EDGE", "context", 15,
            "RSI9/21/63 är alla stressade; edge finns men pathen är smutsigare.",
            "Deep MR: fwd21 +2.66%, hit 71.7%, MAE p10 -13.4%",
        ))
    if rsi63_d42_washout:
        labels.append(_label(
            "rsi63_d42_washout", "RSI63 D42 WASHOUT", "95_CONTEXT_EDGE", "context", 16,
            "RSI63 42d-delta är extremt negativt i rolling RankINT.",
            "RSI63 d42 washout: fwd21 +2.16%, hit 64.3%, n=367",
        ))
    if rs_weak_rsi21_low:
        labels.append(_label(
            "rs_weak_rsi21_low", "RS WEAK RSI21 LOW", "95_CONTEXT_EDGE", "context", 17,
            "Relativ svaghet plus RSI21-stress har historiskt varit mean-reversion-kontext.",
            "RS weak + RSI21 low: fwd21 +2.93%, hit 68.5%",
        ))

    labels.sort(key=lambda x: x["priority"])
    if not labels:
        context = _empty_regime_context()
    else:
        context = {
            "status": "OK",
            "primary": labels[0],
            "labels": labels,
            "flags": {
                "downstack": bool(downstack),
                "transition_126": bool(transition_126),
                "liquidation_risk": bool(liquidation_risk),
                "flush5": bool(flush5),
                "flush10": bool(flush10),
                "downstack_liq": bool(downstack_liq),
                "liq_flush": bool(liq_flush),
                "downstack_drift": bool(downstack_drift),
                "post_liq_rsi50": bool(post_liq_rsi50),
                "post_liq_rs_reclaim": bool(post_liq_rs_reclaim),
                "parent_bear_liq": bool(parent_bear_liq),
                "parent_126_pb": bool(parent_126_pb),
                "rs_strength": bool(rs_strength),
                "rs_weak": bool(rs_weak),
                "rsi21_low": bool(rsi21_low),
                "rsi21_low_5d": bool(rsi21_low_5d),
                "rsi21_low_run": int(rsi21_low_run),
                "deep_mr": bool(deep_mr),
                "rsi63_d42_washout": bool(rsi63_d42_washout),
                "rsi63_d63_context_suppressed": bool(rsi63_d63_context),
                "liquidation_rsi21_low": bool(liquidation_rsi21_low),
                "transition126_rsi21_low": bool(transition126_rsi21_low),
                "transition126_deep_mr": bool(transition126_deep_mr),
                "region_126_rsi21_low": bool(region_126_rsi21_low),
                "rs_weak_rsi21_low": bool(rs_weak_rsi21_low),
                "rs_strength_rsi21_low": bool(rs_strength_rsi21_low),
            },
            "rsi": {
                "rsi9": float(rsi9) if np.isfinite(rsi9) else None,
                "rsi21": float(rsi21) if np.isfinite(rsi21) else None,
                "rsi63": float(rsi63) if np.isfinite(rsi63) else None,
                "rsi21_low_run": int(rsi21_low_run),
                "rsi63_delta_21d": float(rsi63_delta_21d) if np.isfinite(rsi63_delta_21d) else None,
                "rsi63_delta_42d": float(rsi63_delta_42d) if np.isfinite(rsi63_delta_42d) else None,
                "ri_rsi63_delta_42d": float(ri_rsi63_delta_42d) if np.isfinite(ri_rsi63_delta_42d) else None,
            },
            "region": {
                "name": region,
                "regime": region_label,
                "breadth": region_overlay.get("breadth"),
            },
            "relative_strength": {
                "rs_mom21": float(rs_mom21) if isinstance(rs_mom21, (int, float)) and np.isfinite(rs_mom21) else None,
                "rs_above21": bool(rs_above21),
                "rs_reclaim21": bool(rs_reclaim21),
            },
        }
    return context


def apply_regime_research_labels(
    results: list[dict],
    feature_frames: dict[str, pd.DataFrame],
    inst_thresholds: dict[str, dict],
) -> None:
    """Attach validated regime research labels to each OK scan result."""
    region_overlays, ticker_rs = _build_region_overlays(results, feature_frames)
    for r in results:
        if r.get("error") or r.get("skipped"):
            continue
        ticker = r.get("ticker")
        signals = r.get("signals")
        df = feature_frames.get(ticker or "")
        if not ticker or signals is None or df is None:
            continue
        signals["regime_context"] = _ticker_regime_context(
            ticker=ticker,
            region=r.get("region", "?"),
            df=df,
            signals=signals,
            inst_thresholds=inst_thresholds.get(ticker),
            region_overlay=region_overlays.get(r.get("region", "?")),
            rs_overlay=ticker_rs.get(ticker),
        )

# ---------------------------------------------------------------------------
# Formateringshjälpare
# ---------------------------------------------------------------------------

def fmt(v: float, d: int = 3) -> str:
    return "N/A" if np.isnan(v) else f"{v:.{d}f}"


def fmt_pct(v: float) -> str:
    return "N/A" if np.isnan(v) else f"{v * 100:.2f}%"


def fmt_pct_optional(v: float | None) -> str:
    return "N/A" if v is None or np.isnan(v) else f"{v * 100:.2f}%"

# ---------------------------------------------------------------------------
# Output 1: full dagsrapport (audit, alla tickers)
# ---------------------------------------------------------------------------

def write_full_scan(results: list[dict], scan_date: str, path: Path) -> None:
    cap_list   = [r for r in results if r.get("signals", {}).get("cap_trigger")]
    pb_list    = [r for r in results if r.get("signals", {}).get("pb_trigger")]
    pb126_list = [r for r in results if r.get("signals", {}).get("pb126_trigger")]
    skipped    = [r for r in results if r.get("skipped")]
    errors     = [r for r in results if r.get("error")]
    ok_list    = [r for r in results if not r.get("skipped") and not r.get("error")]

    per_inst_count = sum(1 for r in results if r.get("signals", {}).get("threshold_mode") == "per-instrument")
    proxy_count    = sum(1 for r in results if r.get("signals", {}).get("threshold_mode") == "proxy-volume")
    univ_count     = sum(1 for r in results if r.get("signals", {}).get("threshold_mode") == "universal-fallback")

    lines = [
        f"# Daily ETF Scan — {scan_date}",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "#",
        f"# Capitulation:  DirRVOL≤-p85(per-inst) + RankINT(RSI9)≤{CAP_THRESHOLD:.3f} + RankINT(dist_sma252)≤{CAP_THRESHOLD:.3f}",
        f"# Pullback:      Bull-stack + DirRVOL≥p70(per-inst) + RankINT(dist_sma21)≤{PB_THRESHOLD:.3f} + RankINT(RSI9)≤{PB_THRESHOLD:.3f} [whitelist: EWG,2800.HK,^HSI]",
        f"# PB SMA126:     SMA126>SMA252 + DirRVOL≥p70(per-inst) + RankINT(dist_sma126)≤{PB_THRESHOLD:.3f} + RankINT(RSI9)≤{PB_THRESHOLD:.3f} [universellt]",
        f"# DirRVOL mode:  per-instrument={per_inst_count} | proxy-volume={proxy_count} | universal-fallback={univ_count}",
        "# RankINT:       window=1260d, min_periods=252",
        "# DirRVOL63:     (Volume/SMA63(Volume)) × CLV, CLV=0 om High==Low",
        "#",
        "",
        "## Sammanfattning",
        "",
        f"- Scannade:             {len(results)}",
        f"- Capitulation-signaler: {len(cap_list)}",
        f"- Pullback-signaler:    {len(pb_list)}",
        f"- PB SMA126-signaler:  {len(pb126_list)}",
        f"- Skippade (för kort):  {len(skipped)}",
        f"- Fel (fetch-failure):  {len(errors)}",
        "",
        "## Alla tickers",
        "",
        "| Ticker | Namn | Region | Bars | Status | BullStack | ri_rvol | ri_rsi | ri_dist252 | ri_dist21 | ri_dist126 | Δ1d | Δ5d | Fresh | 95% Regime | Analog fwd21 | Path | CAP n/gap | PB n/gap | PB126 n/gap |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        ticker = r["ticker"]
        name   = r["name"]
        region = r["region"]
        if r.get("error"):
            lines.append(f"| {ticker} | {name} | {region} | - | FETCH_ERROR | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |")
        elif r.get("skipped"):
            reason = str(r.get("reason", "SKIPPED")).replace("|", "/")
            lines.append(
                f"| {ticker} | {name} | {region} | {r.get('bars', '-')} "
                f"| SKIPPED: {reason} | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |"
            )
        else:
            s = r["signals"]
            a = r.get("analog", {})
            f = s.get("freshness", {})
            ctx = s.get("regime_context", {}) or {}
            primary_ctx = ctx.get("primary", {}) or {}
            bull    = "Y" if s["bull_stack"] else "N"
            cap_cell   = "**CAP**" if s["cap_trigger"] else f"{s.get('cap_count', 0)}/3 ({fmt(s.get('cap_gap_total', float('nan')), 2)})"
            pb_cell    = "**PB**"  if s["pb_trigger"]  else f"{s.get('pb_count', 0)}/3 ({fmt(s.get('pb_gap_total', float('nan')), 2)})"
            pb126_cell = "**PB126**" if s["pb126_trigger"] else f"{s.get('pb126_count', 0)}/3 ({fmt(s.get('pb126_gap_total', float('nan')), 2)})"
            fresh_cell = (
                f"{f.get('best_type')} D+{f.get('best_age')} {f.get('best_state')}"
                if f.get("status") == "OK" else f.get("status", "NO_RECENT")
            )
            analog_cell = (
                f"{a.get('verdict')} {fmt_pct_optional(a.get('fwd21_mean'))} / "
                f"hit {fmt_pct_optional(a.get('fwd21_hit_rate'))} / n={a.get('n')}"
                if a.get("status") == "OK" else a.get("status", "N/A")
            )
            path_cell = (
                f"{a.get('path_risk')} / MAE p10 {fmt_pct_optional(a.get('mae21_p10'))} / "
                f"shake5 {fmt_pct_optional(a.get('shakeout_5pct_rate'))}"
                if a.get("status") == "OK" else "-"
            )
            regime_cell = primary_ctx.get("label", "NO 95 REGIME")
            lines.append(
                f"| {ticker} | {name} | {region} | {r['bars']} | OK "
                f"| {bull} | {fmt(s['ri_rvol'])} | {fmt(s['ri_rsi'])} "
                f"| {fmt(s['ri_dist252'])} | {fmt(s['ri_dist21'])} | {fmt(s['ri_dist126'])} "
                f"| {fmt(s.get('rsi9_delta_1d', float('nan')), 2)} | {fmt(s.get('rsi9_delta_5d', float('nan')), 2)} "
                f"| {fresh_cell} | {regime_cell} | {analog_cell} | {path_cell} | {cap_cell} | {pb_cell} | {pb126_cell} |"
            )

    # Närmast-grön-sektioner (top 5 per signal-typ, sorterat på total z-gap stigande)
    # Confirmation-info: Δ5d > 0 krävs för entry per personal-trading.md:125 (visas i kolumn)
    def _proximity_block(label: str, key_count: str, key_gap: str, threshold_count: int) -> list[str]:
        candidates = [r for r in ok_list
                      if not r["signals"].get(f"{label.lower().replace(' ', '').replace('sma','sma')}_trigger")
                      and r["signals"].get(key_count, 0) >= 1
                      and not np.isnan(r["signals"].get(key_gap, float("nan")))]
        candidates.sort(key=lambda r: r["signals"][key_gap])
        out = [f"### Närmast {label}-signal (top 5, sort: minst z-gap)", ""]
        if not candidates:
            out += ["_Inga kandidater med ≥1 kriterium uppfyllt._", ""]
            return out
        out += ["| Ticker | Region | Krit | z-gap | Δ1d | Δ5d | Confirmation (Δ5d>0) |", "|---|---|---|---|---|---|---|"]
        for r in candidates[:5]:
            s = r["signals"]
            d5 = s.get("rsi9_delta_5d", float("nan"))
            conf = "✓" if (not np.isnan(d5) and d5 > 0) else ("✗ dead-cat" if not np.isnan(d5) else "—")
            out.append(
                f"| {r['ticker']} | {r['region']} | {s[key_count]}/{threshold_count} | "
                f"{fmt(s[key_gap], 3)} | {fmt(s.get('rsi9_delta_1d', float('nan')), 2)} | "
                f"{fmt(d5, 2)} | {conf} |"
            )
        out.append("")
        return out

    lines += ["", "## Närmast-grön-rankning", ""]
    lines += _proximity_block("PB126", "pb126_count", "pb126_gap_total", 3)
    lines += _proximity_block("PB",    "pb_count",    "pb_gap_total",    3)
    lines += _proximity_block("CAP",   "cap_count",   "cap_gap_total",   3)

    # Detaljsektion per ticker
    lines += ["", "## Detaljdata", ""]
    for r in ok_list:
        s = r["signals"]
        a = r.get("analog", {})
        f = s.get("freshness", {})
        q = r.get("quote", {})
        lines += [
            f"### {r['ticker']} — {r['name']}",
            f"- Region: {r['region']}  |  Bars: {r['bars']}",
            f"- Close: {fmt(s['close'])}  |  "
            f"SMA21: {fmt(s['sma21'])}  |  SMA52: {fmt(s['sma52'])}  |  "
            f"SMA126: {fmt(s['sma126'])}  |  SMA252: {fmt(s['sma252'])}",
            f"- RSI9: {fmt(s['rsi9'], 1)}  |  Δ1d: {fmt(s['rsi9_delta_1d'], 2)}  |  Δ5d: {fmt(s['rsi9_delta_5d'], 2)}  |  "
            f"DirRVOL63: {fmt(s['dir_rvol63'])}  |  "
            f"dist_sma21: {fmt_pct(s['dist_sma21'])}  |  dist_sma252: {fmt_pct(s['dist_sma252'])}",
            f"- Volume+: DirLogVolZ63: {fmt(s.get('dir_logvolz63', float('nan')))}  |  "
            f"DirRVOL Δ1d: {fmt(s.get('dir_rvol_delta_1d', float('nan')))}  |  "
            f"Δ5d: {fmt(s.get('dir_rvol_delta_5d', float('nan')))}  |  "
            f"ri_logvolz={fmt(s.get('ri_logvolz', float('nan')))}  |  "
            f"ri_rvol_d5={fmt(s.get('ri_rvol_delta_5d', float('nan')))}",
            f"- RSI21: {fmt(s.get('rsi21', float('nan')), 1)}  |  Δ5d: {fmt(s.get('rsi21_delta_5d', float('nan')), 2)}  |  Δ21d: {fmt(s.get('rsi21_delta_21d', float('nan')), 2)}  |  "
            f"RSI63: {fmt(s.get('rsi63', float('nan')), 1)}  |  Δ21d: {fmt(s.get('rsi63_delta_21d', float('nan')), 2)}  |  Δ42d: {fmt(s.get('rsi63_delta_42d', float('nan')), 2)}",
            f"- RankINT:  ri_rvol={fmt(s['ri_rvol'])}  ri_rsi={fmt(s['ri_rsi'])}  "
            f"ri_rsi21={fmt(s.get('ri_rsi21', float('nan')))}  ri_rsi63={fmt(s.get('ri_rsi63', float('nan')))}  "
            f"ri_rsi63_d42={fmt(s.get('ri_rsi63_delta_42d', float('nan')))}  "
            f"ri_dist252={fmt(s['ri_dist252'])}  ri_dist21={fmt(s['ri_dist21'])}  ri_dist126={fmt(s['ri_dist126'])}",
            f"- dist_sma126: {fmt_pct(s['dist_sma126'])}",
            f"- Bull-stack: {'JA' if s['bull_stack'] else 'NEJ'}  |  "
            f"SMA126>SMA252: {'JA' if s['sma126_above_sma252'] else 'NEJ'}  |  "
            f"Capitulation: {'**TRIGGER**' if s['cap_trigger'] else 'nej'}  |  "
            f"CAP LogVolZ: {'JA' if s.get('cap_logvolz_trigger') else 'nej'}  |  "
            f"Pullback: {'**TRIGGER**' if s['pb_trigger'] else 'nej'}  |  "
            f"PB SMA126: {'**TRIGGER**' if s['pb126_trigger'] else 'nej'}  |  "
            f"PB126 LogVolZ: {'JA' if s.get('pb126_logvolz_trigger') else 'nej'}",
            f"- ATR20: {fmt(s['atr20'])}  |  ATR20%: {fmt_pct(s['atr20_pct'])}  |  "
            f"Threshold: {s.get('threshold_mode', 'N/A')}  |  "
            f"VolumeTrust: {s.get('volume_trust', 'N/A')}  |  "
            f"Currency: {r.get('currency', 'N/A')}  |  "
            f"LastClose: {r.get('last_close_date', 'N/A')}",
            f"- Volume policy: {s.get('volume_reason') or s.get('volume_source_ticker') or 'OK'}",
            f"- Data: {q.get('price_source', 'N/A')}  |  Delay: {q.get('latency_label', 'N/A')}  |  "
            f"QuoteTime: {q.get('quote_time') or 'N/A'}  |  Overlay: {q.get('intraday_status', 'N/A')}",
            (
                f"- Signal freshness: {f.get('best_type')} D+{f.get('best_age')} ({f.get('best_state')})  |  "
                f"score={f.get('score')}  |  lookback={f.get('lookback')}d"
                if f.get("status") == "OK" else f"- Signal freshness: {f.get('status', 'NO_RECENT')}"
            ),
            (
                f"- Analog-context: {a.get('verdict')} ({a.get('confidence')})  |  "
                f"n={a.get('n')} / pool={a.get('candidate_pool')}  |  "
                f"fwd5={fmt_pct_optional(a.get('fwd5_mean'))}  "
                f"fwd21={fmt_pct_optional(a.get('fwd21_mean'))} hit={fmt_pct_optional(a.get('fwd21_hit_rate'))}  "
                f"fwd63={fmt_pct_optional(a.get('fwd63_mean'))}  |  "
                f"MAE21 p10={fmt_pct_optional(a.get('mae21_p10'))}  |  "
                f"MFE21 med={fmt_pct_optional(a.get('mfe21_median'))}  |  "
                f"Path={a.get('path_risk')} shake5={fmt_pct_optional(a.get('shakeout_5pct_rate'))}  |  "
                f"best-delay≈{fmt(a.get('best_entry_delay_median') if a.get('best_entry_delay_median') is not None else float('nan'), 1)}d"
                if a.get("status") == "OK" else f"- Analog-context: {a.get('status', 'N/A')}"
            ),
            "",
        ]

    # Skippade
    if skipped:
        lines += ["## Skippade tickers", ""]
        for r in skipped:
            lines.append(f"- {r['ticker']} ({r['name']}): {r['reason']}")
        lines.append("")

    # Fel
    if errors:
        lines += ["## Fetch-fel", ""]
        for r in errors:
            lines.append(f"- {r['ticker']} ({r['name']}): fetch_failed")
        lines.append("")

    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Position sizing helpers
# ---------------------------------------------------------------------------

_CONVICTION_AMOUNT = 10_000.0
_VOL_TARGET_AMOUNT = 5_000.0


def _compute_conviction_sizing(
    ticker: str,
    currency: str,
    close: float,
    atr20_pct: float,
    portfolio: float,
) -> dict:
    """Returnerar operativ conviction-sizing per §5e.2. Kräver _SIZING_AVAILABLE=True."""
    try:
        fx = get_fx_rate_to_sek(currency)
        entry_sek = close * fx
        res = size_position_conviction(
            entry=entry_sek,
            portfolio=portfolio,
            atr20_pct=atr20_pct,
            conviction_amount=_CONVICTION_AMOUNT,
            vol_target_amount=_VOL_TARGET_AMOUNT,
        )
        position_value = res.units * entry_sek
        daily_var_pct = None if np.isnan(res.daily_var_pct) else res.daily_var_pct
        return {
            "units":     res.units,
            "position_value_sek": position_value,
            "smartroskel_sek":  res.risk_value,
            "smartroskel_pct":  res.risk_pct,
            "daily_var_pct": daily_var_pct,
            "constraint": res.constraint,
            "fx":        fx,
            "error":     None,
        }
    except Exception as e:
        return {
            "units": None,
            "position_value_sek": None,
            "smartroskel_sek": None,
            "smartroskel_pct": None,
            "daily_var_pct": None,
            "constraint": None,
            "fx": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Output 2: watchlist — bara aktiva triggers
# ---------------------------------------------------------------------------

def write_watchlist(results: list[dict], scan_date: str, path: Path, portfolio: float = 60_000) -> None:
    cap_list   = [r for r in results if r.get("signals", {}).get("cap_trigger")]
    pb_list    = [r for r in results if r.get("signals", {}).get("pb_trigger")]
    pb126_list = [r for r in results if r.get("signals", {}).get("pb126_trigger")]

    do_sizing = _SIZING_AVAILABLE and portfolio > 0

    lines = [
        f"# Watchlist — {scan_date}",
        f"Genererad: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Portfolio: {portfolio:,.0f} SEK  |  Mode: conviction/no hard stop (§5e.2)  |  "
        f"Sizing: {'aktiv' if do_sizing else 'inaktiverad (portfolio=0 eller position_size ej tillgänglig)'}",
        "",
    ]

    if cap_list:
        lines += [f"## Capitulation-signaler ({len(cap_list)} st)", "", "> Alla 3 RankINT z-scores ≤ -1.036 (-Z_P85)", ""]
        for r in cap_list:
            s = r["signals"]
            close    = s["close"]
            atr20    = s.get("atr20", float("nan"))
            atr20_pct = s.get("atr20_pct", float("nan"))
            entry_zone = close
            currency   = r.get("currency", "USD")

            lines += [
                f"### {r['ticker']} — {r['name']} ({r['region']})",
                f"| Fält | Värde |",
                f"|---|---|",
                f"| Signal | Capitulation |",
                f"| Entry zone | {fmt(entry_zone)} {currency} |",
                f"| Operativ stop | Ingen hård stop (§5e.2) |",
                f"| ATR20 | {fmt(atr20)} ({fmt_pct(atr20_pct)}) |",
            ]

            if do_sizing and not np.isnan(close):
                sz = _compute_conviction_sizing(r["ticker"], currency, close, atr20_pct, portfolio)
                if sz["error"]:
                    lines.append(f"| Sizing | ERROR: {sz['error']} |")
                else:
                    lines += [
                        f"| Position (enheter) | {sz['units']} |",
                        f"| Position SEK | {sz['position_value_sek']:,.0f} |",
                        f"| Smärttröskel SEK | {sz['smartroskel_sek']:,.0f} |",
                        f"| Smärttröskel % | {sz['smartroskel_pct']*100:.2f}% |",
                        f"| Daglig VaR % | {fmt_pct_optional(sz['daily_var_pct'])} |",
                        f"| Begränsare | {sz['constraint']} |",
                        f"| FX ({currency}→SEK) | {sz['fx']:.4f} |",
                    ]
            elif not do_sizing:
                lines.append(
                    f"| Sizing | `.venv/bin/python scripts/position_size.py "
                    f"--ticker {r['ticker']} --entry {fmt(close)} "
                    f"--portfolio {portfolio:.0f} --no-stop` |"
                )

            lines += [
                f"",
                f"**Signal-villkor:**",
                f"| Villkor | Z-score | Tröskel |",
                f"|---|---|---|",
                f"| RankINT(DirRVOL63) | {fmt(s['ri_rvol'])} | ≤ {CAP_THRESHOLD:.3f} |",
                f"| RankINT(RSI9) | {fmt(s['ri_rsi'])} | ≤ {CAP_THRESHOLD:.3f} |",
                f"| RankINT(dist_sma252) | {fmt(s['ri_dist252'])} | ≤ {CAP_THRESHOLD:.3f} |",
                f"",
                f"- Close: {fmt(s['close'])}  |  SMA252: {fmt(s['sma252'])}  "
                f"|  dist_sma252: {fmt_pct(s['dist_sma252'])}  |  RSI9: {fmt(s['rsi9'], 1)}",
                f"- DirRVOL63 (raw): {fmt(s['dir_rvol63'])}",
                "",
            ]
    else:
        lines += ["## Capitulation-signaler", "", "_(inga idag)_", ""]

    if pb_list:
        lines += [f"## Pullback-signaler ({len(pb_list)} st)", "", "> Bull-stack + 3 villkor på Z_P70", ""]
        for r in pb_list:
            s = r["signals"]
            close     = s["close"]
            atr20     = s.get("atr20", float("nan"))
            atr20_pct = s.get("atr20_pct", float("nan"))
            entry_zone = close
            currency   = r.get("currency", "USD")

            lines += [
                f"### {r['ticker']} — {r['name']} ({r['region']})",
                f"| Fält | Värde |",
                f"|---|---|",
                f"| Signal | Pullback |",
                f"| Entry zone | {fmt(entry_zone)} {currency} |",
                f"| Operativ stop | Ingen hård stop (§5e.2) |",
                f"| ATR20 | {fmt(atr20)} ({fmt_pct(atr20_pct)}) |",
            ]

            if do_sizing and not np.isnan(close):
                sz = _compute_conviction_sizing(r["ticker"], currency, close, atr20_pct, portfolio)
                if sz["error"]:
                    lines.append(f"| Sizing | ERROR: {sz['error']} |")
                else:
                    lines += [
                        f"| Position (enheter) | {sz['units']} |",
                        f"| Position SEK | {sz['position_value_sek']:,.0f} |",
                        f"| Smärttröskel SEK | {sz['smartroskel_sek']:,.0f} |",
                        f"| Smärttröskel % | {sz['smartroskel_pct']*100:.2f}% |",
                        f"| Daglig VaR % | {fmt_pct_optional(sz['daily_var_pct'])} |",
                        f"| Begränsare | {sz['constraint']} |",
                        f"| FX ({currency}→SEK) | {sz['fx']:.4f} |",
                    ]
            elif not do_sizing:
                lines.append(
                    f"| Sizing | `.venv/bin/python scripts/position_size.py "
                    f"--ticker {r['ticker']} --entry {fmt(close)} "
                    f"--portfolio {portfolio:.0f} --no-stop` |"
                )

            lines += [
                f"",
                f"**Signal-villkor:**",
                f"| Villkor | Z-score | Tröskel |",
                f"|---|---|---|",
                f"| RankINT(dist_sma21) | {fmt(s['ri_dist21'])} | ≤ {PB_THRESHOLD:.3f} |",
                f"| RankINT(RSI9) | {fmt(s['ri_rsi'])} | ≤ {PB_THRESHOLD:.3f} |",
                f"| RankINT(DirRVOL63) | {fmt(s['ri_rvol'])} | ≥ +{PB_VOL_THRESH:.3f} |",
                f"",
                f"- Close: {fmt(s['close'])}  |  SMA21: {fmt(s['sma21'])}  "
                f"|  dist_sma21: {fmt_pct(s['dist_sma21'])}  |  RSI9: {fmt(s['rsi9'], 1)}",
                f"- Bull-stack: JA (SMA21>SMA52>SMA126>SMA252)",
                "",
            ]
    else:
        lines += ["## Pullback-signaler", "", "_(inga idag)_", ""]

    if pb126_list:
        lines += [f"## PB SMA126-signaler ({len(pb126_list)} st)", "", "> SMA126>SMA252 + 3 villkor på Z_P70 (§ 5b.2 VALIDERAD 5/5)", ""]
        for r in pb126_list:
            s = r["signals"]
            close     = s["close"]
            atr20     = s.get("atr20", float("nan"))
            atr20_pct = s.get("atr20_pct", float("nan"))
            entry_zone = close
            currency   = r.get("currency", "USD")

            lines += [
                f"### {r['ticker']} — {r['name']} ({r['region']})",
                f"| Fält | Värde |",
                f"|---|---|",
                f"| Signal | PB SMA126 |",
                f"| Entry zone | {fmt(entry_zone)} {currency} |",
                f"| Operativ stop | Ingen hård stop (§5e.2) |",
                f"| ATR20 | {fmt(atr20)} ({fmt_pct(atr20_pct)}) |",
            ]

            if do_sizing and not np.isnan(close):
                sz = _compute_conviction_sizing(r["ticker"], currency, close, atr20_pct, portfolio)
                if sz["error"]:
                    lines.append(f"| Sizing | ERROR: {sz['error']} |")
                else:
                    lines += [
                        f"| Position (enheter) | {sz['units']} |",
                        f"| Position SEK | {sz['position_value_sek']:,.0f} |",
                        f"| Smärttröskel SEK | {sz['smartroskel_sek']:,.0f} |",
                        f"| Smärttröskel % | {sz['smartroskel_pct']*100:.2f}% |",
                        f"| Daglig VaR % | {fmt_pct_optional(sz['daily_var_pct'])} |",
                        f"| Begränsare | {sz['constraint']} |",
                        f"| FX ({currency}→SEK) | {sz['fx']:.4f} |",
                    ]
            elif not do_sizing:
                lines.append(
                    f"| Sizing | `.venv/bin/python scripts/position_size.py "
                    f"--ticker {r['ticker']} --entry {fmt(close)} "
                    f"--portfolio {portfolio:.0f} --no-stop` |"
                )

            lines += [
                f"",
                f"**Signal-villkor:**",
                f"| Villkor | Z-score | Tröskel |",
                f"|---|---|---|",
                f"| RankINT(dist_sma126) | {fmt(s['ri_dist126'])} | ≤ {PB_THRESHOLD:.3f} |",
                f"| RankINT(RSI9) | {fmt(s['ri_rsi'])} | ≤ {PB_THRESHOLD:.3f} |",
                f"| RankINT(DirRVOL63) | {fmt(s['ri_rvol'])} | ≥ +{PB_VOL_THRESH:.3f} |",
                f"",
                f"- Close: {fmt(s['close'])}  |  SMA126: {fmt(s['sma126'])}  "
                f"|  dist_sma126: {fmt_pct(s['dist_sma126'])}  |  RSI9: {fmt(s['rsi9'], 1)}",
                f"- SMA126>SMA252: JA",
                "",
            ]
    else:
        lines += ["## PB SMA126-signaler", "", "_(inga idag)_", ""]

    lines += [
        "---",
        f"*Totalt: {len(cap_list)} capitulation + {len(pb_list)} pullback + {len(pb126_list)} pb-sma126*",
    ]

    path.write_text("\n".join(lines) + "\n")


def _journal_value(v, digits: int | None = None):
    """JSON-safe scalar for signal memory snapshots."""
    if v is None:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, (int, float)):
        try:
            if np.isnan(v) or np.isinf(v):
                return None
        except TypeError:
            return None
        return round(float(v), digits) if digits is not None else float(v)
    return v


def _active_signal_snapshots(results: list, scan_date: str) -> list[dict]:
    """Return all currently active scanner signals as immutable run snapshots."""
    now = datetime.now().isoformat(timespec="seconds")
    snapshots: list[dict] = []
    for r in results:
        if r.get("error") or r.get("skipped"):
            continue
        s = r.get("signals") or {}
        q = r.get("quote") or {}
        for flag_key, signal_key, sig_type, label in SIGNAL_DEFS:
            if not s.get(flag_key):
                continue
            snapshots.append({
                "key": f"{scan_date}|{r.get('ticker')}|{signal_key}",
                "date": scan_date,
                "ticker": r.get("ticker"),
                "name": r.get("name"),
                "region": r.get("region"),
                "signal_key": signal_key,
                "type": sig_type,
                "label": label,
                "entry": _journal_value(s.get("close"), 4),
                "currency": r.get("currency", "USD"),
                "source": "intraday_overlay" if q.get("intraday_overlay") else "daily_close",
                "quote_time": q.get("quote_time"),
                "quote_date": q.get("quote_date"),
                "intraday_status": q.get("intraday_status"),
                "execution": r.get("execution") or {},
                "seen_at": now,
                "snapshot": {
                    "ri_rvol": _journal_value(s.get("ri_rvol"), 3),
                    "ri_rsi": _journal_value(s.get("ri_rsi"), 3),
                    "ri_dist252": _journal_value(s.get("ri_dist252"), 3),
                    "ri_dist21": _journal_value(s.get("ri_dist21"), 3),
                    "ri_dist126": _journal_value(s.get("ri_dist126"), 3),
                    "dirvol63": _journal_value(s.get("dir_rvol63"), 3),
                    "rsi9": _journal_value(s.get("rsi9"), 2),
                    "rsi9_delta_5d": _journal_value(s.get("rsi9_delta_5d"), 2),
                    "dist_sma126_pct": _journal_value(
                        s.get("dist_sma126") * 100 if s.get("dist_sma126") is not None else None,
                        2,
                    ),
                    "pb126_gap_total": _journal_value(s.get("pb126_gap_total"), 3),
                    "cap_gap_total": _journal_value(s.get("cap_gap_total"), 3),
                    "pb_gap_total": _journal_value(s.get("pb_gap_total"), 3),
                    "threshold_mode": s.get("threshold_mode"),
                    "open_gap_pct": _journal_value(s.get("open_gap_pct"), 2),
                    "gap_risk": s.get("gap_risk"),
                },
            })
    return snapshots


def _frame_pos_for_date(df: pd.DataFrame, date_str: str) -> int | None:
    target = pd.Timestamp(date_str).date()
    matches = [idx for idx, value in enumerate(df.index) if pd.Timestamp(value).date() == target]
    return matches[-1] if matches else None


def _close_state_for_journal_item(
    item: dict,
    feature_frames: dict[str, pd.DataFrame] | None,
    inst_thresholds_by_ticker: dict[str, dict] | None,
) -> str | None:
    if feature_frames is None:
        return None
    ticker = item.get("ticker")
    signal_key = item.get("signal_key")
    signal_date = item.get("date")
    if not ticker or not signal_key or not signal_date:
        return None
    df = feature_frames.get(ticker)
    if df is None or df.empty:
        return "CLOSE_PENDING_NO_FRAME"
    pos = _frame_pos_for_date(df, str(signal_date))
    if pos is None:
        return "CLOSE_PENDING_NO_BAR"
    # Conservative: only classify after a later bar exists, so the signal-date
    # daily candle should be finalized rather than still intraday.
    if pd.Timestamp(df.index[-1]).date() <= pd.Timestamp(signal_date).date():
        return None
    flags = _signal_flags_at(
        df,
        pos,
        inst_thresholds=(inst_thresholds_by_ticker or {}).get(ticker),
        ticker=ticker,
    )
    return "CLOSE_CONFIRMED" if flags.get(signal_key) else "CLOSE_FAILED"


def update_signal_journal(
    results: list,
    scan_date: str,
    path: Path,
    *,
    feature_frames: dict[str, pd.DataFrame] | None = None,
    inst_thresholds_by_ticker: dict[str, dict] | None = None,
) -> dict:
    """Persist intraday signal lifecycle so fired signals do not disappear."""
    now = datetime.now().isoformat(timespec="seconds")
    existing: dict = {"version": 1, "signals": []}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {"version": 1, "signals": []}

    entries = {
        str(item.get("key")): dict(item)
        for item in existing.get("signals", [])
        if item.get("key")
    }

    active = _active_signal_snapshots(results, scan_date)
    active_by_key = {item["key"]: item for item in active}
    result_by_ticker = {
        r.get("ticker"): r for r in results
        if r.get("ticker")
    }
    new_this_run: list[dict] = []
    faded_this_run: list[dict] = []
    closed_this_run: list[dict] = []

    for key, snap in active_by_key.items():
        current = entries.get(key)
        if current is None:
            current = {
                "key": key,
                "date": snap["date"],
                "ticker": snap["ticker"],
                "name": snap["name"],
                "region": snap["region"],
                "signal_key": snap["signal_key"],
                "type": snap["type"],
                "label": snap["label"],
                "currency": snap["currency"],
                "execution": snap.get("execution") or {},
                "first_seen_at": snap["seen_at"],
                "first_entry": snap["entry"],
                "seen_count": 0,
            }
            entries[key] = current
            new_this_run.append(current)

        current.update({
            "last_seen_at": snap["seen_at"],
            "last_checked_at": now,
            "last_entry": snap["entry"],
            "active": True,
            "status": "ACTIVE_NOW",
            "source": snap["source"],
            "quote_time": snap["quote_time"],
            "quote_date": snap["quote_date"],
            "intraday_status": snap["intraday_status"],
            "execution": snap.get("execution") or current.get("execution") or {},
            "snapshot": snap["snapshot"],
        })
        current["seen_count"] = int(current.get("seen_count", 0) or 0) + 1

    for key, current in entries.items():
        if not current.get("execution") and result_by_ticker.get(current.get("ticker")):
            current["execution"] = result_by_ticker[current.get("ticker")].get("execution") or {}
        if key in active_by_key:
            continue
        current["last_checked_at"] = now
        if current.get("date") == scan_date:
            was_active = current.get("active") is True or current.get("status") == "ACTIVE_NOW"
            current["active"] = False
            if current.get("status") not in {"FADED_INTRADAY", "CLOSE_FAILED"}:
                current["status"] = "FADED_INTRADAY"
            if was_active:
                faded_this_run.append(current)
            continue
        if str(current.get("date", "")) < scan_date and current.get("status") not in {"CLOSE_CONFIRMED", "CLOSE_FAILED"}:
            close_status = _close_state_for_journal_item(current, feature_frames, inst_thresholds_by_ticker)
            if close_status in {"CLOSE_CONFIRMED", "CLOSE_FAILED"}:
                previous_status = current.get("status")
                current["status"] = close_status
                current["active"] = False
                current["close_checked_at"] = now
                current["close_check_scan_date"] = scan_date
                if previous_status != close_status:
                    closed_this_run.append(current)
            elif close_status is not None:
                current["status"] = close_status
                current["active"] = False

    cutoff = date.fromisoformat(scan_date) - timedelta(days=SIGNAL_JOURNAL_LOOKBACK_DAYS)
    kept = []
    for item in entries.values():
        try:
            item_date = date.fromisoformat(str(item.get("date")))
        except ValueError:
            item_date = date.today()
        if item_date >= cutoff:
            kept.append(item)
    kept.sort(key=lambda x: (str(x.get("date")), str(x.get("first_seen_at")), str(x.get("ticker"))))

    today = [item for item in kept if item.get("date") == scan_date]
    today.sort(key=lambda x: (not bool(x.get("active")), str(x.get("first_seen_at")), str(x.get("ticker"))))

    payload = {
        "version": 1,
        "updated_at": now,
        "scan_date": scan_date,
        "signals": kept,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "updated_at": now,
        "today": today,
        "new_this_run": new_this_run,
        "faded_this_run": faded_this_run,
        "closed_this_run": closed_this_run,
        "today_count": len(today),
        "active_today_count": sum(1 for item in today if item.get("active")),
        "faded_today_count": sum(1 for item in today if not item.get("active")),
        "close_confirmed_count": sum(1 for item in kept if item.get("status") == "CLOSE_CONFIRMED"),
        "close_failed_count": sum(1 for item in kept if item.get("status") == "CLOSE_FAILED"),
    }


def write_signals_json(
    results: list,
    scan_date: str,
    path: Path,
    portfolio: float = 60_000,
    signal_memory: dict | None = None,
) -> None:
    """Skriver latest-signals.json — kompakt format för notify + SwiftBar.

    Format:
      {"date": "2026-05-18", "generated": "23:59",
       "cap_count": 0, "pb_count": 2,
       "signals": [{"ticker", "name", "type", "region", "entry",
                    "currency", "units", "mode", "stop_policy",
                    "position_value_sek", "risk_sek", "risk_pct"}, ...]}
    """
    do_sizing = _SIZING_AVAILABLE and portfolio > 0
    out_signals: list = []

    for r in results:
        s = r.get("signals")
        if not s:
            continue
        is_cap   = s.get("cap_trigger")
        is_pb    = s.get("pb_trigger")
        is_pb126 = s.get("pb126_trigger")
        if not (is_cap or is_pb or is_pb126):
            continue
        close      = s.get("close", float("nan"))
        atr20      = s.get("atr20", float("nan"))
        atr20_pct  = s.get("atr20_pct", float("nan"))
        currency   = r.get("currency", "USD")
        sig_type   = "capitulation" if is_cap else ("pullback" if is_pb else "pullback-sma126")

        entry: dict = {
            "ticker": r["ticker"],
            "name": r["name"],
            "type": sig_type,
            "region": r["region"],
            "entry": round(close, 4) if not np.isnan(close) else None,
            "open_gap_pct": round(s.get("open_gap_pct"), 2) if s.get("open_gap_pct") is not None and not np.isnan(s.get("open_gap_pct")) else None,
            "gap_risk": s.get("gap_risk"),
            "stop": None,
            "stop_policy": "no_hard_stop_5e2",
            "mode": "conviction",
            "currency": currency,
            "units": None,
            "position_value_sek": None,
            "risk_sek": None,
            "risk_pct": None,
            "risk_label": "smärttröskel_30pct",
            "daily_var_pct": None,
            "constraint": None,
            "execution": r.get("execution") or {},
            "manual_checks": list(MANUAL_TRADE_CHECKS),
            "trade_readiness": "MANUAL_CHECK_REQUIRED",
        }

        if do_sizing and not np.isnan(close):
            sz = _compute_conviction_sizing(r["ticker"], currency, close, atr20_pct, portfolio)
            if not sz.get("error"):
                entry["units"] = sz["units"]
                entry["position_value_sek"] = round(sz["position_value_sek"], 0)
                entry["risk_sek"] = round(sz["smartroskel_sek"], 0)
                entry["risk_pct"] = round(sz["smartroskel_pct"] * 100, 2)
                entry["daily_var_pct"] = (
                    round(sz["daily_var_pct"] * 100, 2)
                    if sz["daily_var_pct"] is not None else None
                )
                entry["constraint"] = sz["constraint"]

        out_signals.append(entry)

    payload = {
        "date": scan_date,
        "generated": datetime.now().strftime("%H:%M"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cap_count":   sum(1 for s in out_signals if s["type"] == "capitulation"),
        "pb_count":    sum(1 for s in out_signals if s["type"] == "pullback"),
        "pb126_count": sum(1 for s in out_signals if s["type"] == "pullback-sma126"),
        "err_count": sum(1 for r in results if r.get("error")),
        "skip_count": sum(1 for r in results if r.get("skipped")),
        "data_integrity": {
            "excluded_count": sum(
                1 for r in results
                if r.get("skipped") and str(r.get("reason", "")).startswith(("STALE_DATA", "SPLIT_ACTION"))
            ),
            "reasons": dict(Counter(
                str(r.get("reason", "")).split(" — ", 1)[0]
                for r in results
                if r.get("skipped") and str(r.get("reason", "")).startswith(("STALE_DATA", "SPLIT_ACTION"))
            )),
        },
        "total_scanned": len(results),
        "signals": out_signals,
    }
    if signal_memory is not None:
        payload["signal_memory"] = signal_memory
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_scan_js(
    results: list[dict],
    scan_date: str,
    path: Path,
    signal_memory: dict | None = None,
) -> None:
    """Skriver dashboard/scan_data.js direkt från in-memory results.

    Ersätter tidigare daily-scan-DATE.txt → export_scan_json.py → scan_data.js-
    omväg. UI:t läser samma window.SCAN_DATA-shape.
    """
    def _safe(v):
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            try:
                if np.isnan(v):
                    return None
            except (TypeError, ValueError):
                pass
        return v

    def _safe_deep(v):
        if isinstance(v, dict):
            return {k: _safe_deep(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            return [_safe_deep(val) for val in v]
        if isinstance(v, np.generic):
            v = v.item()
        return _safe(v)

    ok_results = [r for r in results if not r.get("error") and not r.get("skipped")]
    scanned = len(results)
    ok_count = len(ok_results)
    error_count = sum(1 for r in results if r.get("error"))
    usable_volume_count = sum(
        1 for r in ok_results
        if r.get("signals", {}).get("threshold_mode") in {"per-instrument", "proxy-volume"}
    )
    overlay_count = sum(
        1 for r in ok_results
        if r.get("quote", {}).get("intraday_overlay") is True
    )
    realtime_count = sum(
        1 for r in ok_results
        if r.get("quote", {}).get("latency") in {"REAL_TIME", "US_BEST_EFFORT_RT"}
    )
    analog_status_counts = Counter(
        r.get("analog", {}).get("verdict", "NO_SAMPLE") for r in ok_results
    )
    freshness_state_counts = Counter(
        r.get("signals", {}).get("freshness", {}).get("best_state", "NO_RECENT") for r in ok_results
    )
    data_integrity_counts = Counter(
        str(r.get("reason", "")).split(" — ", 1)[0]
        for r in results
        if r.get("skipped") and str(r.get("reason", "")).startswith(("STALE_DATA", "SPLIT_ACTION"))
    )
    regime_context_counts = Counter()
    for r in ok_results:
        ctx = r.get("signals", {}).get("regime_context", {}) or {}
        for label in ctx.get("labels", []) or []:
            regime_context_counts[label.get("id", "unknown")] += 1
    data_quality_score = 0
    if scanned:
        data_quality_score = round(
            100 * (
                0.35 * (ok_count / scanned) +
                0.25 * (usable_volume_count / scanned) +
                0.25 * (overlay_count / scanned) +
                0.15 * (1 - (error_count / scanned))
            )
        )

    meta = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": scanned,
        "ok": ok_count,
        "cap_signals":   sum(1 for r in results if r.get("signals", {}).get("cap_trigger")),
        "pb_signals":    sum(1 for r in results if r.get("signals", {}).get("pb_trigger")),
        "pb126_signals": sum(1 for r in results if r.get("signals", {}).get("pb126_trigger")),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "errors":  error_count,
        "scan_date": scan_date,
        "data_source": "Yahoo Finance via yfinance 1d + 15m overlay",
        "latency_counts": dict(Counter(
            r.get("quote", {}).get("latency_label", "?") for r in results
        )),
        "region_counts": dict(Counter(r.get("region", "?") for r in results)),
        "threshold_counts": dict(Counter(
            r.get("signals", {}).get("threshold_mode", "none") for r in results
        )),
        "analog_counts": dict(analog_status_counts),
        "freshness_counts": dict(freshness_state_counts),
        "data_integrity_counts": dict(data_integrity_counts),
        "regime_context_counts": dict(regime_context_counts),
        "overlay_count": overlay_count,
        "realtime_count": realtime_count,
        "data_quality_score": data_quality_score,
    }
    if signal_memory is not None:
        meta["signal_memory"] = _safe_deep(signal_memory)

    rows: list[dict] = []
    null_fields = (
        "bull_stack", "ri_rvol", "ri_rsi", "ri_dist252", "ri_dist21", "ri_dist126",
        "cap_count", "cap_gap_total", "pb_count", "pb_gap_total",
        "pb126_count", "pb126_gap_total",
        "delta_1d_table", "delta_5d_table",
        "close", "sma21", "sma52", "sma126", "sma252",
        "open", "prev_close", "open_gap_pct", "gap_risk",
        "rsi9", "rsi21", "rsi63",
        "rsi9_delta_1d", "rsi9_delta_5d",
        "rsi21_delta_5d", "rsi21_delta_21d",
        "rsi63_delta_21d", "rsi63_delta_42d",
        "ri_rsi21", "ri_rsi63", "ri_rsi63_delta_42d",
        "dirvol63", "dir_logvolz63", "dir_rvol_delta_1d", "dir_rvol_delta_5d",
        "ri_logvolz", "ri_rvol_delta_1d", "ri_rvol_delta_5d",
        "cap_logvolz_trigger", "pb126_logvolz_trigger",
        "dist_sma21", "dist_sma126", "dist_sma252",
        "atr20", "atr20_pct", "threshold_mode", "last_close_date",
        "volume_trust", "volume_reason", "volume_source_ticker",
        "sma126_above_sma252",
        "price_source", "data_latency", "data_latency_label", "delay_minutes",
        "quote_time", "quote_date", "intraday_overlay", "intraday_status",
        "execution_ticker", "execution_name", "execution_market", "execution_broker",
        "execution_reason", "execution_manual_checks", "execution_requires_manual_spread_check",
        "manual_checks", "trade_readiness",
        "isin", "exchange", "ter", "note", "reason", "analog", "freshness", "alignment", "regime_context",
    )

    for r in results:
        q = r.get("quote", {})
        if r.get("error"):
            row = {k: None for k in null_fields}
            row.update({
                "ticker": r["ticker"], "name": r["name"], "region": r["region"],
                "bars": None, "status": "FETCH_ERROR",
                "cap": False, "pb": False, "pb126": False,
                "currency": r.get("currency"),
                "isin": r.get("isin"),
                "exchange": r.get("exchange"),
                "ter": r.get("ter"),
                "note": r.get("note"),
                "reason": r.get("reason"),
                "analog": _safe_deep(r.get("analog")),
                "freshness": _safe_deep(r.get("signals", {}).get("freshness")),
                "alignment": _safe_deep(r.get("signals", {}).get("alignment")),
                "regime_context": _safe_deep(r.get("signals", {}).get("regime_context")),
                "price_source": q.get("price_source"),
                "data_latency": q.get("latency"),
                "data_latency_label": q.get("latency_label"),
                "delay_minutes": q.get("delay_minutes"),
                "quote_time": q.get("quote_time"),
                "quote_date": q.get("quote_date"),
                "intraday_overlay": q.get("intraday_overlay"),
                "intraday_status": q.get("intraday_status"),
                "execution_ticker": (r.get("execution") or {}).get("execution_ticker"),
                "execution_name": (r.get("execution") or {}).get("name"),
                "execution_market": (r.get("execution") or {}).get("market"),
                "execution_broker": (r.get("execution") or {}).get("broker"),
                "execution_reason": (r.get("execution") or {}).get("reason"),
                "execution_manual_checks": (r.get("execution") or {}).get("manual_checks"),
                "execution_requires_manual_spread_check": (r.get("execution") or {}).get("requires_manual_spread_check"),
            })
            rows.append(row)
            continue
        if r.get("skipped"):
            row = {k: None for k in null_fields}
            row.update({
                "ticker": r["ticker"], "name": r["name"], "region": r["region"],
                "bars": r.get("bars"), "status": "SKIPPED",
                "cap": None, "pb": None, "pb126": None,
                "currency": r.get("currency"),
                "isin": r.get("isin"),
                "exchange": r.get("exchange"),
                "ter": r.get("ter"),
                "note": r.get("note"),
                "reason": r.get("reason"),
                "analog": _safe_deep(r.get("analog")),
                "freshness": _safe_deep(r.get("signals", {}).get("freshness")),
                "alignment": _safe_deep(r.get("signals", {}).get("alignment")),
                "regime_context": _safe_deep(r.get("signals", {}).get("regime_context")),
                "price_source": q.get("price_source"),
                "data_latency": q.get("latency"),
                "data_latency_label": q.get("latency_label"),
                "delay_minutes": q.get("delay_minutes"),
                "quote_time": q.get("quote_time"),
                "quote_date": q.get("quote_date"),
                "intraday_overlay": q.get("intraday_overlay"),
                "intraday_status": q.get("intraday_status"),
                "execution_ticker": (r.get("execution") or {}).get("execution_ticker"),
                "execution_name": (r.get("execution") or {}).get("name"),
                "execution_market": (r.get("execution") or {}).get("market"),
                "execution_broker": (r.get("execution") or {}).get("broker"),
                "execution_reason": (r.get("execution") or {}).get("reason"),
                "execution_manual_checks": (r.get("execution") or {}).get("manual_checks"),
                "execution_requires_manual_spread_check": (r.get("execution") or {}).get("requires_manual_spread_check"),
            })
            rows.append(row)
            continue

        s = r["signals"]
        rows.append({
            "ticker": r["ticker"], "name": r["name"], "region": r["region"],
            "bars": r.get("bars"), "status": "OK",
            "bull_stack": bool(s["bull_stack"]),
            "ri_rvol":    _safe(s["ri_rvol"]),
            "ri_rsi":     _safe(s["ri_rsi"]),
            "ri_dist252": _safe(s["ri_dist252"]),
            "ri_dist21":  _safe(s["ri_dist21"]),
            "ri_dist126": _safe(s["ri_dist126"]),
            "cap":   bool(s["cap_trigger"]),
            "pb":    bool(s["pb_trigger"]),
            "pb126": bool(s["pb126_trigger"]),
            "cap_count":       int(s.get("cap_count", 0)),
            "cap_gap_total":   _safe(s.get("cap_gap_total")),
            "pb_count":        int(s.get("pb_count", 0)),
            "pb_gap_total":    _safe(s.get("pb_gap_total")),
            "pb126_count":     int(s.get("pb126_count", 0)),
            "pb126_gap_total": _safe(s.get("pb126_gap_total")),
            # UI-fallback-fält (terminal.js:285-286 läser dem om primärfält saknas)
            "delta_1d_table": _safe(s.get("rsi9_delta_1d")),
            "delta_5d_table": _safe(s.get("rsi9_delta_5d")),
            "close":  _safe(s["close"]),
            "open": _safe(s.get("open")),
            "prev_close": _safe(s.get("prev_close")),
            "open_gap_pct": _safe(s.get("open_gap_pct")),
            "gap_risk": s.get("gap_risk"),
            "sma21":  _safe(s["sma21"]),
            "sma52":  _safe(s["sma52"]),
            "sma126": _safe(s["sma126"]),
            "sma252": _safe(s["sma252"]),
            "rsi9":          _safe(s["rsi9"]),
            "rsi21":         _safe(s.get("rsi21")),
            "rsi63":         _safe(s.get("rsi63")),
            "rsi9_delta_1d": _safe(s.get("rsi9_delta_1d")),
            "rsi9_delta_5d": _safe(s.get("rsi9_delta_5d")),
            "rsi21_delta_5d":  _safe(s.get("rsi21_delta_5d")),
            "rsi21_delta_21d": _safe(s.get("rsi21_delta_21d")),
            "rsi63_delta_21d": _safe(s.get("rsi63_delta_21d")),
            "rsi63_delta_42d": _safe(s.get("rsi63_delta_42d")),
            "ri_rsi21": _safe(s.get("ri_rsi21")),
            "ri_rsi63": _safe(s.get("ri_rsi63")),
            "ri_rsi63_delta_42d": _safe(s.get("ri_rsi63_delta_42d")),
            "dirvol63":    _safe(s.get("dir_rvol63")),
            "dir_logvolz63": _safe(s.get("dir_logvolz63")),
            "dir_rvol_delta_1d": _safe(s.get("dir_rvol_delta_1d")),
            "dir_rvol_delta_5d": _safe(s.get("dir_rvol_delta_5d")),
            "ri_logvolz": _safe(s.get("ri_logvolz")),
            "ri_rvol_delta_1d": _safe(s.get("ri_rvol_delta_1d")),
            "ri_rvol_delta_5d": _safe(s.get("ri_rvol_delta_5d")),
            "cap_logvolz_trigger": bool(s.get("cap_logvolz_trigger")),
            "pb126_logvolz_trigger": bool(s.get("pb126_logvolz_trigger")),
            "dist_sma21":  _safe(s["dist_sma21"] * 100) if not (s.get("dist_sma21") is None or (isinstance(s.get("dist_sma21"), float) and np.isnan(s["dist_sma21"]))) else None,
            "dist_sma126": _safe(s["dist_sma126"] * 100) if not (s.get("dist_sma126") is None or (isinstance(s.get("dist_sma126"), float) and np.isnan(s["dist_sma126"]))) else None,
            "dist_sma252": _safe(s["dist_sma252"] * 100) if not (s.get("dist_sma252") is None or (isinstance(s.get("dist_sma252"), float) and np.isnan(s["dist_sma252"]))) else None,
            "atr20":     _safe(s.get("atr20")),
            "atr20_pct": _safe(s.get("atr20_pct") * 100) if not (s.get("atr20_pct") is None or (isinstance(s.get("atr20_pct"), float) and np.isnan(s.get("atr20_pct", float("nan"))))) else None,
            "threshold_mode": s.get("threshold_mode"),
            "volume_trust": s.get("volume_trust"),
            "volume_reason": s.get("volume_reason"),
            "volume_source_ticker": s.get("volume_source_ticker"),
            "currency": r.get("currency"),
            "isin": r.get("isin"),
            "exchange": r.get("exchange"),
            "ter": r.get("ter"),
            "note": r.get("note"),
            "reason": r.get("reason"),
            "analog": _safe_deep(r.get("analog")),
            "freshness": _safe_deep(s.get("freshness")),
            "alignment": _safe_deep(s.get("alignment")),
            "regime_context": _safe_deep(s.get("regime_context")),
            "last_close_date": r.get("last_close_date"),
            "sma126_above_sma252": bool(s.get("sma126_above_sma252", False)),
            "price_source": q.get("price_source"),
            "data_latency": q.get("latency"),
            "data_latency_label": q.get("latency_label"),
            "delay_minutes": q.get("delay_minutes"),
            "quote_time": q.get("quote_time"),
            "quote_date": q.get("quote_date"),
            "intraday_overlay": q.get("intraday_overlay"),
            "intraday_status": q.get("intraday_status"),
            "execution_ticker": (r.get("execution") or {}).get("execution_ticker"),
            "execution_name": (r.get("execution") or {}).get("name"),
            "execution_market": (r.get("execution") or {}).get("market"),
            "execution_broker": (r.get("execution") or {}).get("broker"),
            "execution_reason": (r.get("execution") or {}).get("reason"),
            "execution_manual_checks": (r.get("execution") or {}).get("manual_checks"),
            "execution_requires_manual_spread_check": (r.get("execution") or {}).get("requires_manual_spread_check"),
            "manual_checks": _safe_deep(s.get("manual_checks")),
            "trade_readiness": s.get("trade_readiness"),
        })

    rows_json = json.dumps(rows, indent=4, ensure_ascii=False)
    meta_json = json.dumps(meta, indent=4, ensure_ascii=False)
    rows_indented = "\n  ".join(rows_json.splitlines())
    meta_indented = "\n  ".join(meta_json.splitlines())

    content = (
        f"// Auto-generated by daily_scan.py (in-memory, scan_date={scan_date})\n"
        f"window.SCAN_DATA = {{\n"
        f"  meta: {meta_indented},\n"
        f"  rows: {rows_indented}\n"
        f"}};\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daglig multi-region ETF-scanner (Framework-v2)"
    )
    parser.add_argument(
        "--universe-file",
        default="data/scanner_universe.json",
        help="Sökväg till universe JSON (default: data/scanner_universe.json)",
    )
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y-%m-%d"),
        help="Scandatum YYYY-MM-DD (default: idag)",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Mapp för outputfiler (default: data/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Kör bara 5 tickers och avbryt (smoke-test)",
    )
    parser.add_argument(
        "--portfolio",
        type=float,
        default=60_000,
        help="Portföljstorlek i SEK för position sizing (default: 60000, sätt 0 för att inaktivera)",
    )
    args = parser.parse_args()

    scan_date  = args.date
    try:
        runtime_date = date.fromisoformat(args.date)
    except ValueError:
        runtime_date = date.today()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    universe_path = Path(args.universe_file)
    if not universe_path.exists():
        raise SystemExit(
            f"ERROR: universe file saknas: {universe_path}. "
            "Avbryter hellre än att köra en stale fallback utan kalibrerade thresholds."
        )

    with open(universe_path, encoding="utf-8") as f:
        universe = json.load(f)

    instruments = universe.get("instruments", [])
    execution_map = load_execution_map(Path("data/execution_map.json"))

    # Build per-instrument threshold lookup from calibrated thresholds in universe JSON.
    # For index rows with a volume proxy, price/SMA/RSI stay on the index while
    # DirRVOL thresholds and DirRVOL source come from the tradable proxy.
    raw_thresholds_by_ticker = {
        inst.get("ticker"): inst.get("thresholds", {})
        for inst in instruments
        if inst.get("ticker")
    }
    inst_thresholds: dict[str, dict] = {}
    direct_volume_count = 0
    proxy_volume_count = 0
    volume_blocked_count = 0
    for inst in instruments:
        ticker_for_thresholds = inst.get("ticker")
        t = dict(inst.get("thresholds", {}) or {})
        volume_policy = inst.get("volume_policy", {}) or {}
        if volume_policy.get("live_dirvol") == "proxy":
            proxy_ticker = volume_policy.get("proxy_ticker")
            proxy_thresholds = raw_thresholds_by_ticker.get(proxy_ticker, {})
            for key in (
                "rvol_p70", "rvol_p85", "rvol_p95",
                "logvolz_p70", "logvolz_p85", "logvolz_p95",
            ):
                if proxy_thresholds.get(key) is not None:
                    t[key] = proxy_thresholds[key]
            t["volume_source"] = "proxy"
            t["volume_source_ticker"] = proxy_ticker
            t["volume_policy"] = volume_policy
        if t and not t.get("fetch_fail") and not t.get("vol_fail") and not t.get("skip"):
            if t.get("rvol_p85") is not None:
                inst_thresholds[ticker_for_thresholds] = t
                if _volume_block_reason(ticker_for_thresholds, t) is None:
                    if t.get("volume_source") == "proxy":
                        proxy_volume_count += 1
                    else:
                        direct_volume_count += 1
                else:
                    volume_blocked_count += 1

    print(f"\n=== Daily ETF Scan — {scan_date} ===")
    print(f"Universe: {len(instruments)} instrument från {universe_path}")
    print(
        f"Volume thresholds: {direct_volume_count} direct + {proxy_volume_count} proxy"
        f" / {len(instruments)} usable ({volume_blocked_count} blocked)"
    )
    if args.dry_run:
        print("DRY-RUN: stannar efter 5 tickers\n")
    else:
        print()

    results: list[dict] = []
    feature_frames: dict[str, pd.DataFrame] = {}

    for idx, inst in enumerate(instruments):
        if args.dry_run and idx >= 5:
            print(f"  [DRY-RUN] Stannar efter 5 tickers.")
            break

        ticker = inst["ticker"]
        name   = inst.get("name", ticker)
        region = inst.get("region", "?")

        print(f"  [{ticker}] {name} ...", end=" ", flush=True)

        df = fetch_ohlcv(ticker)

        if df is None:
            print("FETCH_ERROR")
            results.append({
                "ticker": ticker, "name": name, "region": region,
                "currency": inst.get("currency", "USD"),
                "isin": inst.get("isin"),
                "exchange": inst.get("exchange"),
                "ter": inst.get("ter"),
                "note": inst.get("note"),
                "execution": execution_map.get(ticker, {}),
                "error": True,
                "quote": _empty_quote_meta(ticker, "fetch_error"),
            })
            continue

        df, quote_meta = _overlay_intraday(ticker, df)
        last_close_date = pd.Timestamp(df.index[-1]).date()

        if _is_stale_close(last_close_date, runtime_date, ticker):
            reason = (
                f"STALE_DATA — last close {last_close_date.isoformat()} "
                f"är äldre än tillåten handelskalender-tolerans före scan {runtime_date.isoformat()}"
            )
            print(f"SKIPPED — {reason}")
            results.append({
                "ticker": ticker, "name": name, "region": region,
                "currency": inst.get("currency", "USD"),
                "isin": inst.get("isin"),
                "exchange": inst.get("exchange"),
                "ter": inst.get("ter"),
                "note": inst.get("note"),
                "execution": execution_map.get(ticker, {}),
                "skipped": True, "bars": len(df), "reason": reason,
                "last_close_date": last_close_date.isoformat(),
                "quote": quote_meta,
            })
            continue

        split_event = _recent_split_event(ticker, runtime_date)
        if split_event:
            reason = (
                f"SPLIT_ACTION — stock split {split_event['factor']:g} "
                f"rapporterad {split_event['date']}; exkludera 90d DirRVOL-fönster"
            )
            print(f"SKIPPED — {reason}")
            results.append({
                "ticker": ticker, "name": name, "region": region,
                "currency": inst.get("currency", "USD"),
                "isin": inst.get("isin"),
                "exchange": inst.get("exchange"),
                "ter": inst.get("ter"),
                "note": inst.get("note"),
                "execution": execution_map.get(ticker, {}),
                "skipped": True, "bars": len(df), "reason": reason,
                "last_close_date": last_close_date.isoformat(),
                "quote": quote_meta,
            })
            continue

        if len(df) < MIN_HISTORY:
            reason = f"endast {len(df)} bars (kräver {MIN_HISTORY})"
            print(f"SKIPPED — {reason}")
            results.append({
                "ticker": ticker, "name": name, "region": region,
                "currency": inst.get("currency", "USD"),
                "isin": inst.get("isin"),
                "exchange": inst.get("exchange"),
                "ter": inst.get("ter"),
                "note": inst.get("note"),
                "execution": execution_map.get(ticker, {}),
                "skipped": True, "bars": len(df), "reason": reason,
                "quote": quote_meta,
            })
            continue

        ticker_thresholds = inst_thresholds.get(ticker)
        dir_rvol_source = None
        proxy_ticker = (ticker_thresholds or {}).get("volume_source_ticker")
        if proxy_ticker:
            proxy_df = fetch_ohlcv(proxy_ticker)
            if proxy_df is None:
                ticker_thresholds = dict(ticker_thresholds or {})
                ticker_thresholds["fetch_fail"] = True
                ticker_thresholds["volume_proxy_error"] = f"proxy fetch failed: {proxy_ticker}"
            else:
                proxy_df, _proxy_quote_meta = _overlay_intraday(proxy_ticker, proxy_df)
                proxy_last_close = pd.Timestamp(proxy_df.index[-1]).date()
                if _is_stale_close(proxy_last_close, runtime_date, proxy_ticker):
                    ticker_thresholds = dict(ticker_thresholds or {})
                    ticker_thresholds["fetch_fail"] = True
                    ticker_thresholds["volume_proxy_error"] = (
                        f"proxy stale: {proxy_ticker} last close {proxy_last_close.isoformat()}"
                    )
                else:
                    dir_rvol_source = proxy_df

        df = build_features(df, dir_rvol_source=dir_rvol_source)
        feature_frames[ticker] = df.copy()
        signals = eval_signals(df, inst_thresholds=ticker_thresholds, ticker=ticker)
        analog = compute_analog_expectancy(df, signals, inst_thresholds=ticker_thresholds, ticker=ticker)

        cap_flag   = "CAP"  if signals["cap_trigger"]   else "-"
        pb_flag    = "PB"   if signals["pb_trigger"]    else "-"
        pb126_flag = "PB126" if signals["pb126_trigger"] else "-"
        thresh_mode = signals.get("threshold_mode", "?")
        print(
            f"OK  |  bull={'Y' if signals['bull_stack'] else 'N'}  "
            f"|  cap={cap_flag}  pb={pb_flag}  pb126={pb126_flag}  "
            f"[{thresh_mode}; {quote_meta.get('latency_label')}; {quote_meta.get('intraday_status')}]"
        )

        results.append({
            "ticker":          ticker,
            "name":            name,
            "region":          region,
            "currency":        inst.get("currency", "USD"),
            "isin":            inst.get("isin"),
            "exchange":        inst.get("exchange"),
            "ter":             inst.get("ter"),
            "note":            inst.get("note"),
            "execution":        execution_map.get(ticker, {}),
            "bars":            len(df),
            "signals":         signals,
            "analog":          analog,
            "last_close_date": df.index[-1].strftime("%Y-%m-%d"),
            "quote":           quote_meta,
        })

    apply_regime_research_labels(results, feature_frames, inst_thresholds)

    # Härled scan_date från senaste close-datum i data — undviker midnatt-drift i filnamn.
    # Om skriptet körs 00:25 (ny dag) men data är från igår: scan_date korrigeras automatiskt.
    _close_dates = [
        r["last_close_date"] for r in results
        if not r.get("error") and not r.get("skipped") and r.get("last_close_date")
    ]
    if _close_dates:
        _data_date = Counter(_close_dates).most_common(1)[0][0]
        if _data_date != scan_date:
            print(f"  INFO scan_date: runtime={scan_date} → data={_data_date} (senaste close-datum i data)")
            scan_date = _data_date

    run_stamp = datetime.now().strftime("%H%M%S")
    if args.dry_run:
        scan_path = output_dir / f"daily-scan-{scan_date}-dry-run-{run_stamp}.txt"
        watch_path = output_dir / f"watchlist-{scan_date}-dry-run-{run_stamp}.md"
        write_full_scan(results, scan_date, scan_path)
        write_watchlist(results, scan_date, watch_path, portfolio=args.portfolio)
        print(f"\nDRY-RUN klart: skrev endast separata smoke-filer.")
        print(f"Rapport:  {scan_path}")
        print(f"Watchlist: {watch_path}")
        return

    # Skriv timestampad arkivfil först, kopiera sedan till canonical latest.
    # Det bevarar körningshistorik utan att bryta terminal/wrapper-kontraktet.
    scan_archive_path = output_dir / f"daily-scan-{scan_date}-{run_stamp}.txt"
    watch_archive_path = output_dir / f"watchlist-{scan_date}-{run_stamp}.md"
    scan_path  = output_dir / f"daily-scan-{scan_date}.txt"
    watch_path = output_dir / f"watchlist-{scan_date}.md"
    signals_json_path = output_dir / "latest-signals.json"
    signal_journal_path = output_dir / "signal-journal.json"

    write_full_scan(results, scan_date, scan_archive_path)
    scan_path.write_text(scan_archive_path.read_text(encoding="utf-8"), encoding="utf-8")
    write_watchlist(results, scan_date, watch_archive_path, portfolio=args.portfolio)
    watch_path.write_text(watch_archive_path.read_text(encoding="utf-8"), encoding="utf-8")
    signal_memory = update_signal_journal(
        results,
        scan_date,
        signal_journal_path,
        feature_frames=feature_frames,
        inst_thresholds_by_ticker=inst_thresholds,
    )
    write_signals_json(
        results,
        scan_date,
        signals_json_path,
        portfolio=args.portfolio,
        signal_memory=signal_memory,
    )

    # Skriv scan_data.js direkt från in-memory results (eliminerar markdown→regex-omvägen)
    try:
        dashboard_js = Path(__file__).parent.parent / "dashboard" / "scan_data.js"
        write_scan_js(results, scan_date, dashboard_js, signal_memory=signal_memory)
        print(f"Dashboard: scan_data.js uppdaterad")
    except Exception as exc:
        print(f"VARNING: kunde inte skriva scan_data.js: {exc}", file=sys.stderr)

    cap_count   = sum(1 for r in results if r.get("signals", {}).get("cap_trigger"))
    pb_count    = sum(1 for r in results if r.get("signals", {}).get("pb_trigger"))
    pb126_count = sum(1 for r in results if r.get("signals", {}).get("pb126_trigger"))
    skip_count  = sum(1 for r in results if r.get("skipped"))
    err_count   = sum(1 for r in results if r.get("error"))
    integrity_count = sum(
        1 for r in results
        if r.get("skipped") and str(r.get("reason", "")).startswith(("STALE_DATA", "SPLIT_ACTION"))
    )

    print(f"\nResultat: {cap_count} capitulation | {pb_count} pullback | {pb126_count} pb126 | "
          f"{skip_count} skippade | {err_count} fel")
    print(f"Rapport:  {scan_path}")
    print(f"Arkiv:    {scan_archive_path}")
    print(f"Watchlist: {watch_path}")

    total = len(results)
    err_ratio = (err_count / total) if total else 0.0
    alert_path = output_dir / "SCAN_ALERT.txt"
    if err_ratio > 0.5:
        msg = (
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"FETCH_FAILURE — {err_count}/{total} tickers ({err_ratio:.0%}) "
            f"misslyckades. Rapport: {scan_path}\n"
        )
        with alert_path.open("a", encoding="utf-8") as fh:
            fh.write(msg)
        print(f"\nALERT skriven till {alert_path}", flush=True)
        sys.exit(2)
    elif integrity_count:
        reasons = Counter(
            str(r.get("reason", "")).split(" — ", 1)[0]
            for r in results
            if r.get("skipped") and str(r.get("reason", "")).startswith(("STALE_DATA", "SPLIT_ACTION"))
        )
        msg = (
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"DATA_INTEGRITY — {integrity_count}/{total} tickers exkluderade "
            f"({dict(reasons)}). Rapport: {scan_path}\n"
        )
        with alert_path.open("a", encoding="utf-8") as fh:
            fh.write(msg)
        print(f"\nDATA_INTEGRITY alert skriven till {alert_path}", flush=True)
    else:
        if alert_path.exists():
            alert_path.unlink()


if __name__ == "__main__":
    main()
