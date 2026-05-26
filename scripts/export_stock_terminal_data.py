#!/usr/bin/env python3
"""Export stock scanner research into a small browser-consumable JS payload."""

from __future__ import annotations

import json
import math
import csv
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROBUSTNESS_PATH = ROOT / "data" / "stock_framework_robustness_summary.json"
WALKFORWARD_PATH = ROOT / "data" / "stock_framework_walkforward_summary.json"
CURRENT_PATH = ROOT / "data" / "stock_framework_deep_current_signals.json"
MLPB_GATE_PATH = ROOT / "data" / "mlpb_current_trade_gate.json"
OUTPUT_PATH = ROOT / "dashboard" / "stock_data.js"
STOCK_JOURNAL_PATH = ROOT / "data" / "stock-signal-journal.json"
STOCK_CACHE_DIR = ROOT / "data" / "stock_cache"
MLPB_CACHE_DIR = ROOT / "data" / "mlpb_broad_cache"

_PRICE_HISTORY_CACHE = {}

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
        "WAIT_95_SIGNAL": 1,
        "WAIT_SIGNAL": 1,
        "PAPER_TRACK": 2,
        "NO_TRADE": 3,
        "RESEARCH_ONLY": 3,
        "BLOCKED": 4,
    }.get(action, 9)


def date_rank(date_text):
    try:
        return int(str(date_text or "").replace("-", ""))
    except ValueError:
        return 0


def cache_file_candidates(ticker):
    safe = str(ticker or "").replace("/", "_").replace("^", "")
    candidates = [
        STOCK_CACHE_DIR / f"{safe}.csv",
        MLPB_CACHE_DIR / f"{safe}.csv",
        STOCK_CACHE_DIR / f"{safe}.8y.csv",
        STOCK_CACHE_DIR / f"{safe}.1y.csv",
    ]
    return [path for path in candidates if path.exists()]


def load_price_history(ticker):
    key = str(ticker or "")
    if key in _PRICE_HISTORY_CACHE:
        return _PRICE_HISTORY_CACHE[key]
    rows = []
    for path in cache_file_candidates(key):
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    close = safe_round(row.get("Close"))
                    if close is None:
                        continue
                    rows.append({
                        "date": str(row.get("Date") or "")[:10],
                        "close": close,
                        "high": safe_round(row.get("High")) or close,
                        "low": safe_round(row.get("Low")) or close,
                    })
        except OSError:
            rows = []
        if rows:
            break
    rows = sorted({row["date"]: row for row in rows if row.get("date")}.values(), key=lambda row: row["date"])
    _PRICE_HISTORY_CACHE[key] = rows
    return rows


def signal_outcome(ticker, signal_date, entry):
    history = load_price_history(ticker)
    if not history or not signal_date:
        return {"status": "NO_PRICE_HISTORY"}
    try:
        signal_day = date.fromisoformat(str(signal_date)[:10])
    except ValueError:
        return {"status": "BAD_SIGNAL_DATE"}

    idx = None
    for i, row in enumerate(history):
        try:
            row_day = date.fromisoformat(row["date"])
        except ValueError:
            continue
        if row_day >= signal_day:
            idx = i
            break
    if idx is None:
        return {"status": "SIGNAL_AFTER_HISTORY"}

    entry_price = safe_round(entry) or history[idx]["close"]
    if not entry_price or entry_price <= 0:
        return {"status": "BAD_ENTRY"}

    latest_idx = len(history) - 1
    bars_elapsed = max(0, latest_idx - idx)
    outcome = {
        "status": "OPEN" if bars_elapsed < 21 else "MATURED_21D",
        "entry_date_used": history[idx]["date"],
        "entry_price_used": entry_price,
        "latest_date": history[latest_idx]["date"],
        "bars_elapsed": bars_elapsed,
        "latest_return": safe_round(history[latest_idx]["close"] / entry_price - 1),
    }
    for horizon in (5, 10, 21):
        target_idx = idx + horizon
        if target_idx <= latest_idx:
            window = history[idx + 1 : target_idx + 1]
            highs = [row["high"] for row in window if row.get("high") is not None]
            lows = [row["low"] for row in window if row.get("low") is not None]
            outcome[f"fwd{horizon}"] = safe_round(history[target_idx]["close"] / entry_price - 1)
            outcome[f"mfe{horizon}"] = safe_round(max(highs) / entry_price - 1) if highs else None
            outcome[f"mae{horizon}"] = safe_round(min(lows) / entry_price - 1) if lows else None
        else:
            outcome[f"fwd{horizon}"] = None
            outcome[f"mfe{horizon}"] = None
            outcome[f"mae{horizon}"] = None
    return outcome


