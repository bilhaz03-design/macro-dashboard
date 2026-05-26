#!/usr/bin/env python3
"""MLPB Prime framework research.

This is the second-pass audit requested after the broader ticker expansion:
1. harder MLPB backtest,
2. visual quality filters,
3. theme performance,
4. universe quality review,
5. signal tiering with QT Prime Lite.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mlpb_prime_utils import prime_tier

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "data" / "mlpb_final_falsification_events.json"
GATE_PATH = ROOT / "data" / "mlpb_current_trade_gate.json"
OUT = ROOT / "data" / f"mlpb_prime_framework_{datetime.now().strftime('%Y-%m-%d')}.txt"
JSON_OUT = ROOT / "data" / "mlpb_prime_framework_summary.json"


def pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value * 100:+.2f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value:.{digits}f}"


def winsor_mean(series: pd.Series, q: float = 0.05) -> float:
    clean = series.dropna()
    if clean.empty:
        return np.nan
    lo, hi = clean.quantile(q), clean.quantile(1 - q)
    return float(clean.clip(lo, hi).mean())


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "n": 0,
            "nextopen21": np.nan,
            "close21": np.nan,
            "median21": np.nan,
            "winsor21": np.nan,
            "hit21": np.nan,
            "mae21_p10": np.nan,
            "mfe21_p50": np.nan,
            "gap_next_open": np.nan,
        }
    return {
        "n": int(len(df)),
        "nextopen21": float(df["nextopen_fwd21"].mean()),
        "close21": float(df["fwd21"].mean()),
        "median21": float(df["nextopen_fwd21"].median()),
        "winsor21": winsor_mean(df["nextopen_fwd21"]),
        "hit21": float((df["nextopen_fwd21"] > 0).mean()),
        "mae21_p10": float(df["nextopen_mae21"].quantile(0.10)),
        "mfe21_p50": float(df["mfe21"].median()),
        "gap_next_open": float(df["gap_next_open"].mean()),
    }


def summary_table(rows: list[dict[str, Any]], sort_by: str = "nextopen21") -> pd.DataFrame:
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table = table.sort_values(sort_by, ascending=False)
    for col in ("nextopen21", "close21", "median21", "winsor21", "hit21", "mae21_p10", "mfe21_p50", "gap_next_open"):
        if col in table.columns:
            table[col] = table[col].map(pct)
    return table


def text_bucket(df: pd.DataFrame, col: str, default: str = "UNKNOWN") -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index)
    return df[col].fillna(default)


def bucketize(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    out["dist_bucket"] = pd.cut(
        out["dist_sma"],
        [-np.inf, 0, 0.04, 0.08, 0.12, np.inf],
        labels=["lost_sma", "0-4%", "4-8%", "8-12%", ">12%"],
    )
    out["atr_bucket"] = pd.cut(
        out["atr14_pct"],
        [-np.inf, 0.05, 0.08, 0.12, np.inf],
        labels=["<=5%", "5-8%", "8-12%", ">12%"],
    )
    out["visual_bucket"] = text_bucket(out, "visual_grade")
    out["qt_bucket"] = text_bucket(out, "qt_label")
    out["qt_phase_bucket"] = text_bucket(out, "qt_phase")
    out["qt_wait_bucket"] = text_bucket(out, "qt_wait_label")
    out["prime_pass"] = (
        (out["setup"] == "MLPB50")
        & (out["variant"] == "STRICT")
        & (out["visual_bucket"] == "CLEAN")
        & (out["qt_bucket"] == "QT_SUPPORT")
        & (out["qt_phase_bucket"].isin(["REPAIRING", "SUPPORTED_PULLBACK"]))
        & (out["qt_wait_bucket"] != "HIGH_WAIT_VALUE")
        & (out["dist_sma"].between(0, 0.08))
        & (out["atr14_pct"] <= 0.08)
        & (out["nextopen_mae21"] > -0.24)
    )
    out["prime_watch"] = (
        out["variant"].isin(["QUALITY", "STRICT"])
        & out["visual_bucket"].isin(["CLEAN", "MIXED"])
        & out["qt_bucket"].isin(["QT_SUPPORT", "QT_NEUTRAL"])
        & (out["dist_sma"].between(0, 0.12))
        & (out["atr14_pct"] <= 0.10)
    )
    return out


def group_rows(df: pd.DataFrame, by: list[str], min_n: int = 20) -> list[dict[str, Any]]:
    rows = []
    for key, group in df.groupby(by, dropna=False):
        stats = summarize(group)
        if stats["n"] < min_n:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        rows.append({col: val for col, val in zip(by, key)} | stats)
    return rows


def infer_current_prime_tier(item: dict[str, Any]) -> str:
    return str(item.get("prime_tier") or prime_tier(
        action_label=str(item.get("label") or "NO_TRADE"),
        score=int(item.get("score") or 0),
        setup=str(item.get("setup") or ""),
        warnings=list(item.get("warnings") or []),
        blocks=list(item.get("blocks") or []),
        visual={"grade": item.get("visual_grade") or item.get("latest_visual_grade")},
        qt={
            "label": item.get("qt_label") or item.get("latest_qt_label"),
            "phase": item.get("qt_phase") or item.get("latest_qt_phase"),
            "wait_score": item.get("qt_wait_score") or item.get("latest_qt_wait_score"),
        },
        hist=dict(item.get("hist") or {}),
    ))


def main() -> int:
    payload = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    events = pd.DataFrame(payload.get("events", []))
    if events.empty:
        OUT.write_text("No MLPB events available.\n", encoding="utf-8")
        return 1

    events = bucketize(events)
    focus = events[events["setup"].isin(["MLPB21", "MLPB50"]) & events["variant"].isin(["QUALITY", "STRICT"])].copy()
    strict = events[events["variant"].eq("STRICT") & events["setup"].isin(["MLPB21", "MLPB50"])].copy()
    current_payload = json.loads(GATE_PATH.read_text(encoding="utf-8")) if GATE_PATH.exists() else {"candidates": []}
    current = current_payload.get("candidates", [])

    hard_backtest = []
    for name, subset in {
        "MLPB50 STRICT": strict[(strict["setup"] == "MLPB50")],
        "MLPB21 STRICT": strict[(strict["setup"] == "MLPB21")],
        "Prime pass": events[events["prime_pass"]],
        "Prime watch": events[events["prime_watch"]],
        "QT support": focus[focus["qt_bucket"] == "QT_SUPPORT"],
        "Visual clean": focus[focus["visual_bucket"] == "CLEAN"],
        "Too extended >12%": focus[focus["dist_sma"] > 0.12],
        "High ATR >8%": focus[focus["atr14_pct"] > 0.08],
    }.items():
        hard_backtest.append({"filter": name} | summarize(subset))

    visual_rows = group_rows(focus, ["setup", "variant", "visual_bucket"], min_n=30)
    qt_rows = group_rows(focus, ["setup", "variant", "qt_bucket"], min_n=30)
    qt_phase_rows = group_rows(focus, ["setup", "variant", "qt_phase_bucket"], min_n=30)
    qt_wait_rows = group_rows(focus, ["setup", "variant", "qt_wait_bucket"], min_n=30)
    theme_rows = group_rows(focus, ["group", "setup"], min_n=25)
    theme_rows = sorted(theme_rows, key=lambda row: (row["nextopen21"], row["n"]), reverse=True)[:35]
    universe_rows = (
        focus.groupby("group")
        .agg(
            tickers=("ticker", "nunique"),
            events=("ticker", "size"),
            strict_events=("variant", lambda s: int((s == "STRICT").sum())),
            nextopen21=("nextopen_fwd21", "mean"),
            hit21=("nextopen_fwd21", lambda s: float((s > 0).mean())),
            visual_clean=("visual_bucket", lambda s: float((s == "CLEAN").mean())),
            qt_support=("qt_bucket", lambda s: float((s == "QT_SUPPORT").mean())),
        )
        .reset_index()
        .sort_values(["nextopen21", "events"], ascending=[False, False])
    )

    current_rows = []
    for item in sorted(current, key=lambda row: (infer_current_prime_tier(row), -(row.get("score") or 0), row.get("ticker", ""))):
        current_rows.append({
            "ticker": item.get("ticker"),
            "date": item.get("date"),
            "setup": item.get("setup"),
            "variant": item.get("variant"),
            "score": item.get("score"),
            "label": item.get("label"),
            "prime_tier": infer_current_prime_tier(item),
            "visual": item.get("latest_visual_grade") or item.get("visual_grade"),
            "visual_score": item.get("latest_visual_score") or item.get("visual_score"),
            "qt": item.get("latest_qt_label") or item.get("qt_label"),
            "qt_score": item.get("latest_qt_score") or item.get("qt_score"),
            "qt_phase": item.get("latest_qt_phase") or item.get("qt_phase"),
            "qt_wait": item.get("latest_qt_wait_label") or item.get("qt_wait_label"),
            "qt_confirmation": item.get("latest_qt_confirmation") or item.get("qt_confirmation"),
            "post_entry": item.get("latest_qt_post_entry_state") or item.get("qt_post_entry_state"),
        })

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_count": payload.get("universe_count"),
        "used_count": payload.get("used_count"),
        "events_count": payload.get("events_count"),
        "hard_backtest": hard_backtest,
        "visual": visual_rows,
        "qt": qt_rows,
        "qt_phase": qt_phase_rows,
        "qt_wait": qt_wait_rows,
        "themes": theme_rows,
        "universe_groups": universe_rows.to_dict(orient="records"),
        "current": current_rows,
    }
    JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    lines.append("# MLPB Prime framework research")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Universe: {report['universe_count']} candidates; usable histories: {report['used_count']}; events: {report['events_count']}")
    lines.append("")
    lines.append("## 1. Harder MLPB backtest")
    lines.append("")
    lines.append(summary_table(hard_backtest, "n").to_string(index=False))
    lines.append("")
    lines.append("## 2. Visual quality filter")
    lines.append("")
    lines.append(summary_table(visual_rows, "n").head(30).to_string(index=False))
    lines.append("")
    lines.append("## 3. QT Prime Lite filter")
    lines.append("")
    lines.append(summary_table(qt_rows, "n").head(30).to_string(index=False))
    lines.append("")
    lines.append("### QT phase")
    lines.append("")
    lines.append(summary_table(qt_phase_rows, "n").head(30).to_string(index=False))
    lines.append("")
    lines.append("### Value of waiting")
    lines.append("")
    lines.append(summary_table(qt_wait_rows, "n").head(30).to_string(index=False))
    lines.append("")
    lines.append("## 4. Theme backtest")
    lines.append("")
    lines.append(summary_table(theme_rows, "nextopen21").to_string(index=False))
    lines.append("")
    lines.append("## 5. Current tiering")
    lines.append("")
    lines.append(pd.DataFrame(current_rows).to_string(index=False))
    lines.append("")
    lines.append("## Framework decision")
    lines.append("")
    lines.append("- A_PLUS_TRADE_CANDIDATE requires MLPB50 STRICT, clean visual structure, QT support, controlled extension/ATR, and tolerable historical path risk.")
    lines.append("- A_WATCH means statistically interesting, but still needs a better live setup or manual checks.")
    lines.append("- B_WATCH is information only; do not force capital.")
    lines.append("- EVENT_BLOCKED is mainly earnings/news proximity.")
    lines.append("- FAILED_STRUCTURE means the signal exists, but the chart/extension/path does not fit our trade standard.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Wrote {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
