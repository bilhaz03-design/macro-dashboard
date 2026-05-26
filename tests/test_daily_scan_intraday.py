import sys
import json
from pathlib import Path
from datetime import date

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import daily_scan


def _daily_frame(index):
    return pd.DataFrame(
        {
            "Open": [100.0] * len(index),
            "High": [101.0] * len(index),
            "Low": [99.0] * len(index),
            "Close": [100.0] * len(index),
            "Volume": [1000.0] * len(index),
        },
        index=index,
    )


def _intraday_frame(index):
    return pd.DataFrame(
        {
            "Open": [101.0, 102.0, 103.0],
            "High": [102.0, 104.0, 105.0],
            "Low": [100.0, 101.0, 102.0],
            "Close": [102.0, 103.0, 104.0],
            "Volume": [10.0, 20.0, 30.0],
        },
        index=index,
    )


def test_intraday_overlay_appends_new_session(monkeypatch):
    daily = _daily_frame(pd.to_datetime(["2026-05-15", "2026-05-18"]))
    intra_idx = pd.date_range("2026-05-19 09:00", periods=3, freq="15min", tz="Europe/Stockholm")
    monkeypatch.setattr(daily_scan.yf, "download", lambda *args, **kwargs: _intraday_frame(intra_idx))

    out, meta = daily_scan._overlay_intraday("XACT-OMXS30.ST", daily)

    assert len(out) == 3
    assert out.index[-1] == pd.Timestamp("2026-05-19")
    assert out.iloc[-1]["Close"] == 104.0
    assert out.iloc[-1]["High"] == 105.0
    assert out.iloc[-1]["Low"] == 100.0
    assert out.iloc[-1]["Volume"] == 60.0
    assert meta["intraday_overlay"] is True
    assert meta["intraday_status"] == "appended_intraday"
    assert meta["latency"] == "REAL_TIME"


def test_intraday_overlay_replaces_current_daily_bar(monkeypatch):
    daily = _daily_frame(pd.to_datetime(["2026-05-18", "2026-05-19"]))
    daily.loc[pd.Timestamp("2026-05-19"), "High"] = 106.0
    daily.loc[pd.Timestamp("2026-05-19"), "Low"] = 98.0
    intra_idx = pd.date_range("2026-05-19 09:00", periods=3, freq="15min", tz="Europe/Berlin")
    monkeypatch.setattr(daily_scan.yf, "download", lambda *args, **kwargs: _intraday_frame(intra_idx))

    out, meta = daily_scan._overlay_intraday("DBXD.DE", daily)

    assert len(out) == 2
    assert out.iloc[-1]["Close"] == 104.0
    assert out.iloc[-1]["High"] == 106.0
    assert out.iloc[-1]["Low"] == 98.0
    assert out.iloc[-1]["Volume"] == 60.0
    assert meta["intraday_status"] == "replaced_latest_daily"
    assert meta["latency"] == "DELAY_15M"
    assert meta["latency_label"] == "15m"


def test_intraday_overlay_keeps_daily_when_intraday_missing(monkeypatch):
    daily = _daily_frame(pd.to_datetime(["2026-05-18", "2026-05-19"]))
    monkeypatch.setattr(daily_scan.yf, "download", lambda *args, **kwargs: pd.DataFrame())

    out, meta = daily_scan._overlay_intraday("2800.HK", daily)

    assert out.equals(daily)
    assert meta["intraday_overlay"] is False
    assert meta["intraday_status"] == "no_intraday"
    assert meta["latency"] == "DELAY_15M"


def test_latency_profile_exchange_suffixes():
    assert daily_scan._latency_profile("XACT-OMXS30.ST")["latency_label"] == "RT"
    assert daily_scan._latency_profile("DBXD.DE")["latency_label"] == "15m"
    assert daily_scan._latency_profile("TDT.AS")["latency_label"] == "15m"
    assert daily_scan._latency_profile("VMID.L")["latency_label"] == "20m"
    assert daily_scan._latency_profile("OBXD.OL")["latency_label"] == "15m"


