#!/usr/bin/env python3
"""Refresh current stock scanner signals without rerunning the full research audit."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stock_framework_deep_research import (  # noqa: E402
    CURRENT_OUT,
    add_features,
    current_events,
    fetch_ohlcv_batch,
    fetch_ohlcv,
    signal_specs,
    universe,
)

COVERAGE_OUT = ROOT / "data" / "stock_framework_current_scan_coverage.json"
CURRENT_SCAN_PERIOD = os.environ.get("SWING_TERMINAL_STOCK_CURRENT_PERIOD", "8y")


def main() -> int:
    current: list[dict] = []
    coverage: list[dict] = []
    stocks = universe()
    batch = fetch_ohlcv_batch([stock.ticker for stock in stocks], period=CURRENT_SCAN_PERIOD)
    print(f"[stock_current_scan] batch period={CURRENT_SCAN_PERIOD} tickers={len(stocks)}", flush=True)

    for index, stock in enumerate(stocks, 1):
        print(f"[stock_current_scan] [{index:03d}/{len(stocks)}] {stock.ticker}", flush=True)
        df = batch.get(stock.ticker)
        if df is None:
            df = fetch_ohlcv(stock.ticker, period=CURRENT_SCAN_PERIOD)
        if df is None or len(df) < 756:
            coverage.append({
                "ticker": stock.ticker,
                "name": stock.name,
                "market": stock.market,
                "group": stock.group,
                "status": "FAIL",
                "bars": 0 if df is None else len(df),
            })
            continue

        features = add_features(df)
        coverage.append({
            "ticker": stock.ticker,
            "name": stock.name,
            "market": stock.market,
            "group": stock.group,
            "status": "OK",
            "bars": len(features),
            "start": features.index.min().date().isoformat(),
            "end": features.index.max().date().isoformat(),
        })

        for signal, (mask, _) in signal_specs(features).items():
            current.extend(current_events(features, stock, signal, mask))

    current.sort(key=lambda row: (row.get("date", ""), row.get("ticker", ""), row.get("signal", "")), reverse=True)
    CURRENT_OUT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_OUT.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    COVERAGE_OUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "period": CURRENT_SCAN_PERIOD,
                "stocks": len(stocks),
                "ok": sum(1 for row in coverage if row["status"] == "OK"),
                "fail": sum(1 for row in coverage if row["status"] == "FAIL"),
                "current": len(current),
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[stock_current_scan] wrote {CURRENT_OUT}")
    print(f"[stock_current_scan] current={len(current)} ok={sum(1 for row in coverage if row['status'] == 'OK')} fail={sum(1 for row in coverage if row['status'] == 'FAIL')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