def post_entry_monitor(item):
    outcome = item.get("outcome") or {}
    status = outcome.get("status") or "NO_PRICE_HISTORY"
    latest_return = outcome.get("latest_return")
    bars_elapsed = outcome.get("bars_elapsed")
    action = item.get("action") or "NO_TRADE"
    active = item.get("active")
    label = "No post-entry data"
    tone = "neutral"
    note = status

    if status == "OPEN":
        label = "Open monitor"
        if latest_return is not None and latest_return >= 0.05:
            tone = "positive"
            note = f"Open +{latest_return * 100:.1f}% after {bars_elapsed} bars"
        elif latest_return is not None and latest_return <= -0.05:
            tone = "negative"
            note = f"Open {latest_return * 100:.1f}% after {bars_elapsed} bars"
        else:
            tone = "watch"
            note = f"Open, {bars_elapsed} bars elapsed"
    elif status == "MATURED_21D":
        fwd21 = outcome.get("fwd21")
        label = "21D matured"
        if fwd21 is not None and fwd21 > 0:
            tone = "positive"
            note = f"21D +{fwd21 * 100:.1f}%"
        elif fwd21 is not None:
            tone = "negative"
            note = f"21D {fwd21 * 100:.1f}%"
        else:
            note = "21D matured, no fwd21 close"
    elif active is False:
        label = "Faded monitor"
        tone = "negative" if action in {"TRADE", "LIVE_REVIEW", "WAIT_95_SIGNAL", "WAIT_SIGNAL"} else "neutral"
        note = "Signal faded after being tracked"

    return {
        "status": status,
        "label": label,
        "tone": tone,
        "note": note,
        "bars_elapsed": bars_elapsed,
        "latest_return": latest_return,
        "fwd5": outcome.get("fwd5"),
        "fwd10": outcome.get("fwd10"),
        "fwd21": outcome.get("fwd21"),
        "mae21": outcome.get("mae21"),
        "mfe21": outcome.get("mfe21"),
    }