def _signal_result():
    return [{
        "ticker": "TEST.DE",
        "name": "Test ETF",
        "region": "Europa",
        "currency": "EUR",
        "signals": {
            "cap_trigger": True,
            "pb_trigger": False,
            "pb126_trigger": False,
            "close": 100.0,
            "atr20": 2.0,
            "atr20_pct": 0.02,
            "ri_rvol": -1.2,
            "ri_rsi": -1.3,
            "ri_dist252": -1.4,
            "dir_rvol63": -1.1,
            "dist_sma252": -0.12,
            "sma252": 115.0,
            "rsi9": 24.0,
        },
    }]


def test_latest_signals_uses_no_hard_stop_conviction(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_scan, "_compute_conviction_sizing", lambda *args, **kwargs: {
        "units": 100,
        "position_value_sek": 10_000.0,
        "smartroskel_sek": 3_000.0,
        "smartroskel_pct": 0.05,
        "daily_var_pct": 0.02,
        "constraint": "conviction",
        "fx": 1.0,
        "error": None,
    })
    path = tmp_path / "latest-signals.json"

    daily_scan.write_signals_json(_signal_result(), "2026-05-19", path, portfolio=60_000)

    payload = json.loads(path.read_text())
    sig = payload["signals"][0]
    assert sig["mode"] == "conviction"
    assert sig["stop"] is None
    assert sig["stop_policy"] == "no_hard_stop_5e2"
    assert sig["risk_label"] == "smärttröskel_30pct"
    assert sig["position_value_sek"] == 10_000


def test_latest_signals_counts_pb126(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_scan, "_compute_conviction_sizing", lambda *args, **kwargs: {
        "units": 100,
        "position_value_sek": 10_000.0,
        "smartroskel_sek": 3_000.0,
        "smartroskel_pct": 0.05,
        "daily_var_pct": 0.02,
        "constraint": "conviction",
        "fx": 1.0,
        "error": None,
    })
    row = _signal_result()[0]
    row["signals"]["cap_trigger"] = False
    row["signals"]["pb126_trigger"] = True
    path = tmp_path / "latest-signals.json"

    daily_scan.write_signals_json([row], "2026-05-19", path, portfolio=60_000)

    payload = json.loads(path.read_text())
    assert payload["cap_count"] == 0
    assert payload["pb_count"] == 0
    assert payload["pb126_count"] == 1
    assert payload["signals"][0]["type"] == "pullback-sma126"


def test_latest_signals_surfaces_data_integrity_exclusions(tmp_path):
    path = tmp_path / "latest-signals.json"
    skipped = {
        "ticker": "STALE.DE",
        "name": "Stale ETF",
        "region": "Europa",
        "currency": "EUR",
        "skipped": True,
        "reason": "STALE_DATA — last close 2026-05-20 är >3 kalenderdagar före scan 2026-05-26",
    }

    daily_scan.write_signals_json([*_signal_result(), skipped], "2026-05-26", path, portfolio=0)

    payload = json.loads(path.read_text())
    assert payload["data_integrity"]["excluded_count"] == 1
    assert payload["data_integrity"]["reasons"] == {"STALE_DATA": 1}


def test_signal_journal_keeps_faded_intraday_signal(tmp_path):
    active = _signal_result()[0]
    active["signals"]["cap_trigger"] = False
    active["signals"]["pb126_trigger"] = True
    active["quote"] = {
        "intraday_overlay": True,
        "intraday_status": "appended_intraday",
        "quote_time": "2026-05-21T11:00:00+02:00",
        "quote_date": "2026-05-21",
    }
    path = tmp_path / "signal-journal.json"

    first = daily_scan.update_signal_journal([active], "2026-05-21", path)

    assert first["today_count"] == 1
    assert first["active_today_count"] == 1
    assert first["new_this_run"][0]["ticker"] == "TEST.DE"

    faded = _signal_result()[0]
    faded["signals"]["cap_trigger"] = False
    faded["signals"]["pb126_trigger"] = False

    second = daily_scan.update_signal_journal([faded], "2026-05-21", path)

    assert second["today_count"] == 1
    assert second["active_today_count"] == 0
    assert second["faded_today_count"] == 1
    assert second["faded_this_run"][0]["status"] == "FADED_INTRADAY"
    assert second["today"][0]["label"] == "PB126"


