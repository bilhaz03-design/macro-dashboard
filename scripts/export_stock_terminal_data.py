#!/usr/bin/env python3
"""Export stock scanner research into a small browser-consumable JS payload."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROBUSTNESS_PATH = ROOT / "data" / "stock_framework_robustness_summary.json"
WALKFORWARD_PATH = ROOT / "data" / "stock_framework_walkforward_summary.json"
CURRENT_PATH = ROOT / "data" / "stock_framework_deep_current_signals.json"
OUTPUT_PATH = ROOT / "dashboard" / "stock_data.js"
STOCK_JOURNAL_PATH = ROOT / "data" / "stock-signal-journal.json"

LABEL_RANK = {
    "ROBUST_95_CANDIDATE": 0,
    "ROBUST_BUT_RECENT_SPARSE": 1,
    "WATCHLIST_PLUS": 2,
    "RESEARCH_ONLY": 3,
    "BLOCK_PATH": 4,
}


def safe_round(value, digits=6):
    if value is None:
        return None
    try:
        x = float(value)
        if not math.isfinite(x):
            return None
        return round(x, digits)
    except (TypeError, ValueError):
        return None


def stat(src, key):
    if not isinstance(src, dict):
        return None
    return safe_round(src.get(key))


def combo_tier(label):
    if label == "ROBUST_95_CANDIDATE":
        return "TIER1"
    if label == "ROBUST_BUT_RECENT_SPARSE":
        return "TIER1_WARNING"
    if label == "WATCHLIST_PLUS":
        return "NO_TRADE"
    if label == "BLOCK_PATH":
        return "BLOCKED"
    return "NO_TRADE"


def tier_rank(tier):
    return {
        "TIER1": 0,
        "TIER1_WARNING": 1,
        "NO_TRADE": 2,
        "WATCHLIST_PLUS": 2,
        "RESEARCH_ONLY": 2,
        "BLOCKED": 4,
        "UNTESTED": 5,
    }.get(tier, 9)


def action_rank(action):
    return {
        "TRADE": 0,
        "LIVE_REVIEW": 0,
        "NO_TRADE": 1,
        "WAIT_95_SIGNAL": 1,
        "WAIT_SIGNAL": 1,
        "PAPER_TRACK": 1,
        "RESEARCH_ONLY": 1,
        "BLOCKED": 1,
    }.get(action, 9)


def combo_quality(row):
    tier = row.get("tier")
    if tier == "BLOCKED":
        return 0, "Blocked", ["path-risk blocker"]

    score = {
        "TIER1": 42,
        "TIER1_WARNING": 36,
        "NO_TRADE": 12,
        "WATCHLIST_PLUS": 12,
        "RESEARCH_ONLY": 12,
        "UNTESTED": 0,
    }.get(tier, 0)
    blockers = []

    n = row.get("n") or 0
    mean = row.get("fwd21_mean")
    lift = row.get("lift")
    hit = row.get("hit")
    ci_low = row.get("ci_low")
    mae = row.get("mae_p10")
    shake20 = row.get("shake20")
    q = row.get("q")
    recent_n = row.get("recent_n") or 0
    recent_mean = row.get("recent_mean")
    recent2023_n = row.get("recent2023_n") or 0
    recent2023_mean = row.get("recent2023_mean")

    score += 5 if n >= 80 else 4 if n >= 40 else 2 if n >= 30 else 0
    score += 10 if mean is not None and mean >= 0.04 else 8 if mean is not None and mean >= 0.03 else 5 if mean is not None and mean >= 0.025 else 0
    score += 10 if lift is not None and lift >= 0.03 else 8 if lift is not None and lift >= 0.02 else 5 if lift is not None and lift >= 0.0125 else 0
    score += 8 if hit is not None and hit >= 0.68 else 6 if hit is not None and hit >= 0.60 else 0
    score += 6 if ci_low is not None and ci_low > 0.02 else 4 if ci_low is not None and ci_low > 0 else 0
    score += 6 if q is not None and q <= 0.05 else 4 if q is not None and q <= 0.10 else 2 if q is not None and q <= 0.20 else 0
    score += 7 if mae is not None and mae >= -0.12 else 5 if mae is not None and mae >= -0.18 else 1 if mae is not None and mae >= -0.22 else 0
    score += 5 if shake20 is not None and shake20 <= 0.03 else 3 if shake20 is not None and shake20 <= 0.10 else 0
    score += 4 if recent_n >= 5 and recent_mean is not None and recent_mean > 0 else 0
    score += 4 if recent2023_n >= 5 and recent2023_mean is not None and recent2023_mean > 0 else 0

    if n < 40:
        blockers.append("N<40")
    if mean is None or mean < 0.03:
        blockers.append("edge<3%")
    if lift is None or lift < 0.0125:
        blockers.append("lift weak")
    if hit is None or hit < 0.60:
        blockers.append("hit<60%")
    if ci_low is None or ci_low <= 0:
        blockers.append("CI<=0")
    if q is None or q > 0.10:
        blockers.append("q>0.10")
    if mae is not None and mae < -0.18:
        blockers.append("MAE p10")
    if recent2023_n < 5 or recent2023_mean is None or recent2023_mean <= 0:
        blockers.append("2023+ weak/sparse")

    score = max(0, min(99, int(round(score))))
    if score >= 95:
        label = "95"
    elif score >= 90:
        label = "A"
    elif score >= 80:
        label = "B"
    elif score >= 70:
        label = "C"
    else:
        label = "No trade"
    return score, label, blockers[:5]


def simplify_combo(row):
    s = row.get("summary", {})
    recent = row.get("recent", {})
    recent2023 = row.get("recent2023", {})
    raw_label = row.get("label", "NO_TRADE")
    label = {
        "RESEARCH_ONLY": "NO_TRADE",
        "WATCHLIST_PLUS": "NO_TRADE",
        "BLOCK_PATH": "BLOCKED",
    }.get(raw_label, raw_label)
    out = {
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "market": row.get("market"),
        "group": row.get("group"),
        "signal": row.get("signal"),
        "label": label,
        "tier": combo_tier(raw_label),
        "n": s.get("n", 0),
        "fwd21_mean": stat(s, "mean"),
        "fwd21_median": stat(s, "median"),
        "hit": stat(s, "hit"),
        "ci_low": stat(s, "ci_low"),
        "mae_p10": stat(s, "mae_p10"),
        "shake20": stat(s, "shake20"),
        "lift": safe_round(row.get("lift")),
        "q": safe_round(row.get("q")),
        "rand_p": safe_round(row.get("rand_p")),
        "recent_n": recent.get("n", 0),
        "recent_mean": stat(recent, "mean"),
        "recent2023_n": recent2023.get("n", 0),
        "recent2023_mean": stat(recent2023, "mean"),
    }
    score, quality, blockers = combo_quality(out)
    out["quality_score"] = score
    out["quality"] = quality
    out["quality_blockers"] = blockers
    return out


def current_key(row):
    return (row.get("date"), row.get("ticker"), row.get("signal"))


def update_stock_signal_journal(current_rows, path=STOCK_JOURNAL_PATH):
    now = datetime.now().isoformat(timespec="seconds")
    existing = {"version": 1, "signals": []}
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
    dates = sorted({row.get("date") for row in current_rows if row.get("date")})
    scan_date = dates[-1] if dates else None
    active_keys = set()
    new_this_run = []
    faded_this_run = []

    for row in current_rows:
        if row.get("date") != scan_date:
            continue
        key = f"{row.get('date')}|{row.get('ticker')}|{row.get('signal')}"
        active_keys.add(key)
        item = entries.get(key)
        if item is None:
            item = {
                "key": key,
                "date": row.get("date"),
                "ticker": row.get("ticker"),
                "name": row.get("name"),
                "market": row.get("market"),
                "group": row.get("group"),
                "signal": row.get("signal"),
                "first_seen_at": now,
                "seen_count": 0,
            }
            entries[key] = item
            new_this_run.append(item)
        item.update({
            "last_seen_at": now,
            "last_checked_at": now,
            "active": True,
            "status": "ACTIVE_NOW",
            "action": row.get("action"),
            "entry": row.get("entry"),
            "quality_score": row.get("quality_score"),
            "tier": row.get("tier"),
            "gates": row.get("gates"),
        })
        item["seen_count"] = int(item.get("seen_count", 0) or 0) + 1

    if scan_date:
        for key, item in entries.items():
            if item.get("date") != scan_date or key in active_keys:
                continue
            was_active = item.get("active") is True or item.get("status") == "ACTIVE_NOW"
            item["active"] = False
            item["last_checked_at"] = now
            if item.get("status") != "FADED_INTRADAY":
                item["status"] = "FADED_INTRADAY"
            if was_active:
                faded_this_run.append(item)

    for item in entries.values():
        if item.get("action") not in {"TRADE", "NO_TRADE"}:
            item["action"] = "NO_TRADE"
        if item.get("tier") in {"RESEARCH_ONLY", "WATCHLIST_PLUS"}:
            item["tier"] = "NO_TRADE"

    kept = sorted(entries.values(), key=lambda x: (str(x.get("date")), str(x.get("first_seen_at")), str(x.get("ticker"))))[-500:]
    today = [item for item in kept if item.get("date") == scan_date]
    payload = {
        "version": 1,
        "updated_at": now,
        "scan_date": scan_date,
        "signals": kept,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "updated_at": now,
        "scan_date": scan_date,
        "today": today,
        "new_this_run": new_this_run,
        "faded_this_run": faded_this_run,
        "today_count": len(today),
        "active_today_count": sum(1 for item in today if item.get("active")),
        "faded_today_count": sum(1 for item in today if not item.get("active")),
    }


def stock_blockers(best, current_rows):
    blockers = []
    if not best:
        return ["no tested combo"]
    if best["quality_score"] < 95:
        blockers.append("quality<95")
    if best["tier"] not in {"TIER1", "TIER1_WARNING"}:
        blockers.append("not robust tier")
    if not current_rows:
        blockers.append("wait current signal")
    else:
        has_strict = any(g.get("gate") == "strict" for row in current_rows for g in row.get("gates", []))
        if not has_strict:
            blockers.append("no strict live gate")
        current_best = sorted(current_rows, key=lambda row: action_rank(row["action"]))[0]
        if current_best.get("action") != "TRADE":
            blockers.append("current signal blocked")
    if best.get("mae_p10") is not None and best["mae_p10"] < -0.18:
        blockers.append("path risk")
    return blockers[:6]


def stock_action(best, current_rows):
    return "TRADE" if not stock_blockers(best, current_rows) else "NO_TRADE"


def current_signal_blockers(combo, gates):
    blockers = []
    if not combo:
        return ["untested combo"]
    if combo["tier"] == "BLOCKED":
        return ["path-risk blocked"]
    if combo["quality_score"] < 95:
        blockers.append("quality<95")
    if combo["tier"] not in {"TIER1", "TIER1_WARNING"}:
        blockers.append("not robust tier")
    if not any(g.get("gate") == "strict" for g in gates):
        blockers.append("no strict live gate")
    if combo.get("mae_p10") is not None and combo["mae_p10"] < -0.18:
        blockers.append("path risk")
    return blockers[:6]


def current_signal_action(combo, gates):
    return "TRADE" if not current_signal_blockers(combo, gates) else "NO_TRADE"


def main() -> int:
    robustness = json.loads(ROBUSTNESS_PATH.read_text())
    walkforward = json.loads(WALKFORWARD_PATH.read_text())
    current_raw = json.loads(CURRENT_PATH.read_text())

    combos = [simplify_combo(row) for row in robustness]
    combos.sort(key=lambda row: (
        LABEL_RANK.get(row["label"], 9),
        -(row.get("lift") or -99),
        -(row.get("fwd21_mean") or -99),
        row.get("ticker") or "",
    ))

    combo_by_key = {(row["ticker"], row["signal"]): row for row in combos}
    current_gate = walkforward.get("current_gate", [])
    gate_by_key = {}
    for row in current_gate:
        gate_by_key.setdefault(current_key(row), []).append({
            "gate": row.get("gate"),
            "robust_key": bool(row.get("robust_key")),
            "train_n": row.get("train", {}).get("n", 0),
            "train_mean": stat(row.get("train", {}), "mean"),
            "train_hit": stat(row.get("train", {}), "hit"),
            "train_mae_p10": stat(row.get("train", {}), "mae_p10"),
        })

    current = []
    seen = set()
    for raw in sorted(current_raw, key=lambda row: (row.get("date", ""), row.get("ticker", ""), row.get("signal", "")), reverse=True):
        key = current_key(raw)
        if key in seen:
            continue
        seen.add(key)
        combo = combo_by_key.get((raw.get("ticker"), raw.get("signal")))
        gates = gate_by_key.get(key, [])
        signal_blockers = current_signal_blockers(combo, gates)
        action = current_signal_action(combo, gates)
        current.append({
            "date": raw.get("date"),
            "ticker": raw.get("ticker"),
            "name": raw.get("name"),
            "market": raw.get("market"),
            "group": raw.get("group"),
            "signal": raw.get("signal"),
            "entry": safe_round(raw.get("entry"), 4),
            "ri_rsi": safe_round(raw.get("ri_rsi"), 4),
            "ri_dirrvol": safe_round(raw.get("ri_dirrvol"), 4),
            "gap_p95_252": safe_round(raw.get("gap_p95_252"), 4),
            "vol63_ann": safe_round(raw.get("vol63_ann"), 4),
            "tier": combo["tier"] if combo else "UNTESTED",
            "label": combo["label"] if combo else "UNTESTED",
            "quality_score": combo["quality_score"] if combo else 0,
            "quality": combo["quality"] if combo else "Untested",
            "quality_blockers": combo["quality_blockers"] if combo else ["untested combo"],
            "blockers": signal_blockers,
            "combo": combo,
            "gates": gates,
            "action": action,
        })

    signal_memory = update_stock_signal_journal(current)

    combos_by_ticker = defaultdict(list)
    for combo in combos:
        combos_by_ticker[combo["ticker"]].append(combo)
    current_by_ticker = defaultdict(list)
    for row in current:
        current_by_ticker[row["ticker"]].append(row)

    stocks = []
    for ticker in sorted(set(combos_by_ticker) | set(current_by_ticker)):
        stock_combos = combos_by_ticker.get(ticker, [])
        best = sorted(
            stock_combos,
            key=lambda row: (
                tier_rank(row["tier"]),
                -row["quality_score"],
                -(row.get("lift") or -99),
                -(row.get("fwd21_mean") or -99),
            ),
        )[0] if stock_combos else None
        current_rows = sorted(current_by_ticker.get(ticker, []), key=lambda row: (action_rank(row["action"]), row.get("date") or ""), reverse=False)
        action = stock_action(best, current_rows)
        blockers = stock_blockers(best, current_rows)
        current_best = current_rows[0] if current_rows else None
        stocks.append({
            "ticker": ticker,
            "name": (best or current_best or {}).get("name"),
            "market": (best or current_best or {}).get("market"),
            "group": (best or current_best or {}).get("group"),
            "action": action,
            "tier": best["tier"] if best else "UNTESTED",
            "quality_score": best["quality_score"] if best else 0,
            "quality": best["quality"] if best else "Untested",
            "best_signal": best["signal"] if best else (current_best or {}).get("signal"),
            "combo_count": len(stock_combos),
            "fwd21_mean": best.get("fwd21_mean") if best else None,
            "lift": best.get("lift") if best else None,
            "hit": best.get("hit") if best else None,
            "mae_p10": best.get("mae_p10") if best else None,
            "q": best.get("q") if best else None,
            "recent2023_n": best.get("recent2023_n") if best else 0,
            "recent2023_mean": best.get("recent2023_mean") if best else None,
            "current_signal": current_best.get("signal") if current_best else None,
            "current_date": current_best.get("date") if current_best else None,
            "current_gate": "+".join(g["gate"] for g in current_best.get("gates", [])) if current_best else "",
            "current_action": current_best.get("action") if current_best else "",
            "blockers": blockers,
        })
    stocks.sort(key=lambda row: (
        action_rank(row["action"]),
        tier_rank(row["tier"]),
        -row["quality_score"],
        -(row.get("lift") or -99),
        row["ticker"],
    ))

    rolling = walkforward.get("rolling", {})
    payload = {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "source": "stock robustness + walk-forward audits",
            "combos": len(combos),
            "stocks": len(stocks),
            "current_raw": len(current),
            "tier1": sum(1 for row in combos if row["tier"] == "TIER1"),
            "tier1_warning": sum(1 for row in combos if row["tier"] == "TIER1_WARNING"),
            "watchlist_plus": sum(1 for row in combos if row["tier"] == "WATCHLIST_PLUS"),
            "blocked": sum(1 for row in combos if row["tier"] == "BLOCKED"),
            "high_quality_stocks": sum(1 for row in stocks if row["quality_score"] >= 95),
            "trade": sum(1 for row in stocks if row["action"] == "TRADE"),
            "no_trade": sum(1 for row in stocks if row["action"] == "NO_TRADE"),
            "current_trade": sum(1 for row in current if row["action"] == "TRADE"),
            "current_no_trade": sum(1 for row in current if row["action"] == "NO_TRADE"),
            "current_live_review": sum(1 for row in current if row["action"] == "TRADE"),
            "current_paper_track": 0,
            "strict_oos_mean": stat(rolling.get("strict", {}), "mean"),
            "strict_oos_hit": stat(rolling.get("strict", {}), "hit"),
            "strict_oos_net_100bps": stat(rolling.get("strict", {}), "net_100bps"),
            "candidate_oos_mean": stat(rolling.get("candidate", {}), "mean"),
            "candidate_oos_net_100bps": stat(rolling.get("candidate", {}), "net_100bps"),
            "signal_memory": signal_memory,
        },
        "stocks": stocks,
        "combos": combos,
        "current": current,
    }

    text = "// Auto-generated by export_stock_terminal_data.py\nwindow.STOCK_DATA = "
    text += json.dumps(payload, ensure_ascii=False, indent=2)
    text += ";\n"
    OUTPUT_PATH.write_text(text)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Combos: {len(combos)} Current: {len(current)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