def governance_for_signal(row):
    action = row.get("action") or "NO_TRADE"
    blockers = list(row.get("blockers") or row.get("quality_blockers") or [])
    quality = int(row.get("quality_score") or 0)
    tier = row.get("tier") or "UNTESTED"
    gates = [gate.get("gate") for gate in row.get("gates", []) if isinstance(gate, dict) and gate.get("gate")]
    prime = row.get("prime_tier")
    parts = []
    next_steps = []

    if prime:
        parts.append(f"Prime {prime}")
    if quality:
        parts.append(f"Q{quality}")
    if tier:
        parts.append(str(tier))
    if gates:
        parts.append("+".join(gates))

    if action == "TRADE":
        status = "TRADE_READY"
        reason = "Strict live gate plus robust evidence."
    elif action == "LIVE_REVIEW":
        status = "MANUAL_REVIEW"
        reason = "Quant gate passed; broker/news checks still decide."
        next_steps.extend(["Check spread/depth", "Check major news/event risk"])
    elif action == "WAIT_95_SIGNAL":
        status = "NEAR_MISS"
        reason = "Strong signal, but one confirmation layer is still missing."
    elif action == "WAIT_SIGNAL":
        status = "ALIVE_WAIT"
        reason = "Signal is alive; not enough evidence for capital yet."
    elif action == "PAPER_TRACK":
        status = "OBSERVE_ONLY"
        reason = "Useful information, but not an actionable setup yet."
    elif action == "BLOCKED":
        status = "HARD_BLOCKED"
        reason = "A hard blocker overrides signal interest."
    else:
        status = "NO_TRADE"
        reason = "No active trade path."

    blocker_text = " · ".join(blockers[:3])
    if blocker_text and action in {"WAIT_95_SIGNAL", "WAIT_SIGNAL", "PAPER_TRACK", "NO_TRADE", "BLOCKED"}:
        reason = f"{reason} Main gap: {blocker_text}."

    blocker_steps = {
        "quality<95": "Needs Q >= 95 or a stronger exact setup",
        "not robust tier": "Needs robust tier or more live evidence",
        "no strict live gate": "Needs strict/EOD confirmation",
        "path risk": "Needs cleaner path or smaller size",
        "severe path risk": "Wait for new base; path risk is too high",
        "untested combo": "Needs historical combo validation",
        "path-risk blocked": "Blocked until path risk improves",
    }
    for blocker in blockers:
        step = blocker_steps.get(blocker)
        if step and step not in next_steps:
            next_steps.append(step)
    if not next_steps and action in {"WAIT_SIGNAL", "PAPER_TRACK"}:
        next_steps.append("Needs stronger follow-through before capital")
    if not next_steps and action == "TRADE":
        next_steps.append("Manual execution check only")

    return {
        "status": status,
        "reason": reason,
        "evidence": " · ".join(parts[:5]) or "No evidence stack",
        "next": next_steps[:4],
        "hard_blockers": blockers[:4] if action == "BLOCKED" else [],
    }


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def trade_thesis_for_signal(row):
    action = row.get("action") or "NO_TRADE"
    governance = row.get("governance") or governance_for_signal(row)
    quality = int(row.get("quality_score") or 0)
    tier = row.get("tier") or "UNTESTED"
    blockers = list(row.get("blockers") or row.get("quality_blockers") or [])
    gates = [gate.get("gate") for gate in row.get("gates", []) if isinstance(gate, dict) and gate.get("gate")]
    prime = row.get("prime_tier")
    qt_phase = row.get("qt_phase")
    qt_wait = row.get("qt_wait_label")
    qt_label = row.get("qt_label")
    group = row.get("group") or row.get("market") or ""
    signal = row.get("signal") or row.get("best_signal") or ""

    base = {
        "TRADE": 72,
        "LIVE_REVIEW": 70,
        "WAIT_95_SIGNAL": 66,
        "WAIT_SIGNAL": 60,
        "PAPER_TRACK": 54,
        "NO_TRADE": 48,
        "BLOCKED": 42,
    }.get(action, 50)
    prob = base
    if quality:
        prob += clamp((quality - 70) // 6, -5, 5)
    if tier == "TIER1":
        prob += 4
    elif tier == "TIER1_WARNING":
        prob += 2
    elif tier == "UNTESTED":
        prob -= 4
    elif tier == "BLOCKED":
        prob -= 10
    if prime == "A_PLUS_TRADE_CANDIDATE":
        prob += 5
    elif prime == "A_WATCH":
        prob += 3
    elif prime == "B_WATCH":
        prob += 1
    elif prime == "EVENT_BLOCKED":
        prob -= 8
    if qt_phase in {"REPAIRING", "SUPPORTED_PULLBACK"}:
        prob += 3
    elif qt_phase in {"EARLY_FALLING", "FADING", "CHASING"}:
        prob -= 4
    if qt_wait == "HIGH_WAIT_VALUE":
        prob -= 3
    prob -= min(len(blockers), 4) * 2
    prob = int(clamp(prob, 35, 78))

    bull = []
    bear = []
    if action in {"TRADE", "LIVE_REVIEW", "WAIT_95_SIGNAL", "WAIT_SIGNAL"}:
        bull.append("Signal is alive in the current scan.")
    if quality >= 90:
        bull.append(f"High-quality setup context (Q{quality}).")
    elif quality >= 65:
        bull.append(f"Enough structure to keep watching (Q{quality}).")
    if tier in {"TIER1", "TIER1_WARNING"}:
        bull.append(f"Historical combo is {tier}.")
    if prime in {"A_PLUS_TRADE_CANDIDATE", "A_WATCH", "B_WATCH"}:
        bull.append(f"MLPB Prime stack is {prime}.")
    if qt_phase in {"REPAIRING", "SUPPORTED_PULLBACK"}:
        bull.append(f"QT phase is constructive: {qt_phase}.")
    if row.get("fwd21_mean") is not None:
        bull.append(f"Historical fwd21 mean {row.get('fwd21_mean') * 100:+.1f}%.")

    if blockers:
        bear.append("Main friction: " + " · ".join(blockers[:3]) + ".")
    if action == "BLOCKED":
        bear.append("A hard risk blocks capital for now.")
    if tier in {"UNTESTED", "NO_TRADE"}:
        bear.append("The statistical background is not strong enough by itself.")
    if qt_phase in {"EARLY_FALLING", "FADING", "CHASING"}:
        bear.append(f"QT phase warns about timing: {qt_phase}.")
    if qt_wait == "HIGH_WAIT_VALUE":
        bear.append("Waiting may have higher value than immediate entry.")
    if not bull:
        bull.append("Worth observing only if the chart/narrative is visually compelling.")
    if not bear:
        bear.append("Main risk is false precision: good setup still needs market confirmation.")

    wait_for = list(governance.get("next") or [])
    if not wait_for:
        wait_for = ["Clearer price confirmation", "Cleaner execution context"]
    buy_if = []
    if action in {"TRADE", "LIVE_REVIEW"}:
        buy_if.append("Manual spread/news checks are clean.")
    elif action == "WAIT_95_SIGNAL":
        buy_if.append("The missing confirmation layer appears without chasing price.")
    elif action == "WAIT_SIGNAL":
        buy_if.append("Price confirms and the signal stays alive into close.")
    elif action == "PAPER_TRACK":
        buy_if.append("It upgrades from observation to a live setup.")
    else:
        buy_if.append("The blocker disappears and the chart still looks asymmetric.")

    return {
        "stance": {
            "TRADE": "Trade-ready if manual execution checks pass",
            "LIVE_REVIEW": "Manual review, not automatic buy",
            "WAIT_95_SIGNAL": "Strong near-miss",
            "WAIT_SIGNAL": "Alive, but wait for confirmation",
            "PAPER_TRACK": "Observe only",
            "BLOCKED": "Blocked for now",
            "NO_TRADE": "No trade path yet",
        }.get(action, "Open question"),
        "subjective_probability": prob,
        "probability_note": "subjective setup probability, not statistical certainty",
        "bull_case": bull[:4],
        "bear_case": bear[:4],
        "chart_read": f"{signal} in {group}".strip(),
        "quant_context": governance.get("evidence") or "No strong quant context",
        "feel": "Interesting but not urgent" if action in {"WAIT_SIGNAL", "PAPER_TRACK"} else "Needs direct attention" if action in {"TRADE", "LIVE_REVIEW", "WAIT_95_SIGNAL"} else "Respect the blocker",
        "wait_for": wait_for[:4],
        "buy_if": buy_if[:3],
    }


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


def current_key_text(row):
    return f"{row.get('date')}|{row.get('ticker')}|{row.get('signal')}"


def probability_for(row):
    thesis = row.get("thesis") or {}
    value = thesis.get("subjective_probability")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lifecycle_summary(item):
    state = item.get("lifecycle_state")
    if not state:
        state = "ACTIVE" if item.get("active") else "FADED"
    labels = {
        "NEW": "New thesis",
        "RETURNED": "Returned",
        "UPGRADED": "Improving",
        "DOWNGRADED": "Weakening",
        "PERSISTING": "Still alive",
        "ACTIVE": "Active",
        "FADED": "Faded",
        "ARCHIVE": "Historical",
    }
    return {
        "state": state,
        "label": labels.get(state, state.title()),
        "first_seen_at": item.get("first_seen_at"),
        "last_seen_at": item.get("last_seen_at"),
        "seen_count": int(item.get("seen_count", 0) or 0),
        "active": bool(item.get("active")),
        "previous_action": item.get("previous_action"),
        "action_change": item.get("action_change"),
        "probability_delta": item.get("probability_delta"),
    }


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
    returned_this_run = []
    upgraded_this_run = []
    downgraded_this_run = []
    faded_this_run = []

    for row in current_rows:
        if row.get("date") != scan_date:
            continue
        key = current_key_text(row)
        active_keys.add(key)
        item = entries.get(key)
        old_active = bool(item.get("active")) if item else False
        old_action = item.get("action") if item else None
        old_probability = probability_for(item or {})
        new_probability = probability_for(row)
        if item is None:
            same_signal_seen_before = any(
                old.get("ticker") == row.get("ticker")
                and old.get("signal") == row.get("signal")
                and old.get("date") != row.get("date")
                for old in entries.values()
            )
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
            item["lifecycle_state"] = "RETURNED" if same_signal_seen_before else "NEW"
            if same_signal_seen_before:
                returned_this_run.append(item)
            else:
                new_this_run.append(item)
        elif not old_active:
            item["lifecycle_state"] = "RETURNED"
            returned_this_run.append(item)
        else:
            old_rank = action_rank(old_action)
            new_rank = action_rank(row.get("action"))
            prob_delta = None
            if old_probability is not None and new_probability is not None:
                prob_delta = new_probability - old_probability
            item["probability_delta"] = prob_delta
            if new_rank < old_rank or (prob_delta is not None and prob_delta >= 5):
                item["lifecycle_state"] = "UPGRADED"
                upgraded_this_run.append(item)
            elif new_rank > old_rank or (prob_delta is not None and prob_delta <= -5):
                item["lifecycle_state"] = "DOWNGRADED"
                downgraded_this_run.append(item)
            else:
                item["lifecycle_state"] = "PERSISTING"
        item["previous_action"] = old_action
        item["action_change"] = f"{old_action}->{row.get('action')}" if old_action and old_action != row.get("action") else None
        item.update({
            "last_seen_at": now,
            "last_checked_at": now,
            "active": True,
            "status": "ACTIVE_NOW",
            "action": row.get("action"),
            "entry": row.get("entry"),
            "quality_score": row.get("quality_score"),
            "tier": row.get("tier"),
            "prime_tier": row.get("prime_tier"),
            "visual_grade": row.get("visual_grade"),
            "qt_label": row.get("qt_label"),
            "qt_phase": row.get("qt_phase"),
            "qt_wait_label": row.get("qt_wait_label"),
            "qt_confirmation": row.get("qt_confirmation"),
            "qt_post_entry_state": row.get("qt_post_entry_state"),
            "gates": row.get("gates"),
            "current_gate": row.get("current_gate"),
            "earnings_date": row.get("earnings_date"),
            "governance": row.get("governance"),
            "thesis": row.get("thesis"),
        })
        item["seen_count"] = int(item.get("seen_count", 0) or 0) + 1
        item["lifecycle"] = lifecycle_summary(item)

    if scan_date:
        for key, item in entries.items():
            if item.get("date") != scan_date or key in active_keys:
                continue
            was_active = item.get("active") is True or item.get("status") == "ACTIVE_NOW"
            item["active"] = False
            item["last_checked_at"] = now
            if item.get("status") != "FADED_INTRADAY":
                item["status"] = "FADED_INTRADAY"
            item["lifecycle_state"] = "FADED"
            item["lifecycle"] = lifecycle_summary(item)
            if was_active:
                faded_this_run.append(item)

    for item in entries.values():
        if item.get("action") not in {"TRADE", "LIVE_REVIEW", "WAIT_SIGNAL", "WAIT_95_SIGNAL", "PAPER_TRACK", "BLOCKED", "NO_TRADE"}:
            item["action"] = "NO_TRADE"
        if item.get("tier") in {"RESEARCH_ONLY", "WATCHLIST_PLUS"}:
            item["tier"] = "NO_TRADE"
        if not item.get("governance"):
            item["governance"] = governance_for_signal(item)
        if not item.get("thesis"):
            item["thesis"] = trade_thesis_for_signal(item)
        item["outcome"] = signal_outcome(item.get("ticker"), item.get("date"), item.get("entry"))
        item["post_entry_monitor"] = post_entry_monitor(item)
        item["lifecycle"] = lifecycle_summary(item)

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
        "returned_this_run": returned_this_run,
        "upgraded_this_run": upgraded_this_run,
        "downgraded_this_run": downgraded_this_run,
        "faded_this_run": faded_this_run,
        "today_count": len(today),
        "active_today_count": sum(1 for item in today if item.get("active")),
        "faded_today_count": sum(1 for item in today if not item.get("active")),
        "new_count": sum(1 for item in today if (item.get("lifecycle") or {}).get("state") == "NEW"),
        "returned_count": sum(1 for item in today if (item.get("lifecycle") or {}).get("state") == "RETURNED"),
        "upgraded_count": sum(1 for item in today if (item.get("lifecycle") or {}).get("state") == "UPGRADED"),
        "downgraded_count": sum(1 for item in today if (item.get("lifecycle") or {}).get("state") == "DOWNGRADED"),
        "matured_21d_count": sum(1 for item in kept if (item.get("outcome") or {}).get("status") == "MATURED_21D"),
        "tracked_count": sum(1 for item in kept if (item.get("outcome") or {}).get("status") in {"OPEN", "MATURED_21D"}),
    }