def test_signal_journal_close_confirms_on_later_bar(monkeypatch, tmp_path):
    path = tmp_path / "signal-journal.json"
    path.write_text(json.dumps({
        "version": 1,
        "signals": [{
            "key": "2026-05-20|TEST.DE|pb126",
            "date": "2026-05-20",
            "ticker": "TEST.DE",
            "name": "Test ETF",
            "signal_key": "pb126",
            "label": "PB126",
            "active": False,
            "status": "FADED_INTRADAY",
            "first_seen_at": "2026-05-20T11:00:00",
            "last_seen_at": "2026-05-20T14:00:00",
        }],
    }))
    monkeypatch.setattr(daily_scan, "_signal_flags_at", lambda *args, **kwargs: {
        "cap": False,
        "pb": False,
        "pb126": True,
    })
    frame = pd.DataFrame(index=pd.to_datetime(["2026-05-20", "2026-05-21"]), data={"Close": [100, 101]})

    out = daily_scan.update_signal_journal(
        [],
        "2026-05-21",
        path,
        feature_frames={"TEST.DE": frame},
        inst_thresholds_by_ticker={"TEST.DE": {}},
    )

    assert out["closed_this_run"][0]["status"] == "CLOSE_CONFIRMED"
    assert out["close_confirmed_count"] == 1


def test_price_gap_state_thresholds():
    assert daily_scan.price_gap_state(0.5) == "OK"
    assert daily_scan.price_gap_state(-3.2) == "GAP_REVIEW"
    assert daily_scan.price_gap_state(7.2) == "GAP_BLOCK_REVIEW"


def test_load_execution_map(tmp_path):
    path = tmp_path / "execution_map.json"
    path.write_text(json.dumps({"mappings": {"FLXC.DE": {"execution_ticker": "ICGA"}}}))

    assert daily_scan.load_execution_map(path)["FLXC.DE"]["execution_ticker"] == "ICGA"


def test_stale_close_guard_allows_weekend_but_blocks_older_data():
    assert daily_scan._is_stale_close(date(2026, 5, 15), date(2026, 5, 18)) is False
    assert daily_scan._is_stale_close(date(2026, 5, 14), date(2026, 5, 18)) is True


def test_stale_close_guard_allows_us_memorial_day_gap_only_for_us_tickers():
    # NYSE is closed on Monday 2026-05-25. A US ETF with Friday close is not
    # stale during the Tuesday session before the new daily close is available.
    assert daily_scan._is_stale_close(date(2026, 5, 22), date(2026, 5, 26), "EWT") is False

    # On a normal US week, Friday close should be stale by Tuesday because a
    # Monday daily close should already exist.
    assert daily_scan._is_stale_close(date(2026, 5, 15), date(2026, 5, 19), "EWT") is True

    # Without a verified market-specific holiday calendar, non-US suffixes use
    # weekday-only fallback and do not inherit NYSE holidays.
    assert daily_scan._is_stale_close(date(2026, 5, 22), date(2026, 5, 26), "DBXD.DE") is True


def test_recent_split_event_detects_stock_splits(monkeypatch):
    class FakeTicker:
        @property
        def actions(self):
            idx = pd.to_datetime(["2026-04-15"])
            return pd.DataFrame({"Stock Splits": [2.0], "Dividends": [0.0]}, index=idx)

    monkeypatch.setattr(daily_scan.yf, "Ticker", lambda ticker: FakeTicker())

    event = daily_scan._recent_split_event("TEST.DE", date(2026, 5, 20))

    assert event == {"date": "2026-04-15", "factor": 2.0}


def test_watchlist_surfaces_no_hard_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_scan, "_compute_conviction_sizing", lambda *args, **kwargs: {
        "units": 100,
        "position_value_sek": 10_000.0,
        "smartroskel_sek": 3_000.0,
        "smartroskel_pct": 0.05,
        "daily_var_pct": 0.02,
        "constraint": "conviction",
        "fx": 1.0,
        "error": None,
    })
    path = tmp_path / "watchlist.md"

    daily_scan.write_watchlist(_signal_result(), "2026-05-19", path, portfolio=60_000)

    text = path.read_text()
    assert "Mode: conviction/no hard stop" in text
    assert "Operativ stop | Ingen hård stop" in text
    assert "Stop-loss" not in text