def stock_blockers(best, current_rows):
    blockers = []
    current_best = sorted(
        current_rows,
        key=lambda row: (action_rank(row.get("action")), -date_rank(row.get("date"))),
    )[0] if current_rows else None
    if current_best and current_best.get("framework") == "MLPB":
        return (current_best.get("blockers") or [])[:6]
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
    current_best = sorted(
        current_rows,
        key=lambda row: (action_rank(row.get("action")), -date_rank(row.get("date"))),
    )[0] if current_rows else None
    if current_best and current_best.get("framework") == "MLPB":
        return current_best.get("action") or "NO_TRADE"
    if current_best:
        current_action = current_best.get("action") or "NO_TRADE"
        if current_action == "TRADE":
            return "TRADE" if not stock_blockers(best, current_rows) else "WAIT_95_SIGNAL"
        if current_action in {"WAIT_95_SIGNAL", "WAIT_SIGNAL", "PAPER_TRACK", "BLOCKED"}:
            return current_action
    return "NO_TRADE"


def current_signal_blockers(combo, gates):
    blockers = []
    if not combo:
        return ["untested combo"]
    if combo["tier"] == "BLOCKED":
        return ["path-risk blocked"]
    if combo.get("mae_p10") is not None and combo["mae_p10"] < -0.24:
        blockers.append("severe path risk")
    if combo["quality_score"] < 95:
        blockers.append("quality<95")
    if combo["tier"] not in {"TIER1", "TIER1_WARNING"}:
        blockers.append("not robust tier")
    if not any(g.get("gate") == "strict" for g in gates):
        blockers.append("no strict live gate")
    if combo.get("mae_p10") is not None and combo["mae_p10"] < -0.18:
        blockers.append("path risk")
    return blockers[:6]


def current_signal_action_level(combo, gates):
    if not combo:
        return "PAPER_TRACK"
    if combo["tier"] == "BLOCKED":
        return "BLOCKED"
    if combo.get("mae_p10") is not None and combo["mae_p10"] < -0.24:
        return "BLOCKED"

    quality = int(combo.get("quality_score") or 0)
    robust = combo.get("tier") in {"TIER1", "TIER1_WARNING"}
    has_gate = bool(gates)
    has_strict = any(g.get("gate") == "strict" for g in gates)
    path_ok = combo.get("mae_p10") is None or combo["mae_p10"] >= -0.18

    if quality >= 95 and robust and has_strict and path_ok:
        return "TRADE"
    if quality >= 88 and robust and has_gate and path_ok:
        return "WAIT_95_SIGNAL"
    if has_gate and (quality >= 65 or robust):
        return "WAIT_SIGNAL"
    return "PAPER_TRACK"


def current_signal_action(combo, gates):
    return current_signal_action_level(combo, gates)


def mlpb_signal(row):
    return f"{row.get('setup', 'MLPB')} {row.get('variant', '')}".strip()


def mlpb_action(label, prime_tier=None):
    if prime_tier == "A_PLUS_TRADE_CANDIDATE":
        return "LIVE_REVIEW"
    if label == "TRADE_REVIEW_MANUAL_PENDING":
        return "LIVE_REVIEW"
    if label == "WATCH_PULLBACK":
        return "WAIT_SIGNAL"
    if label == "EVENT_BLOCKED":
        return "BLOCKED"
    return "NO_TRADE"


def mlpb_tier(label, prime_tier=None):
    if prime_tier == "A_PLUS_TRADE_CANDIDATE":
        return "TIER1"
    if label == "TRADE_REVIEW_MANUAL_PENDING":
        return "TIER1"
    if label == "EVENT_BLOCKED":
        return "BLOCKED"
    return "NO_TRADE"


def mlpb_quality_label(score):
    if score >= 95:
        return "95"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "No trade"


def simplify_mlpb_combo(row):
    hist = row.get("hist") or {}
    label = row.get("label")
    prime = row.get("prime_tier")
    warnings = row.get("warnings") or []
    blocks = row.get("blocks") or []
    visual_reasons = row.get("latest_visual_reasons") or row.get("visual_reasons") or []
    qt_reasons = row.get("latest_qt_reasons") or row.get("qt_reasons") or []
    score = int(row.get("score") or 0)
    return {
        "ticker": row.get("ticker"),
        "name": row.get("name") or row.get("ticker"),
        "market": "US",
        "group": row.get("group"),
        "signal": mlpb_signal(row),
        "label": "MLPB",
        "tier": mlpb_tier(label, prime),
        "prime_tier": prime,
        "visual_grade": row.get("latest_visual_grade") or row.get("visual_grade"),
        "visual_score": safe_round(row.get("latest_visual_score") or row.get("visual_score"), 0),
        "visual_reasons": visual_reasons,
        "qt_label": row.get("latest_qt_label") or row.get("qt_label"),
        "qt_score": safe_round(row.get("latest_qt_score") or row.get("qt_score"), 0),
        "qt_reasons": qt_reasons,
        "qt_z": safe_round(row.get("latest_qt_z") or row.get("qt_z"), 4),
        "qt_z_delta": safe_round(row.get("latest_qt_z_delta") or row.get("qt_z_delta"), 4),
        "qt_stretch_pctile": safe_round(row.get("latest_qt_stretch_pctile") or row.get("qt_stretch_pctile"), 4),
        "qt_abs_stretch_pctile": safe_round(row.get("latest_qt_abs_stretch_pctile") or row.get("qt_abs_stretch_pctile"), 4),
        "qt_phase": row.get("latest_qt_phase") or row.get("qt_phase"),
        "qt_wait_score": safe_round(row.get("latest_qt_wait_score") or row.get("qt_wait_score"), 0),
        "qt_wait_label": row.get("latest_qt_wait_label") or row.get("qt_wait_label"),
        "qt_confirmation": row.get("latest_qt_confirmation") or row.get("qt_confirmation"),
        "qt_post_entry_state": row.get("latest_qt_post_entry_state") or row.get("qt_post_entry_state"),
        "qt_regime": row.get("latest_qt_regime") or row.get("qt_regime"),
        "n": hist.get("n", 0),
        "fwd21_mean": safe_round(hist.get("nextopen21_mean")),
        "fwd21_median": safe_round(hist.get("nextopen21_median")),
        "hit": safe_round(hist.get("nextopen21_hit")),
        "ci_low": None,
        "mae_p10": safe_round(hist.get("nextopen_mae21_p10")),
        "shake20": None,
        "lift": safe_round(hist.get("nextopen21_winsor")),
        "q": None,
        "rand_p": None,
        "recent_n": 0,
        "recent_mean": None,
        "recent2023_n": 0,
        "recent2023_mean": None,
        "quality_score": score,
        "quality": mlpb_quality_label(score),
        "quality_blockers": (warnings + visual_reasons + qt_reasons + blocks)[:6],
        "framework": "MLPB",
        "earnings_date": row.get("earnings_date"),
    }


def simplify_mlpb_current(row):
    label = row.get("label")
    prime = row.get("prime_tier")
    warnings = row.get("warnings") or []
    blocks = row.get("blocks") or []
    visual_reasons = row.get("latest_visual_reasons") or []
    qt_reasons = row.get("latest_qt_reasons") or []
    signal = mlpb_signal(row)
    action = mlpb_action(label, prime)
    out = {
        "date": row.get("date"),
        "ticker": row.get("ticker"),
        "name": row.get("name") or row.get("ticker"),
        "market": "US",
        "group": row.get("group"),
        "signal": signal,
        "entry": safe_round(row.get("close"), 4),
        "ri_rsi": None,
        "ri_dirrvol": None,
        "gap_p95_252": None,
        "vol63_ann": safe_round(row.get("atr14_pct")),
        "tier": mlpb_tier(label, prime),
        "prime_tier": prime,
        "label": label,
        "quality_score": int(row.get("score") or 0),
        "quality": mlpb_quality_label(int(row.get("score") or 0)),
        "visual_grade": row.get("latest_visual_grade"),
        "visual_score": safe_round(row.get("latest_visual_score"), 0),
        "visual_reasons": visual_reasons,
        "qt_label": row.get("latest_qt_label"),
        "qt_score": safe_round(row.get("latest_qt_score"), 0),
        "qt_reasons": qt_reasons,
        "qt_z": safe_round(row.get("latest_qt_z"), 4),
        "qt_z_delta": safe_round(row.get("latest_qt_z_delta"), 4),
        "qt_stretch_pctile": safe_round(row.get("latest_qt_stretch_pctile"), 4),
        "qt_abs_stretch_pctile": safe_round(row.get("latest_qt_abs_stretch_pctile"), 4),
        "qt_phase": row.get("latest_qt_phase"),
        "qt_wait_score": safe_round(row.get("latest_qt_wait_score"), 0),
        "qt_wait_label": row.get("latest_qt_wait_label"),
        "qt_confirmation": row.get("latest_qt_confirmation"),
        "qt_post_entry_state": row.get("latest_qt_post_entry_state"),
        "qt_regime": row.get("latest_qt_regime"),
        "quality_blockers": (warnings + visual_reasons + qt_reasons)[:6],
        "blockers": (warnings + visual_reasons + qt_reasons + blocks)[:6],
        "combo": simplify_mlpb_combo(row),
        "gates": [{"gate": "mlpb50" if row.get("setup") == "MLPB50" else "mlpb21", "robust_key": action == "LIVE_REVIEW"}],
        "action": action,
        "framework": "MLPB",
        "current_gate": label,
        "earnings_date": row.get("earnings_date"),
        "latest_close": safe_round(row.get("latest_close"), 4),
        "latest_dist_sma": safe_round(row.get("latest_dist_sma")),
        "latest_atr14_pct": safe_round(row.get("latest_atr14_pct")),
        "latest_rsi14": safe_round(row.get("latest_rsi14"), 2),
    }
    out["governance"] = governance_for_signal(out)
    out["thesis"] = trade_thesis_for_signal(out)
    return out


def main() -> int:
    robustness = json.loads(ROBUSTNESS_PATH.read_text())
    walkforward = json.loads(WALKFORWARD_PATH.read_text())
    current_raw = json.loads(CURRENT_PATH.read_text())
    mlpb_payload = {"candidates": []}
    if MLPB_GATE_PATH.exists():
        mlpb_payload = json.loads(MLPB_GATE_PATH.read_text())

    combos = [simplify_combo(row) for row in robustness]
    mlpb_current = [simplify_mlpb_current(row) for row in mlpb_payload.get("candidates", [])]
    mlpb_combos = [row["combo"] for row in mlpb_current]
    combos.extend(mlpb_combos)
    combos.sort(key=lambda row: (
        LABEL_RANK.get(row["label"], 9),
        0 if row.get("framework") == "MLPB" else 1,
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
        out = {
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
        }
        out["governance"] = governance_for_signal(out)
        out["thesis"] = trade_thesis_for_signal(out)
        current.append(out)

    current.extend(mlpb_current)
    signal_memory = update_stock_signal_journal(current)
    journal_today = {
        item.get("key"): item
        for item in signal_memory.get("today", [])
        if isinstance(item, dict) and item.get("key")
    }
    for row in current:
        journal_item = journal_today.get(current_key_text(row))
        if journal_item:
            row["lifecycle"] = journal_item.get("lifecycle")
            row["outcome"] = journal_item.get("outcome")
            row["post_entry_monitor"] = journal_item.get("post_entry_monitor")
            if isinstance(row.get("thesis"), dict):
                row["thesis"]["lifecycle"] = journal_item.get("lifecycle")
                row["thesis"]["probability_delta"] = journal_item.get("probability_delta")
                row["thesis"]["post_entry_monitor"] = journal_item.get("post_entry_monitor")
        else:
            row["lifecycle"] = {
                "state": "ARCHIVE",
                "label": "Historical",
                "first_seen_at": None,
                "last_seen_at": None,
                "seen_count": 0,
                "active": False,
                "previous_action": None,
                "action_change": None,
                "probability_delta": None,
            }
            row["post_entry_monitor"] = post_entry_monitor(row)

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
        current_rows = sorted(
            current_by_ticker.get(ticker, []),
            key=lambda row: (action_rank(row.get("action")), -date_rank(row.get("date"))),
        )
        action = stock_action(best, current_rows)
        blockers = stock_blockers(best, current_rows)
        current_best = current_rows[0] if current_rows else None
        stock_row = {
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
            "current_gate": current_best.get("current_gate") or "+".join(g["gate"] for g in current_best.get("gates", [])) if current_best else "",
            "prime_tier": current_best.get("prime_tier") if current_best else best.get("prime_tier") if best else None,
            "visual_grade": current_best.get("visual_grade") if current_best else best.get("visual_grade") if best else None,
            "qt_label": current_best.get("qt_label") if current_best else best.get("qt_label") if best else None,
            "qt_phase": current_best.get("qt_phase") if current_best else best.get("qt_phase") if best else None,
            "qt_wait_label": current_best.get("qt_wait_label") if current_best else best.get("qt_wait_label") if best else None,
            "qt_confirmation": current_best.get("qt_confirmation") if current_best else best.get("qt_confirmation") if best else None,
            "qt_post_entry_state": current_best.get("qt_post_entry_state") if current_best else best.get("qt_post_entry_state") if best else None,
            "current_action": current_best.get("action") if current_best else "",
            "lifecycle": current_best.get("lifecycle") if current_best else None,
            "post_entry_monitor": current_best.get("post_entry_monitor") if current_best else None,
            "framework": current_best.get("framework") if current_best else best.get("framework") if best else "",
            "earnings_date": current_best.get("earnings_date") if current_best else best.get("earnings_date") if best else None,
            "blockers": blockers,
        }
        stock_row["governance"] = current_best.get("governance") if current_best else governance_for_signal(stock_row)
        stock_row["thesis"] = current_best.get("thesis") if current_best else trade_thesis_for_signal(stock_row)
        stocks.append(stock_row)
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
            "source": "stock robustness + walk-forward audits + MLPB final gate",
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
            "current_live_review": sum(1 for row in current if row["action"] == "LIVE_REVIEW"),
            "current_watch": sum(1 for row in current if row["action"] in {"WAIT_SIGNAL", "WAIT_95_SIGNAL"}),
            "current_event_blocked": sum(1 for row in current if row["action"] == "BLOCKED"),
            "mlpb_current": len(mlpb_current),
            "mlpb_review": sum(1 for row in mlpb_current if row["action"] == "LIVE_REVIEW"),
            "mlpb_prime_a_plus": sum(1 for row in mlpb_current if row.get("prime_tier") == "A_PLUS_TRADE_CANDIDATE"),
            "mlpb_prime_a_watch": sum(1 for row in mlpb_current if row.get("prime_tier") == "A_WATCH"),
            "mlpb_prime_b_watch": sum(1 for row in mlpb_current if row.get("prime_tier") == "B_WATCH"),
            "current_paper_track": sum(1 for row in current if row["action"] == "PAPER_TRACK"),
            "strict_oos_mean": stat(rolling.get("strict", {}), "mean"),
            "strict_oos_hit": stat(rolling.get("strict", {}), "hit"),
            "strict_oos_net_100bps": stat(rolling.get("strict", {}), "net_100bps"),
            "candidate_oos_mean": stat(rolling.get("candidate", {}), "mean"),
            "candidate_oos_net_100bps": stat(rolling.get("candidate", {}), "net_100bps"),
            "signal_memory": signal_memory,
            "post_entry_open": sum(1 for row in current if (row.get("post_entry_monitor") or {}).get("status") == "OPEN"),
            "post_entry_matured": sum(1 for row in current if (row.get("post_entry_monitor") or {}).get("status") == "MATURED_21D"),
            "thesis_upgraded": signal_memory.get("upgraded_count", 0),
            "thesis_downgraded": signal_memory.get("downgraded_count", 0),
            "thesis_returned": signal_memory.get("returned_count", 0),
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
