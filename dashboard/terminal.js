(function () {
  "use strict";

  const STATE_KEY = "swing-terminal-state";
  const data = window.SCAN_DATA || { meta: {}, rows: [] };
  const stockData = window.STOCK_DATA || { meta: {}, stocks: [], combos: [], current: [] };
  const NUMERIC_COLS = [
    "ri_rvol", "ri_rsi", "ri_dist252", "ri_dist21", "close", "rsi9",
    "dirvol63", "dir_logvolz63", "dir_rvol_delta_1d", "dir_rvol_delta_5d",
    "dist_sma21", "dist_sma252", "sma21", "sma52", "sma126", "sma252", "bars",
    "ri_dist126", "dist_sma126", "atr20_pct", "rsi9_delta_1d", "rsi9_delta_5d",
    "ri_logvolz", "ri_rvol_delta_1d", "ri_rvol_delta_5d",
    "delay_minutes", "sig_priority", "near_score", "quality_score", "analog_score",
    "freshness_score", "path_risk_score", "regime_score",
    "qt_score", "qt_z", "qt_z_delta", "qt_wait_score",
  ];

  const state = {
    sortKey: "ri_rsi",
    sortAsc: true,
    viewMode: "command",
    filterRegion: "",
    filterBull: "",
    filterFlag: "",
    filterSearch: "",
    filterRegim: "",
    filterVolcap: "",
    stockFilterTier: "",
    stockFilterMarket: "",
    stockFilterAction: "",
    stockFilterSearch: "",
    selectedTicker: null,
    selectedSource: "scanner",
  };

  // ----- Helpers -----
  function fmt(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    if (typeof v === "number") return v.toFixed(digits ?? 3);
    return String(v);
  }

  function zClass(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "z-neutral";
    const a = Math.abs(v);
    if (a >= 1.5) return v > 0 ? "z-strong-pos" : "z-strong-neg";
    if (a >= 0.5) return v > 0 ? "z-pos" : "z-neg";
    return "z-neutral";
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.className) node.className = opts.className;
      if (opts.text !== undefined) node.textContent = String(opts.text);
      if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, String(v));
      }
      if (opts.dataset) {
        for (const [k, v] of Object.entries(opts.dataset)) node.dataset[k] = String(v);
      }
    }
    if (children) {
      for (const c of children) {
        if (c == null) continue;
        node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
      }
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function timeLabel(ts) {
    if (!ts) return "—";
    const text = String(ts);
    const match = text.match(/T(\d{2}:\d{2})/);
    return match ? match[1] : text.slice(11, 16) || text;
  }

  function isScanStale(dateStr) {
    if (!dateStr || dateStr === "—") return false;
    const scan = new Date(dateStr + "T00:00:00");
    if (isNaN(scan.getTime())) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = Math.round((today - scan) / 86400000);
    if (diffDays <= 0) return false;
    const dow = today.getDay();
    const tolerance = dow === 1 ? 3 : dow === 0 ? 2 : dow === 6 ? 1 : 1;
    return diffDays > tolerance;
  }

  function bullNode(b) {
    if (b === true) return el("span", { className: "bull-yes", text: "Y" });
    if (b === false) return el("span", { className: "bull-no", text: "N" });
    return el("span", { className: "dim", text: "–" });
  }

  function regimLabel(row) {
    if (row.bull_stack === true) return { label: "BULL", cls: "regim-bull" };
    if (row.sma126 != null && row.sma252 != null && row.sma126 > row.sma252)
      return { label: "126+", cls: "regim-trans" };
    return { label: "BEAR", cls: "regim-bear" };
  }

  function signalPriority(row) {
    if (row.cap === true) return 0;
    if (row.pb126 === true) return 1;
    if (row.pb === true) return 2;
    if (row.status === "FETCH_ERROR") return 8;
    if (row.status === "SKIPPED") return 9;
    return 7;
  }

  function caveatsFor(row) {
    const note = String(row.note || "");
    const caveats = [];
    const calibratedVolume = row.threshold_mode === "per-instrument" || row.threshold_mode === "proxy-volume";
    if (row.ticker && row.ticker.startsWith("^")) caveats.push("index/reference");
    if (row.currency === "USD") caveats.push("USD/FX");
    if (row.currency === "GBp") caveats.push("GBX units");
    if (typeof row.ter === "number" && row.ter > 0.3) caveats.push("TER exception");
    if (/Nordnet|BELIEVED|verifier/i.test(note)) caveats.push("Nordnet check");
    if (/swap|GBX|spread|lågvolym|low volume/i.test(note)) caveats.push("execution caveat");
    if (row.intraday_overlay !== true) caveats.push("no intraday");
    if (!calibratedVolume) caveats.push("threshold fallback");
    if (row.execution_requires_manual_spread_check === true) caveats.push("spread/liquidity manual");
    if (row.trade_readiness === "MANUAL_CHECK_REQUIRED") caveats.push("manual trade checks");
    if (row.gap_risk && row.gap_risk !== "OK" && row.gap_risk !== "UNKNOWN") caveats.push("price gap review");
    if (row.case_engine?.caveats?.length) caveats.push(...row.case_engine.caveats.slice(0, 2));
    if (row.case_engine?.blockers?.length) caveats.push(...row.case_engine.blockers.slice(0, 2));
    return Array.from(new Set(caveats));
  }

  function rowQualityScore(row) {
    if (row.status === "FETCH_ERROR") return 0;
    if (row.status === "SKIPPED") return 25;
    let score = 100;
    const calibratedVolume = row.threshold_mode === "per-instrument" || row.threshold_mode === "proxy-volume";
    if (!calibratedVolume) score -= 25;
    if (row.intraday_overlay !== true) score -= 18;
    if (typeof row.bars === "number" && row.bars < 1240) score -= 35;
    if (row.data_latency === "UNKNOWN") score -= 8;
    if (caveatsFor(row).length) score -= Math.min(12, caveatsFor(row).length * 3);
    return Math.max(0, Math.min(100, Math.round(score)));
  }

  function qualityClass(score) {
    if (score >= 95) return "quality-good";
    if (score >= 85) return "quality-watch";
    return "quality-risk";
  }

  function pct(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return `${(v * 100).toFixed(digits ?? 1)}%`;
  }

  function analogScore(row) {
    const a = row.analog || {};
    if (a.status !== "OK" || typeof a.fwd21_mean !== "number") return null;
    return a.fwd21_mean;
  }

  function freshnessScore(row) {
    const f = row.freshness || {};
    return typeof f.score === "number" ? f.score : null;
  }

  function pathRiskScore(row) {
    const a = row.analog || {};
    return typeof a.path_risk_score === "number" ? a.path_risk_score : null;
  }

  function analogClass(verdict) {
    if (verdict === "TAILWIND") return "analog-tailwind";
    if (verdict === "POSITIVE") return "analog-positive";
    if (verdict === "HEADWIND") return "analog-headwind";
    if (verdict === "MIXED") return "analog-mixed";
    return "analog-nosample";
  }

  function analogNode(row) {
    const a = row.analog || {};
    if (a.status !== "OK") {
      return el("span", { className: "analog-chip analog-nosample", text: "N/A", attrs: { title: a.status || "no analog sample" } });
    }
    const text = `${pct(a.fwd21_mean, 1)} / ${(a.fwd21_hit_rate * 100).toFixed(0)}% / n${a.n}`;
    const title = [
      `Context: ${a.verdict}`,
      `Confidence: ${a.confidence}`,
      `fwd5 ${pct(a.fwd5_mean, 1)}`,
      `fwd21 ${pct(a.fwd21_mean, 1)} hit ${(a.fwd21_hit_rate * 100).toFixed(0)}%`,
      `fwd63 ${pct(a.fwd63_mean, 1)}`,
      `MAE p10 ${pct(a.mae21_p10, 1)}`,
    ].join(" · ");
    return el("span", { className: `analog-chip ${analogClass(a.verdict)}`, text, attrs: { title } });
  }

  function freshnessClass(state) {
    if (state === "LIVE" || state === "FRESH") return "fresh-live";
    if (state === "USABLE") return "fresh-usable";
    if (state === "AGING") return "fresh-aging";
    if (state === "STALE") return "fresh-stale";
    return "fresh-none";
  }

  function freshnessNode(row) {
    const f = row.freshness || {};
    if (f.status !== "OK") {
      return el("span", { className: "fresh-chip fresh-none", text: "—", attrs: { title: `No signal in last ${f.lookback || 21} trading days` } });
    }
    const text = `${f.best_type} D+${f.best_age}`;
    const title = [
      `Freshness: ${f.best_state}`,
      `Score: ${f.score}`,
      `Lookback: ${f.lookback} trading days`,
    ].join(" · ");
    return el("span", { className: `fresh-chip ${freshnessClass(f.best_state)}`, text, attrs: { title } });
  }

  function pathRiskClass(risk) {
    if (risk === "LOW") return "path-low";
    if (risk === "MEDIUM") return "path-medium";
    if (risk === "HIGH") return "path-high";
    if (risk === "EXTREME") return "path-extreme";
    return "path-unknown";
  }

  function pathNode(row) {
    const a = row.analog || {};
    if (a.status !== "OK") {
      return el("span", { className: "path-chip path-unknown", text: "N/A", attrs: { title: a.status || "no analog sample" } });
    }
    const text = `${a.path_risk || "?"} ${pct(a.mae21_p10, 0)}`;
    const title = [
      `Path risk: ${a.path_risk}`,
      `MAE p10 ${pct(a.mae21_p10, 1)}`,
      `MFE median ${pct(a.mfe21_median, 1)}`,
      `Shakeout >5% ${pct(a.shakeout_5pct_rate, 0)}`,
      `Shakeout >10% ${pct(a.shakeout_10pct_rate, 0)}`,
      a.reward_to_pain != null ? `Reward/pain ${fmt(a.reward_to_pain, 2)}` : null,
    ].filter(Boolean).join(" · ");
    return el("span", { className: `path-chip ${pathRiskClass(a.path_risk)}`, text, attrs: { title } });
  }

  function regimePrimary(row) {
    return (row.regime_context && row.regime_context.primary) || null;
  }

  function regimeLabels(row) {
    return (row.regime_context && Array.isArray(row.regime_context.labels))
      ? row.regime_context.labels
      : [];
  }

  function regimeClass(tone) {
    if (tone === "boost") return "regime-boost";
    if (tone === "clean") return "regime-clean";
    if (tone === "blocker" || tone === "avoid") return "regime-blocker";
    if (tone === "context") return "regime-contextual";
    return "regime-neutral";
  }

  function regimeNode(row, limit) {
    const labels = regimeLabels(row).slice(0, limit ?? 2);
    if (!labels.length) {
      const primary = regimePrimary(row);
      return el("span", {
        className: "regime-chip regime-neutral",
        text: primary?.label || "NO 95 REGIME",
        attrs: { title: primary?.reason || "No validated regime context" },
      });
    }
    const wrap = el("div", { className: "regime-chip-row" });
    labels.forEach((label) => {
      wrap.appendChild(el("span", {
        className: `regime-chip ${regimeClass(label.tone)}`,
        text: label.label,
        attrs: { title: [label.verdict, label.reason, label.stats].filter(Boolean).join(" · ") },
      }));
    });
    return wrap;
  }

  function regimeScore(row) {
    const primary = regimePrimary(row);
    return typeof primary?.priority === "number" ? primary.priority : 99;
  }

  function alignmentStateClass(state) {
    if (state === "GREEN") return "align-green";
    if (state === "NEAR") return "align-near";
    if (state === "WATCH") return "align-watch";
    return "align-off";
  }

  function alignmentDot(ok) {
    return el("span", {
      className: ok ? "align-dot align-ok" : "align-dot align-miss",
      text: ok ? "✓" : "×",
    });
  }

  function renderAlignmentBoard(row, mode) {
    const alignment = row.alignment || {};
    const best = alignment.best || {};
    const order = mode === "spotlight" && best.key ? [best.key] : ["cap", "pb126", "pb"];
    const board = el("div", { className: `alignment-board ${mode === "spotlight" ? "alignment-spotlight" : ""}` });
    board.appendChild(el("div", { className: "alignment-head" }, [
      el("span", { className: "alignment-title", text: "Signal alignment" }),
      el("span", {
        className: `alignment-read ${alignmentStateClass(best.state)}`,
        text: best.label ? `${best.label} · ${best.state}` : "NO DATA",
      }),
    ]));
    if (best.action) {
      const missing = best.missing && best.missing.length ? ` · saknas: ${best.missing.slice(0, 2).join(", ")}` : "";
      board.appendChild(el("div", { className: "alignment-action", text: `${best.action}${missing}` }));
    }
    board.appendChild(regimeNode(row, mode === "spotlight" ? 2 : 3));

    order.forEach((key) => {
      const sig = alignment[key];
      if (!sig) return;
      const rowNode = el("div", { className: `alignment-row ${alignmentStateClass(sig.state)}` });
      rowNode.appendChild(el("div", { className: "alignment-row-top" }, [
        el("span", { className: "alignment-signal", text: sig.label }),
        el("span", { className: "alignment-progress", text: `${sig.count}/${sig.total}` }),
        el("span", { className: "alignment-state", text: sig.state }),
      ]));
      const criteria = el("div", { className: "alignment-criteria" });
      (sig.criteria || []).forEach((c) => {
        criteria.appendChild(el("span", {
          className: `alignment-criterion ${c.ok ? "is-ok" : "is-miss"}`,
          attrs: { title: c.value || c.label },
        }, [
          alignmentDot(Boolean(c.ok)),
          el("span", { text: c.label }),
        ]));
      });
      rowNode.appendChild(criteria);
      rowNode.appendChild(el("div", {
        className: "alignment-wait",
        text: sig.trigger
          ? "GREEN: review long setup"
          : (sig.missing && sig.missing.length ? `Väntar på: ${sig.missing[0]}` : "Ingen aktiv setup"),
      }));
      board.appendChild(rowNode);
    });

    return board;
  }

  function alignmentRank(row) {
    const best = row?.alignment?.best;
    if (!best) return 999;
    const stateRank = { GREEN: 0, NEAR: 1, WATCH: 2, OFF: 3 };
    const ratio = best.total ? best.count / best.total : 0;
    const gap = typeof best.gap === "number" ? best.gap : 999;
    return (stateRank[best.state] ?? 4) * 1000 - ratio * 100 + gap;
  }

  function signalAlignment(row, key) {
    return (row && row.alignment && row.alignment[key]) || null;
  }

  function isWatchableAlignment(sig) {
    return Boolean(sig && !sig.trigger && (sig.state === "NEAR" || sig.state === "WATCH"));
  }

  function alignmentNearScore(sig) {
    if (!sig) return null;
    if (sig.trigger || sig.state === "GREEN") return -1;
    if (!isWatchableAlignment(sig)) return null;
    const gap = typeof sig.gap === "number" && Number.isFinite(sig.gap) ? sig.gap : 999;
    const statePenalty = sig.state === "NEAR" ? 0 : 10;
    const missingPenalty = Math.max(0, (sig.total || 0) - (sig.count || 0) - 1) * 0.25;
    return statePenalty + missingPenalty + gap;
  }

  function bestWatchAlignment(row) {
    const entries = ["cap", "pb126", "pb"]
      .map((key) => signalAlignment(row, key))
      .filter(isWatchableAlignment)
      .map((sig) => ({ sig, score: alignmentNearScore(sig) }))
      .filter((x) => typeof x.score === "number" && Number.isFinite(x.score))
      .sort((a, b) => a.score - b.score);
    return entries.length ? entries[0] : null;
  }

  function alignmentRowForPanel() {
    if (state.selectedTicker) {
      const selected = (data.rows || []).find((r) => r.ticker === state.selectedTicker);
      if (selected) return { row: selected, mode: "Selected" };
    }
    const rows = (data.rows || [])
      .filter((r) => r.status === "OK" && r.alignment?.best)
      .sort((a, b) => alignmentRank(a) - alignmentRank(b));
    return rows.length ? { row: rows[0], mode: "Top watch" } : { row: null, mode: "No data" };
  }

  function renderAlignmentPanel() {
    const wrap = document.getElementById("alignment-panel");
    if (!wrap) return;
    clear(wrap);
    const meta = document.getElementById("alignment-meta");
    const pick = alignmentRowForPanel();
    if (meta) meta.textContent = pick.mode;
    if (!pick.row) {
      wrap.appendChild(el("div", { className: "detail-empty", text: "No alignment data." }));
      return;
    }
    wrap.appendChild(el("div", { className: "alignment-focus" }, [
      el("div", { className: "alignment-focus-ticker", text: pick.row.ticker }),
      el("div", { className: "alignment-focus-name", text: `${pick.row.name || ""} · ${pick.row.region || ""}` }),
    ]));
    wrap.appendChild(renderAlignmentBoard(pick.row, "spotlight"));
  }

  function actionFor(row) {
    if (row.status === "FETCH_ERROR") return { label: "DATA FIX", cls: "action-risk", priority: 0, reason: "fetch error" };
    if (row.status === "SKIPPED") {
      const reason = String(row.reason || "too few bars");
      if (reason.startsWith("STALE_DATA")) return { label: "DATA STALE", cls: "action-risk", priority: 0, reason };
      if (reason.startsWith("SPLIT_ACTION")) return { label: "SPLIT CHECK", cls: "action-risk", priority: 0, reason };
      return { label: "HISTORY GAP", cls: "action-risk", priority: 1, reason };
    }
    const regime = regimePrimary(row);
    if (regime?.verdict === "BLOCKER") return { label: "BLOCKED", cls: "action-risk", priority: 2, reason: regime.reason };
    if (regime?.verdict === "95_SIGNAL_BOOST") return { label: regime.label, cls: "action-go", priority: 3, reason: regime.reason };
    if (row.case_engine?.state === "WAIT_REPAIR_NEEDED" && row.case_engine?.blockers?.length) {
      return {
        label: "WAIT / REPAIR",
        cls: "action-watch",
        priority: 3,
        reason: `${row.case_engine.label || "case needs repair"} · ${row.case_engine.blockers.slice(0, 2).join(", ")}`,
      };
    }
    if (row.pb126) return { label: "REVIEW PB126", cls: "action-go", priority: 4, reason: "validated §5b.2" };
    if (row.cap) return { label: "REVIEW CAP", cls: "action-watch", priority: 5, reason: "probationary capitulation" };
    if (row.pb) return { label: "REVIEW PB", cls: "action-watch", priority: 6, reason: "probationary SMA21 pullback" };
    if (row.case_engine?.state === "WAIT_REPAIR_NEEDED") {
      return {
        label: "WAIT / REPAIR",
        cls: "action-watch",
        priority: 8,
        reason: row.case_engine.label || "near signal, missing confirmation",
      };
    }
    if (regime?.id === "downstack_drift_avoid") return { label: "AVOID DRIFT", cls: "action-risk", priority: 7, reason: regime.reason };
    if (regime?.verdict === "95_CONTEXT_EDGE") return { label: regime.label, cls: "action-watch", priority: 8, reason: regime.reason };
    const watch = bestWatchAlignment(row);
    if (watch?.sig?.state === "NEAR") {
      return {
        label: `${watch.sig.label} NEAR`,
        cls: "action-watch",
        priority: 9,
        reason: `${watch.sig.count}/${watch.sig.total}; wait ${watch.sig.missing?.[0] || "confirmation"}; gap ${fmt(watch.sig.gap, 2)}`,
      };
    }
    if (watch?.sig?.state === "WATCH") {
      return {
        label: `${watch.sig.label} WATCH`,
        cls: "action-neutral",
        priority: 10,
        reason: `${watch.sig.count}/${watch.sig.total}; missing ${(watch.sig.missing || []).slice(0, 2).join(", ") || "criteria"}`,
      };
    }
    if (row.atr20_pct != null && row.atr20_pct > 4) return { label: "VOLCAP", cls: "action-neutral", priority: 11, reason: `ATR ${fmt(row.atr20_pct, 1)}%` };
    return { label: "SCAN", cls: "action-muted", priority: 99, reason: "no action" };
  }

  function nearScore(row) {
    if (row.cap || row.pb126 || row.pb) return -1;
    const watch = bestWatchAlignment(row);
    return watch ? watch.score : null;
  }

  function sortValue(row, key) {
    if (key === "regim") return regimLabel(row).label;
    if (key === "sig_priority") return signalPriority(row);
    if (key === "near_score") return nearScore(row);
    if (key === "quality_score") return rowQualityScore(row);
    if (key === "analog_score") return analogScore(row);
    if (key === "freshness_score") return freshnessScore(row);
    if (key === "path_risk_score") return pathRiskScore(row);
    if (key === "regime_score") return regimeScore(row);
    return row[key];
  }

  function rowSignalClass(row) {
    if (row.cap) return "row-cap";
    if (row.pb126) return "row-pb126";
    if (row.pb) return "row-pb";
    return "";
  }

  function isDelayed(row) {
    return row.intraday_overlay !== true || (
      row.data_latency !== "REAL_TIME" &&
      row.data_latency !== "US_BEST_EFFORT_RT"
    );
  }

  function compactTime(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
    const now = new Date();
    const sameDay = d.toLocaleDateString("sv-SE", { timeZone: "Europe/Stockholm" }) ===
      now.toLocaleDateString("sv-SE", { timeZone: "Europe/Stockholm" });
    const opts = sameDay
      ? { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit", hour12: false }
      : { timeZone: "Europe/Stockholm", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("sv-SE", opts).format(d);
  }

  function delayBadge(row) {
    const latency = row.data_latency || "UNKNOWN";
    const hasOverlay = row.intraday_overlay === true;
    const label = hasOverlay
      ? (row.data_latency_label || (latency === "DELAY_15M" ? "15m" : "?"))
      : "1D";
    const cls = !hasOverlay
      ? "delay-eod"
      : latency === "REAL_TIME" || latency === "US_BEST_EFFORT_RT"
      ? "delay-rt"
      : latency === "DELAY_15M" || latency === "DELAY_20M" || latency === "DELAY_30M"
        ? "delay-delayed"
        : "delay-unknown";
    const title = [
      row.price_source || "unknown source",
      row.intraday_status || "no status",
      row.quote_time || row.last_close_date || "no timestamp",
    ].join(" · ");
    return el("span", { className: `delay-badge ${cls}`, text: label, attrs: { title } });
  }

  const SIGNAL_STATUS = {
    cap:   { label: "PROBATIONARY", class: "sig-badge-probationary", spec: "§5 — 0/5 Bonferroni" },
    pb:    { label: "PROBATIONARY", class: "sig-badge-probationary", spec: "§5b — whitelist only" },
    pb126: { label: "VALIDATED",    class: "sig-badge-validated",    spec: "§5b.2 — 5/5 W2 PASS" }
  };

  function sigBadge(type) {
    const s = SIGNAL_STATUS[type]; if (!s) return null;
    return el("span", { className: `sig-badge ${s.class}`, text: s.label, attrs: { title: s.spec } });
  }

  function flagNode(row) {
    if (row.cap) {
      return el("span", null, [
        el("span", { className: "flag-cap", text: "CAP" }),
        el("span", { className: "sig-badge sig-badge-mini sig-badge-probationary", text: "P", attrs: { title: "§5 — 0/5 Bonferroni" } })
      ]);
    }
    if (row.pb126) {
      return el("span", null, [
        el("span", { className: "flag-pb126", text: "PB126" }),
        el("span", { className: "sig-badge sig-badge-mini sig-badge-validated", text: "V", attrs: { title: "§5b.2 — 5/5 W2 PASS" } })
      ]);
    }
    if (row.pb) {
      return el("span", null, [
        el("span", { className: "flag-pb", text: "PB" }),
        el("span", { className: "sig-badge sig-badge-mini sig-badge-probationary", text: "P", attrs: { title: "§5b — whitelist only" } })
      ]);
    }
    return el("span", { className: "flag-none", text: "–" });
  }

  // ----- Meta render -----
  function renderMeta() {
    const m = data.meta || {};
    const scanDate = m.scan_date || (m.generated || "").split(" ")[0] || "—";
    const scanTime = (m.generated || "").split(" ")[1] || "—";
    const setText = (id, val) => { const node = document.getElementById(id); if (node) node.textContent = val; };
    setText("meta-date", scanDate);
    setText("meta-time", scanTime);
    setText("meta-scanned", String(m.scanned ?? 0));
    const latencyCounts = m.latency_counts || {};
    const latencyText = Object.entries(latencyCounts)
      .map(([k, v]) => `${k}:${v}`)
      .join(" ");
    setText("meta-source", latencyText || (m.data_source ? "YF" : "—"));
    const setCount = (id, val, activeCls) => {
      const node = document.getElementById(id);
      if (!node) return;
      const n = Number(val ?? 0);
      node.textContent = String(n);
      node.classList.remove("zero", "flag-cap-active", "flag-pb-active", "flag-pb126-active", "flag-error-active");
      node.classList.add(n > 0 ? activeCls : "zero");
    };
    setCount("meta-cap",    m.cap_signals,   "flag-cap-active");
    setCount("meta-pb",     m.pb_signals,    "flag-pb-active");
    setCount("meta-pb126",  m.pb126_signals, "flag-pb126-active");
    setCount("meta-errors", m.errors,        "flag-error-active");
    setText("meta-skipped", String(m.skipped ?? 0));
    const stale = isScanStale(scanDate);
    const dateEl = document.getElementById("meta-date");
    if (dateEl) dateEl.classList.toggle("stale", stale);
    const badge = document.getElementById("stale-badge");
    if (badge) {
      badge.textContent = stale ? "⚠ STALE" : "";
      badge.classList.toggle("visible", stale);
    }
  }

  // ----- localStorage persistence -----
  function loadPersistedState() {
    try {
      const saved = localStorage.getItem(STATE_KEY);
      if (!saved) return;
      const p = JSON.parse(saved);
      if (p.sortKey) state.sortKey = p.sortKey;
      if (p.sortAsc !== undefined) state.sortAsc = p.sortAsc;
      if (p.viewMode !== undefined) state.viewMode = p.viewMode;
      if (p.filterRegion !== undefined) state.filterRegion = p.filterRegion === "Sydamerika" ? "Latinamerika" : p.filterRegion;
      if (p.filterBull !== undefined) state.filterBull = p.filterBull;
      if (p.filterFlag !== undefined) state.filterFlag = p.filterFlag;
      if (p.filterSearch !== undefined) state.filterSearch = p.filterSearch;
      if (p.filterRegim !== undefined) state.filterRegim = p.filterRegim;
      if (p.filterVolcap !== undefined) state.filterVolcap = p.filterVolcap;
      if (p.stockFilterTier !== undefined) state.stockFilterTier = p.stockFilterTier;
      if (p.stockFilterMarket !== undefined) state.stockFilterMarket = p.stockFilterMarket;
      if (p.stockFilterAction !== undefined) state.stockFilterAction = p.stockFilterAction;
      if (p.stockFilterSearch !== undefined) state.stockFilterSearch = p.stockFilterSearch;
    } catch (_) { /* ignore */ }
  }

  function persistState() {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        sortKey: state.sortKey,
        sortAsc: state.sortAsc,
        viewMode: state.viewMode,
        filterRegion: state.filterRegion,
        filterBull: state.filterBull,
        filterFlag: state.filterFlag,
        filterSearch: state.filterSearch,
        filterRegim: state.filterRegim,
        filterVolcap: state.filterVolcap,
        stockFilterTier: state.stockFilterTier,
        stockFilterMarket: state.stockFilterMarket,
        stockFilterAction: state.stockFilterAction,
        stockFilterSearch: state.stockFilterSearch,
      }));
    } catch (_) { /* ignore */ }
  }

  function applyStateToDOM() {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val;
    };
    set("filter-region", state.filterRegion);
    set("filter-bull", state.filterBull);
    set("filter-flag", state.filterFlag);
    set("filter-search", state.filterSearch);
    set("filter-regim", state.filterRegim);
    set("filter-volcap", state.filterVolcap);
    set("stock-filter-tier", state.stockFilterTier);
    set("stock-filter-market", state.stockFilterMarket);
    set("stock-filter-action", state.stockFilterAction);
    set("stock-filter-search", state.stockFilterSearch);
    setTerminalView(state.viewMode, false);
  }

  function setTerminalView(view, shouldPersist) {
    const allowed = ["command", "scanner", "stocks", "signals", "trade", "detail"];
    const next = allowed.includes(view) ? view : "command";
    state.viewMode = next;
    document.body.dataset.terminalView = next;
    document.querySelectorAll(".view-tab[data-terminal-view]").forEach((button) => {
      const active = button.dataset.terminalView === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.setAttribute("tabindex", active ? "0" : "-1");
    });
    if (shouldPersist !== false) persistState();
  }

  function renderFilterWarning(rows) {
    const node = document.getElementById("filter-warning");
    if (!node) return;
    const active = [
      state.filterRegion,
      state.filterBull,
      state.filterFlag,
      state.filterSearch,
      state.filterRegim,
      state.filterVolcap,
    ].filter(Boolean).length;
    if (active === 0) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = `${active} filter · ${rows.length} rows`;
  }

  // ----- Filter + sort -----
  function activeRows() {
    let rows = (data.rows || []).slice();

    if (state.filterRegion) rows = rows.filter((r) => r.region === state.filterRegion);

    if (state.filterBull === "yes") rows = rows.filter((r) => r.bull_stack === true);
    else if (state.filterBull === "no") rows = rows.filter((r) => r.bull_stack === false);

    if (state.filterFlag === "cap") rows = rows.filter((r) => r.cap === true);
    else if (state.filterFlag === "pb") rows = rows.filter((r) => r.pb === true);
    else if (state.filterFlag === "pb126") rows = rows.filter((r) => r.pb126 === true);
    else if (state.filterFlag === "near") {
      rows = rows.filter((r) => isWatchableAlignment(signalAlignment(r, "cap")));
    } else if (state.filterFlag === "pb-near") {
      rows = rows.filter((r) =>
        isWatchableAlignment(signalAlignment(r, "pb")) ||
        isWatchableAlignment(signalAlignment(r, "pb126"))
      );
    }

    if (state.filterSearch) {
      const q = state.filterSearch.toLowerCase();
      rows = rows.filter((r) => (r.ticker || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q));
    }

    if (state.filterRegim === "bull") rows = rows.filter((r) => r.bull_stack === true);
    else if (state.filterRegim === "126") rows = rows.filter((r) =>
      r.bull_stack !== true && r.sma126 != null && r.sma252 != null && r.sma126 > r.sma252
    );
    else if (state.filterRegim === "bear") rows = rows.filter((r) => regimLabel(r).label === "BEAR");

    if (state.filterVolcap === "ok") rows = rows.filter((r) => r.atr20_pct == null || r.atr20_pct <= 4);
    else if (state.filterVolcap === "warn") rows = rows.filter((r) => r.atr20_pct != null && r.atr20_pct > 4);

    rows.sort((a, b) => {
      const k = state.sortKey;
      const va = sortValue(a, k);
      const vb = sortValue(b, k);
      const isNum = NUMERIC_COLS.includes(k);
      const nullsLast = (x) => (x === null || x === undefined || Number.isNaN(x));
      if (nullsLast(va) && nullsLast(vb)) return 0;
      if (nullsLast(va)) return 1;
      if (nullsLast(vb)) return -1;
      let cmp;
      if (isNum) cmp = va - vb;
      else if (typeof va === "boolean") cmp = (va === vb ? 0 : (va ? 1 : -1));
      else cmp = String(va).localeCompare(String(vb));
      return state.sortAsc ? cmp : -cmp;
    });

    return rows;
  }

  function renderGrid() {
    const rows = activeRows();
    renderFilterWarning(rows);
    const tbody = document.getElementById("scanner-tbody");
    const frag = document.createDocumentFragment();

    rows.forEach((r, i) => {
      const tr = el("tr", {
        attrs: { tabindex: "0", "aria-selected": state.selectedTicker === r.ticker ? "true" : "false" },
        dataset: { ticker: r.ticker },
      });
      tr.style.setProperty("--row-i", String(i));
      if (r.status === "SKIPPED") tr.classList.add("skipped");
      if (state.selectedTicker === r.ticker) tr.classList.add("selected");
      const sigCls = rowSignalClass(r);
      if (sigCls) tr.classList.add(sigCls);

      // Near-miss cell
      const nearMissCell = (() => {
        const d5 = (r.delta_5d_table != null) ? r.delta_5d_table : r.rsi9_delta_5d;
        const confOK = (d5 != null && !Number.isNaN(d5) && d5 > 0);
        const parts = ["cap", "pb126", "pb"]
          .map((key) => signalAlignment(r, key))
          .filter(isWatchableAlignment)
          .sort((a, b) => (alignmentNearScore(a) ?? 999) - (alignmentNearScore(b) ?? 999))
          .slice(0, 2)
          .map((sig) => `${sig.label} ${sig.count}/${sig.total} g${fmt(sig.gap, 2)}`);
        if (parts.length === 0) return el("span", { className: "dim", text: "–" });
        const sep = confOK ? " ✓" : " ✗";
        const best = bestWatchAlignment(r)?.sig;
        const missing = best?.missing?.length ? ` · väntar på ${best.missing.slice(0, 2).join(", ")}` : "";
        return el("span", {
          className: confOK ? "z-pos" : "z-neg",
          text: parts.join(" | ") + sep,
          attrs: { title: `Δ5d=${fmt(d5, 2)} (${confOK ? "confirmation OK" : "dead-cat-risk"})${missing}` },
        });
      })();

      const regim = regimLabel(r);

      const d126text = r.dist_sma126 != null
        ? (r.dist_sma126 >= 0 ? "+" : "") + r.dist_sma126.toFixed(1) + "%"
        : "—";

      const d1 = r.rsi9_delta_1d != null ? r.rsi9_delta_1d : r.delta_1d_table;
      const d5val = r.rsi9_delta_5d != null ? r.rsi9_delta_5d : r.delta_5d_table;
      const d1cls = (d1 != null && !Number.isNaN(d1)) ? (d1 > 0 ? "z-pos" : d1 < 0 ? "z-neg" : "") : "";
      const d5cls = (d5val != null && !Number.isNaN(d5val)) ? (d5val > 0 ? "z-pos" : d5val < 0 ? "z-neg" : "") : "";

      const atrText = r.atr20_pct != null ? r.atr20_pct.toFixed(2) + "%" : "—";
      const atrCls = (r.atr20_pct != null && r.atr20_pct > 4) ? "atr-warn" : "dim";

      const cells = [
        el("td", { className: "left ticker", text: r.ticker || "" }),
        el("td", { className: "left name", text: r.name || "" }),
        el("td", { className: "left", text: r.region || "" }),
        el("td", { className: regim.cls, text: regim.label }),
        el("td", null, [bullNode(r.bull_stack)]),
        el("td", { className: zClass(r.ri_rvol), text: fmt(r.ri_rvol, 2) }),
        el("td", { className: zClass(r.ri_rsi), text: fmt(r.ri_rsi, 2) }),
        el("td", { className: zClass(r.ri_dist252), text: fmt(r.ri_dist252, 2) }),
        el("td", { className: zClass(r.ri_dist21), text: fmt(r.ri_dist21, 2) }),
        el("td", { className: zClass(r.ri_dist126), text: d126text }),
        el("td", { text: fmt(r.close, 2) }),
        el("td", { className: "data-cell" }, [
          delayBadge(r),
          el("span", { className: "quote-time", text: compactTime(r.quote_time || r.last_close_date) }),
        ]),
        el("td", { text: fmt(r.rsi9, 1) }),
        el("td", { className: d1cls, text: fmt(d1, 1) }),
        el("td", { className: d5cls, text: fmt(d5val, 1) }),
        el("td", { className: zClass(r.dirvol63), text: fmt(r.dirvol63, 2) }),
        el("td", { className: atrCls, text: atrText }),
        el("td", null, [freshnessNode(r)]),
        el("td", null, [analogNode(r)]),
        el("td", null, [pathNode(r)]),
        el("td", null, [flagNode(r), (r.cap || r.pb) ? el("span", { className: "prob-badge", text: "PROB" }) : null]),
        el("td", { className: "left near-miss" }, [nearMissCell]),
      ];
      cells.forEach((td) => tr.appendChild(td));
      frag.appendChild(tr);
    });

    clear(tbody);
    tbody.appendChild(frag);

    document.getElementById("row-count").textContent = rows.length;
    document.getElementById("sort-label").textContent = `${state.sortKey} ${state.sortAsc ? "asc" : "desc"}`;
    renderAlignmentPanel();
  }

  function renderHeaderSort() {
    document.querySelectorAll("table.grid thead th").forEach((th) => {
      th.classList.remove("sort-active", "asc");
      th.setAttribute("aria-sort", "none");
      if (th.dataset.sort === state.sortKey) {
        th.classList.add("sort-active");
        if (state.sortAsc) th.classList.add("asc");
        th.setAttribute("aria-sort", state.sortAsc ? "ascending" : "descending");
      }
    });
  }

  function deckTile(label, value, sub, cls) {
    return el("div", { className: `deck-tile ${cls || ""}` }, [
      el("div", { className: "deck-label", text: label }),
      el("div", { className: "deck-value", text: value }),
      el("div", { className: "deck-sub", text: sub }),
    ]);
  }

  function renderMarketDeck() {
    const wrap = document.getElementById("market-deck");
    if (!wrap) return;
    clear(wrap);
    const m = data.meta || {};
    const rows = (data.rows || []).filter((r) => r.status === "OK");
    const bull = rows.filter((r) => r.bull_stack === true).length;
    const overlay = rows.filter((r) => r.intraday_overlay === true).length;
    const realtime = rows.filter((r) =>
      r.data_latency === "REAL_TIME" || r.data_latency === "US_BEST_EFFORT_RT"
    ).length;
    const cap = rows.filter((r) => r.cap).length;
    const pb126 = rows.filter((r) => r.pb126).length;
    const pb = rows.filter((r) => r.pb).length;
    const memory = m.signal_memory || {};
    const seenToday = Number(memory.today_count || 0);
    const activeToday = Number(memory.active_today_count || 0);
    const fadedToday = Number(memory.faded_today_count || 0);
    const caveatCount = rows.filter((r) => caveatsFor(r).length > 0).length;
    const quality = m.data_quality_score != null
      ? m.data_quality_score
      : Math.round(rows.reduce((sum, r) => sum + rowQualityScore(r), 0) / Math.max(1, rows.length));
    const regionText = m.region_counts
      ? Object.entries(m.region_counts).map(([k, v]) => `${k.slice(0, 1)}${v}`).join(" · ")
      : "region mix";
    const closest = rows
      .map((r) => ({ r, score: nearScore(r), watch: bestWatchAlignment(r)?.sig }))
      .filter((x) => typeof x.score === "number" && Number.isFinite(x.score))
      .sort((a, b) => a.score - b.score)[0];
    const hotRsi = rows
      .filter((r) => typeof r.rsi9_delta_5d === "number")
      .sort((a, b) => Math.abs(b.rsi9_delta_5d) - Math.abs(a.rsi9_delta_5d))[0];
    const bestAnalog = rows
      .filter((r) => r.analog && r.analog.status === "OK" && typeof r.analog.fwd21_mean === "number")
      .sort((a, b) => b.analog.fwd21_mean - a.analog.fwd21_mean)[0];
    const freshest = rows
      .filter((r) => r.freshness && r.freshness.status === "OK")
      .sort((a, b) => (b.freshness.score || 0) - (a.freshness.score || 0))[0];
    const cleanPath = rows
      .filter((r) => r.analog && r.analog.status === "OK" && typeof r.analog.path_risk_score === "number")
      .sort((a, b) => b.analog.path_risk_score - a.analog.path_risk_score || b.analog.fwd21_mean - a.analog.fwd21_mean)[0];
    const bestRegime = rows
      .filter((r) => regimeLabels(r).length > 0)
      .sort((a, b) => regimeScore(a) - regimeScore(b) || (nearScore(a) ?? 999) - (nearScore(b) ?? 999))[0];

    wrap.appendChild(deckTile("Quality", `${quality}%`, `${m.ok ?? rows.length}/${m.scanned ?? rows.length} ok · ${caveatCount} caveat`, quality >= 95 ? "deck-cool" : quality >= 85 ? "deck-watch" : "deck-risk"));
    wrap.appendChild(deckTile("Universe", String(rows.length), `${bull} bull · ${rows.length - bull} other`, "deck-neutral"));
    wrap.appendChild(deckTile("Signals", `${cap + pb126 + pb}`, `CAP ${cap} · PB126 ${pb126} · PB ${pb}`, cap + pb126 + pb ? "deck-hot" : "deck-cool"));
    wrap.appendChild(deckTile("Seen today", String(seenToday), `${activeToday} active · ${fadedToday} faded`, seenToday ? (activeToday ? "deck-hot" : "deck-watch") : "deck-neutral"));
    wrap.appendChild(deckTile("Regime 95", bestRegime ? bestRegime.ticker : "—", bestRegime ? regimePrimary(bestRegime).label : "no validated edge", bestRegime ? (regimePrimary(bestRegime).tone === "blocker" || regimePrimary(bestRegime).tone === "avoid" ? "deck-risk" : "deck-cool") : "deck-neutral"));
    wrap.appendChild(deckTile("Fresh", freshest ? freshest.ticker : "—", freshest ? `${freshest.freshness.best_type} D+${freshest.freshness.best_age} · ${freshest.freshness.best_state}` : "no recent", freshest ? "deck-watch" : "deck-neutral"));
    wrap.appendChild(deckTile("Analog", bestAnalog ? bestAnalog.ticker : "—", bestAnalog ? `${pct(bestAnalog.analog.fwd21_mean, 1)} · ${bestAnalog.analog.verdict}` : "no sample", bestAnalog ? (bestAnalog.analog.fwd21_mean > 0 ? "deck-cool" : "deck-risk") : "deck-neutral"));
    wrap.appendChild(deckTile("Path", cleanPath ? cleanPath.ticker : "—", cleanPath ? `${cleanPath.analog.path_risk} · MAE ${pct(cleanPath.analog.mae21_p10, 0)}` : "no sample", cleanPath ? (cleanPath.analog.path_risk_score >= 70 ? "deck-cool" : "deck-risk") : "deck-neutral"));
    wrap.appendChild(deckTile(
      "Closest",
      closest ? closest.r.ticker : "—",
      closest?.watch ? `${closest.watch.label} ${closest.watch.count}/${closest.watch.total} · gap ${fmt(closest.watch.gap, 2)}` : "no candidate",
      closest ? "deck-watch" : "deck-neutral"
    ));
    wrap.appendChild(deckTile("Momentum", hotRsi ? hotRsi.ticker : "—", hotRsi ? `Δ5d ${fmt(hotRsi.rsi9_delta_5d, 1)}` : "no RSI data", hotRsi ? (hotRsi.rsi9_delta_5d > 0 ? "deck-cool" : "deck-risk") : "deck-neutral"));
    wrap.appendChild(deckTile("Data", `${overlay}/${rows.length}`, `${realtime} RT · ${rows.length - realtime} delayed`, overlay === rows.length ? "deck-cool" : "deck-watch"));
    wrap.appendChild(deckTile("Coverage", String(Object.keys(m.region_counts || {}).length || "—"), regionText, "deck-neutral"));
  }

  // ----- Stock scanner layer -----
  function stockTierRank(tier) {
    return {
      TIER1: 0,
      TIER1_WARNING: 1,
      NO_TRADE: 2,
      WATCHLIST_PLUS: 2,
      RESEARCH_ONLY: 2,
      BLOCKED: 4,
      UNTESTED: 5,
    }[tier] ?? 9;
  }

  function stockTierLabel(tier) {
    return {
      TIER1: "Tier 1",
      TIER1_WARNING: "Tier 1 varning",
      NO_TRADE: "No trade",
      WATCHLIST_PLUS: "No trade",
      RESEARCH_ONLY: "No trade",
      BLOCKED: "Blocked",
      UNTESTED: "No trade",
    }[tier] || tier || "—";
  }

  function stockTierClass(tier) {
    return {
      TIER1: "stock-tier1",
      TIER1_WARNING: "stock-tier-warning",
      NO_TRADE: "stock-research",
      WATCHLIST_PLUS: "stock-research",
      RESEARCH_ONLY: "stock-research",
      BLOCKED: "stock-blocked",
      UNTESTED: "stock-research",
    }[tier] || "stock-research";
  }

  function stockActionClass(action) {
    return {
      TRADE: "stock-action-live",
      LIVE_REVIEW: "stock-action-review",
      NO_TRADE: "stock-action-blocked",
      WAIT_95_SIGNAL: "stock-action-paper",
      WAIT_SIGNAL: "stock-action-paper",
      PAPER_TRACK: "stock-action-blocked",
      BLOCKED: "stock-action-blocked",
      RESEARCH_ONLY: "stock-action-blocked",
    }[action] || "stock-action-research";
  }

  function stockActionLabel(action) {
    return {
      TRADE: "Trade",
      LIVE_REVIEW: "Review",
      NO_TRADE: "No trade",
      WAIT_95_SIGNAL: "No trade",
      WAIT_SIGNAL: "Watch",
      PAPER_TRACK: "No trade",
      BLOCKED: "Blocked",
      RESEARCH_ONLY: "No trade",
    }[action] || action || "No trade";
  }

  function primeTierLabel(tier) {
    return {
      A_PLUS_TRADE_CANDIDATE: "A+",
      A_WATCH: "A watch",
      B_WATCH: "B watch",
      EVENT_BLOCKED: "Event block",
      FAILED_STRUCTURE: "Failed",
    }[tier] || tier || "Prime —";
  }

  function stockPctClass(value, strongAt) {
    if (value === null || value === undefined || Number.isNaN(value)) return "dim";
    const threshold = strongAt ?? 0.05;
    if (value >= threshold) return "z-strong-pos";
    if (value > 0) return "z-pos";
    if (value <= -threshold) return "z-strong-neg";
    if (value < 0) return "z-neg";
    return "dim";
  }

  function stockRowByTicker(ticker) {
    if (!ticker) return null;
    return (stockData.stocks || []).find((r) => r.ticker === ticker) ||
      (stockData.current || []).find((r) => r.ticker === ticker) ||
      null;
  }

  function stockCurrentRowsFor(ticker) {
    return (stockData.current || [])
      .filter((r) => r.ticker === ticker)
      .sort((a, b) =>
        actionRankForSort(a.action) - actionRankForSort(b.action) ||
        String(b.date || "").localeCompare(String(a.date || ""))
      );
  }

  function actionRankForSort(action) {
    return { TRADE: 0, LIVE_REVIEW: 0, WAIT_95_SIGNAL: 1, WAIT_SIGNAL: 1, PAPER_TRACK: 2, NO_TRADE: 3, RESEARCH_ONLY: 3, BLOCKED: 4 }[action] ?? 9;
  }

  function stockLifecycleClass(lifecycle) {
    const state = lifecycle?.state || "";
    return {
      NEW: "stock-life-new",
      RETURNED: "stock-life-returned",
      UPGRADED: "stock-life-upgraded",
      DOWNGRADED: "stock-life-downgraded",
      PERSISTING: "stock-life-persisting",
      FADED: "stock-life-faded",
      ARCHIVE: "stock-life-archive",
    }[state] || "stock-life-archive";
  }

  function stockThesisList(title, items, fallback) {
    const clean = (Array.isArray(items) ? items : []).filter(Boolean);
    return el("div", { className: "stock-thesis-section" }, [
      el("div", { className: "stock-thesis-section-title", text: title }),
      clean.length
        ? el("ul", null, clean.slice(0, 5).map((item) => el("li", { text: item })))
        : el("div", { className: "detail-empty", text: fallback || "—" }),
    ]);
  }

  function selectStockTicker(ticker) {
    if (!ticker) return;
    state.selectedTicker = ticker;
    state.selectedSource = "stock";
    renderStockGrid();
    renderGrid();
    renderDetail();
    renderAlignmentPanel();
  }

  function renderStockThesisDetail(stock) {
    const thesis = stock.thesis || {};
    const lifecycle = stock.lifecycle || thesis.lifecycle || {};
    const currentRows = stockCurrentRowsFor(stock.ticker).slice(0, 4);
    const probability = thesis.subjective_probability ? `${thesis.subjective_probability}% thesis` : "open thesis";
    const probDelta = thesis.probability_delta ?? lifecycle.probability_delta;
    const deltaText = probDelta ? `${probDelta > 0 ? "+" : ""}${probDelta}p` : "flat";
    const qtParts = [stock.qt_label, stock.qt_phase, stock.qt_wait_label, stock.qt_confirmation].filter(Boolean);
    const postEntry = stock.post_entry_monitor || thesis.post_entry_monitor || {};
    const stats = [
      stock.fwd21_mean != null ? `fwd21 ${pct(stock.fwd21_mean, 1)}` : null,
      stock.hit != null ? `hit ${pct(stock.hit, 0)}` : null,
      stock.mae_p10 != null ? `MAE p10 ${pct(stock.mae_p10, 0)}` : null,
      stock.lift != null ? `lift ${pct(stock.lift, 1)}` : null,
    ].filter(Boolean).join(" · ");

    const node = el("div", { className: "stock-thesis-detail" }, [
      el("div", { className: "stock-thesis-topline" }, [
        el("div", null, [
          el("div", { className: "stock-thesis-kicker", text: "Trade Thesis" }),
          el("div", { className: "stock-thesis-title", text: `${stock.ticker || ""} · ${stock.name || ""}` }),
          el("div", { className: "stock-thesis-subtitle", text: `${thesis.stance || stockActionLabel(stock.action)} · ${stock.best_signal || stock.current_signal || ""} · ${stock.group || stock.market || ""}` }),
        ]),
        el("div", { className: "stock-thesis-badges" }, [
          el("span", { className: "stock-thesis-prob", text: probability }),
          el("span", { className: `stock-life ${stockLifecycleClass(lifecycle)}`, text: lifecycle.label || "Open" }),
          el("span", { className: `stock-status ${stockActionClass(stock.action)}`, text: stockActionLabel(stock.action) }),
        ]),
      ]),
      el("div", { className: "stock-thesis-read" }, [
        el("div", null, [
          el("span", { text: "Feel" }),
          el("strong", { text: thesis.feel || "Open read" }),
        ]),
        el("div", null, [
          el("span", { text: "Lifecycle" }),
          el("strong", { text: `${lifecycle.label || "Open"} · seen ${lifecycle.seen_count || 0} · ${deltaText}` }),
        ]),
        el("div", null, [
          el("span", { text: "QT" }),
          el("strong", { text: qtParts.join(" · ") || "—" }),
        ]),
        el("div", null, [
          el("span", { text: "Stats" }),
          el("strong", { text: stats || thesis.quant_context || "—" }),
        ]),
        el("div", null, [
          el("span", { text: "Post-entry" }),
          el("strong", { text: postEntry.note || postEntry.label || "No post-entry data" }),
        ]),
      ]),
      el("div", { className: "stock-thesis-grid" }, [
        stockThesisList("Bull case", thesis.bull_case, "No bull case yet."),
        stockThesisList("Bear case", thesis.bear_case, "No bear case yet."),
        stockThesisList("Wait for", thesis.wait_for, "No wait condition."),
        stockThesisList("Buy if", thesis.buy_if, "No buy condition."),
      ]),
    ]);

    if (currentRows.length) {
      node.appendChild(el("div", { className: "stock-thesis-tape" }, [
        el("div", { className: "stock-thesis-section-title", text: "Signal tape" }),
        ...currentRows.map((row) => {
          const rowLife = row.lifecycle || row.thesis?.lifecycle || {};
          const rowProb = row.thesis?.subjective_probability ? `${row.thesis.subjective_probability}%` : "open";
          return el("div", { className: "stock-thesis-tape-row" }, [
            el("span", { className: "ticker", text: row.signal || row.current_signal || "signal" }),
            el("span", { text: `${rowProb} · ${row.thesis?.stance || stockActionLabel(row.action)}` }),
            el("span", { className: `stock-life ${stockLifecycleClass(rowLife)}`, text: rowLife.label || row.date || "—" }),
          ]);
        }),
      ]));
    }

    return node;
  }

  function activeStockRows() {
    let rows = ((stockData.stocks && stockData.stocks.length) ? stockData.stocks : stockData.combos || []).slice();
    if (state.stockFilterTier) rows = rows.filter((r) => r.tier === state.stockFilterTier);
    if (state.stockFilterMarket) rows = rows.filter((r) => r.market === state.stockFilterMarket);
    if (state.stockFilterAction) rows = rows.filter((r) => r.action === state.stockFilterAction);
    if (state.stockFilterSearch) {
      const q = state.stockFilterSearch.toLowerCase();
      rows = rows.filter((r) =>
        String(r.ticker || "").toLowerCase().includes(q) ||
        String(r.name || "").toLowerCase().includes(q) ||
        String(r.group || "").toLowerCase().includes(q) ||
        String(r.best_signal || r.signal || "").toLowerCase().includes(q)
      );
    }
    rows.sort((a, b) =>
      actionRankForSort(a.action) - actionRankForSort(b.action) ||
      stockTierRank(a.tier) - stockTierRank(b.tier) ||
      (b.quality_score ?? -1) - (a.quality_score ?? -1) ||
      (b.lift ?? -99) - (a.lift ?? -99) ||
      (b.fwd21_mean ?? -99) - (a.fwd21_mean ?? -99) ||
      String(a.ticker || "").localeCompare(String(b.ticker || ""))
    );
    return rows;
  }

  function renderStockDeck() {
    const wrap = document.getElementById("stock-deck");
    const guard = document.getElementById("stock-guard");
    if (!wrap || !guard) return;
    clear(wrap);
    const m = stockData.meta || {};
    const trade = Number(m.current_trade || 0);
    const review = Number(m.current_live_review || 0);
    const watch = Number(m.current_watch || 0);
    const eventBlocked = Number(m.current_event_blocked || 0);
    const live = trade + review;
    const noTrade = m.current_no_trade ?? 0;
    const stockMemory = m.signal_memory || {};
    const stockSeenToday = Number(stockMemory.today_count || 0);
    const stockFadedToday = Number(stockMemory.faded_today_count || 0);
    const thesisUp = Number(m.thesis_upgraded || stockMemory.upgraded_count || 0);
    const thesisDown = Number(m.thesis_downgraded || stockMemory.downgraded_count || 0);
    const thesisReturned = Number(m.thesis_returned || stockMemory.returned_count || 0);
    const postOpen = Number(m.post_entry_open || 0);
    const postMatured = Number(m.post_entry_matured || 0);
    guard.className = trade > 0 ? "stock-guard live" : review > 0 ? "stock-guard review" : "stock-guard safe";
    guard.textContent = trade > 0
      ? `${trade} TRADE enligt aktiestrategin.`
      : review > 0
        ? `${review} REVIEW: quant-gate passerad, men nyheter/spread måste kollas manuellt.`
        : "0 capital-ready trades. Thesis-listan visar öppna cases, inte avfärdanden.";
    wrap.appendChild(deckTile("Capital", String(trade + review), `${watch} thesis · ${eventBlocked} blocked`, review > 0 ? "deck-watch" : "deck-cool"));
    wrap.appendChild(deckTile("QT Prime", String(m.mlpb_prime_a_plus || 0), `${m.mlpb_prime_a_watch || 0} A-watch · ${m.mlpb_prime_b_watch || 0} B-watch`, m.mlpb_prime_a_plus ? "deck-hot" : m.mlpb_prime_a_watch ? "deck-watch" : "deck-neutral"));
    wrap.appendChild(deckTile("Seen stocks", String(stockSeenToday), `${stockFadedToday} faded`, stockSeenToday ? "deck-watch" : "deck-neutral"));
    const liveThesis = (stockData.current || []).filter((r) => ["WAIT_SIGNAL", "WAIT_95_SIGNAL", "LIVE_REVIEW", "TRADE"].includes(r.action)).length;
    wrap.appendChild(deckTile("Thesis", String(liveThesis), "open trade reads", liveThesis ? "deck-watch" : "deck-neutral"));
    wrap.appendChild(deckTile("Case Δ", `${thesisUp}/${thesisDown}`, `${thesisReturned} returned · ${stockFadedToday} faded`, thesisUp ? "deck-watch" : thesisDown ? "deck-hot" : "deck-neutral"));
    wrap.appendChild(deckTile("Post-entry", String(postOpen), `${postMatured} matured`, postOpen ? "deck-watch" : "deck-neutral"));
    wrap.appendChild(deckTile("95 Stocks", String(m.high_quality_stocks || 0), "quality only, not a trade", m.high_quality_stocks ? "deck-cool" : "deck-neutral"));
    const decision = trade > 0 ? "TRADE" : review > 0 ? "REVIEW" : "NO TRADE";
    const decisionSub = trade > 0 ? "binary stock layer" : review > 0 ? "manual checks required" : "binary stock layer";
    wrap.appendChild(deckTile("Decision", decision, decisionSub, trade > 0 ? "deck-hot" : review > 0 ? "deck-watch" : "deck-neutral"));
    wrap.appendChild(deckTile("Tier 1", String(m.tier1 || 0), `${m.tier1_warning || 0} warning combos`, "deck-cool"));
    wrap.appendChild(deckTile("Strict OOS", pct(m.strict_oos_mean, 2), `${pct(m.strict_oos_net_100bps, 2)} net 100bps`, "deck-cool"));
    wrap.appendChild(deckTile("Universe", String(m.stocks || 0), `${m.combos || 0} combos · ${m.blocked || 0} blocked`, "deck-neutral"));
  }

  function renderStockCurrent() {
    const wrap = document.getElementById("stock-current");
    if (!wrap) return;
    clear(wrap);
    const rows = (stockData.current || [])
      .slice()
      .sort((a, b) =>
        ({ TRADE: 0, LIVE_REVIEW: 0, WAIT_SIGNAL: 1, WAIT_95_SIGNAL: 1, PAPER_TRACK: 2, NO_TRADE: 3, BLOCKED: 4, RESEARCH_ONLY: 3 }[a.action] ?? 9) -
        ({ TRADE: 0, LIVE_REVIEW: 0, WAIT_SIGNAL: 1, WAIT_95_SIGNAL: 1, PAPER_TRACK: 2, NO_TRADE: 3, BLOCKED: 4, RESEARCH_ONLY: 3 }[b.action] ?? 9) ||
        String(b.date || "").localeCompare(String(a.date || ""))
      )
      .slice(0, 6);
    wrap.appendChild(el("div", { className: "stock-current-title", text: "Färska raw-signaler" }));
    if (!rows.length) {
      wrap.appendChild(el("div", { className: "detail-empty", text: "Inga färska aktiesignaler." }));
      return;
    }
    const list = el("div", { className: "stock-current-list" });
    rows.forEach((r) => {
      const gates = (r.gates || []).map((g) => g.gate).join("+") || "no gate";
      const primeText = primeTierLabel(r.prime_tier);
      const qtText = [r.qt_phase, r.qt_wait_label, r.qt_confirmation].filter(Boolean).join(" · ");
      const thesis = r.thesis || {};
      const nextStep = Array.isArray(thesis.wait_for) && thesis.wait_for.length ? ` · ${thesis.wait_for[0]}` : "";
      const probability = thesis.subjective_probability ? `${thesis.subjective_probability}% thesis` : "open thesis";
      const evidence = [`Q${r.quality_score || 0}`, primeText, stockTierLabel(r.tier), gates].filter(Boolean).join(" · ");
      const rowLife = r.lifecycle || thesis.lifecycle || {};
      const card = el("div", {
        className: `stock-current-card ${stockActionClass(r.action)}`,
        dataset: { ticker: r.ticker },
        attrs: { tabindex: "0", role: "button", "aria-label": `Open thesis for ${r.ticker}` },
      }, [
        el("div", { className: "stock-current-main" }, [
          el("span", { className: "ticker", text: r.ticker }),
          el("span", { className: "stock-current-signal", text: r.signal }),
          el("span", { className: "stock-thesis-prob", text: probability }),
          el("span", { className: `stock-life ${stockLifecycleClass(rowLife)}`, text: rowLife.label || "Open" }),
          el("span", { className: `stock-status ${stockActionClass(r.action)}`, text: stockActionLabel(r.action) }),
        ]),
        el("div", { className: "stock-current-sub", text: `${thesis.stance || "open read"} · ${r.date} · ${r.group || r.market || ""}${qtText ? ` · ${qtText}` : ""}${nextStep}` }),
        el("div", { className: "stock-current-evidence", text: `Evidence: ${evidence}` }),
      ]);
      card.addEventListener("click", (e) => {
        e.stopPropagation();
        selectStockTicker(r.ticker);
      });
      card.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        selectStockTicker(r.ticker);
      });
      list.appendChild(card);
    });
    wrap.appendChild(list);
  }

  function renderStockGrid() {
    const tbody = document.getElementById("stock-tbody");
    if (!tbody) return;
    const rows = activeStockRows();
    const frag = document.createDocumentFragment();
    if (!rows.length) {
      const tr = el("tr", { className: "stock-row stock-empty-row" });
      tr.appendChild(el("td", {
        className: "detail-empty",
        text: "Inga aktie-combos matchar filtret.",
        attrs: { colspan: "12" },
      }));
      clear(tbody);
      tbody.appendChild(tr);
      return;
    }
    rows.forEach((r) => {
      const tr = el("tr", {
        className: `stock-row ${stockTierClass(r.tier)} ${stockActionClass(r.action)}`,
        dataset: { ticker: r.ticker },
        attrs: { tabindex: "0", "aria-selected": state.selectedTicker === r.ticker && state.selectedSource === "stock" ? "true" : "false" },
      });
      if (state.selectedTicker === r.ticker && state.selectedSource === "stock") tr.classList.add("selected");
      tr.addEventListener("click", (e) => {
        e.stopPropagation();
        selectStockTicker(r.ticker);
      });
      tr.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        selectStockTicker(r.ticker);
      });
      const currentText = r.current_signal ? `${r.current_signal} · ${primeTierLabel(r.prime_tier)} · ${r.qt_phase || r.current_gate || "raw"} · ${r.current_date || ""}` : "wait";
      const thesis = r.thesis || {};
      const lifecycle = r.lifecycle || thesis.lifecycle || {};
      const blockers = thesis.stance ? `${lifecycle.label ? `${lifecycle.label} · ` : ""}${thesis.stance}${thesis.subjective_probability ? ` · ${thesis.subjective_probability}%` : ""} · ${(thesis.bear_case || []).slice(0, 2).join(" · ")}` : (r.blockers || r.quality_blockers || []).join(" · ");
      [
        el("td", { className: "left ticker", text: r.ticker || "" }),
        el("td", { className: "left name", text: r.name || "" }),
        el("td", null, [el("span", { className: `stock-status ${stockActionClass(r.action)}`, text: stockActionLabel(r.action) })]),
        el("td", null, [el("span", { className: `stock-pill ${stockTierClass(r.tier)}`, text: stockTierLabel(r.tier) })]),
        el("td", { text: r.best_signal || r.signal || "" }),
        el("td", null, [el("span", { className: `stock-quality q${r.quality_score >= 95 ? "95" : r.quality_score >= 90 ? "90" : "low"}`, text: `Q${r.quality_score || 0}` })]),
        el("td", { className: stockPctClass(r.fwd21_mean), text: pct(r.fwd21_mean, 1) }),
        el("td", { className: stockPctClass(r.lift, 0.03), text: pct(r.lift, 1) }),
        el("td", { className: stockPctClass((r.hit || 0) - 0.5, 0.12), text: pct(r.hit, 0) }),
        el("td", { className: (r.mae_p10 != null && r.mae_p10 <= -0.22) ? "z-neg" : "dim", text: pct(r.mae_p10, 0) }),
        el("td", { className: r.current_signal ? "z-pos" : "dim", text: currentText }),
        el("td", { className: "left stock-blockers", text: blockers || "—", attrs: { title: blockers || "" } }),
      ].forEach((td) => tr.appendChild(td));
      frag.appendChild(tr);
    });
    clear(tbody);
    tbody.appendChild(frag);
  }

  function renderStockPanel() {
    renderStockDeck();
    renderStockCurrent();
    renderStockGrid();
  }

  // ----- Signal feed -----
  function feedItem(ticker, valText, valClass, title) {
    const node = el("div", { className: "item" }, [
      el("span", { className: "ticker", text: ticker, dataset: { jump: ticker } }),
      el("span", { className: `val ${valClass || ""}`, text: valText }),
    ]);
    if (title) node.setAttribute("title", title);
    return node;
  }

  function feedSection(title, items, badge) {
    const sec = el("div", { className: "section" });
    const titleEl = el("div", { className: "section-title", text: title });
    if (badge) titleEl.appendChild(badge);
    sec.appendChild(titleEl);
    if (!items || items.length === 0) {
      sec.appendChild(el("div", { className: "none", text: "None" }));
    } else {
      items.forEach((it) => sec.appendChild(it));
    }
    return sec;
  }

  function signalMemoryClass(item) {
    if (!item || !item.active) return "action-muted";
    if (item.signal_key === "cap") return "flag-cap";
    if (item.signal_key === "pb126") return "flag-pb126";
    return "flag-pb";
  }

  function renderFeed() {
    const rows = (data.rows || []).filter((r) => r.status !== "SKIPPED");
    const cap = rows.filter((r) => r.cap === true);
    const pb = rows.filter((r) => r.pb === true);
    const pb126 = rows.filter((r) => r.pb126 === true);
    const memory = data.meta?.signal_memory || {};
    const memoryToday = Array.isArray(memory.today) ? memory.today : [];
    const decisions = rows
      .map((r) => ({ r, action: actionFor(r), quality: rowQualityScore(r) }))
      .filter((x) => x.action.priority < 99)
      .sort((a, b) => a.action.priority - b.action.priority || b.quality - a.quality)
      .slice(0, 6);
    const regimeEdges = rows
      .filter((r) => regimeLabels(r).length > 0)
      .sort((a, b) => regimeScore(a) - regimeScore(b) || (nearScore(a) ?? 999) - (nearScore(b) ?? 999))
      .slice(0, 6);
    const analogTailwinds = rows
      .filter((r) => r.analog && r.analog.status === "OK")
      .sort((a, b) => b.analog.fwd21_mean - a.analog.fwd21_mean)
      .slice(0, 6);
    const freshSignals = rows
      .filter((r) => r.freshness && r.freshness.status === "OK")
      .sort((a, b) => b.freshness.score - a.freshness.score || a.freshness.best_age - b.freshness.best_age)
      .slice(0, 6);
    const pathRisks = rows
      .filter((r) => r.analog && r.analog.status === "OK" && ["HIGH", "EXTREME"].includes(r.analog.path_risk))
      .sort((a, b) => a.analog.path_risk_score - b.analog.path_risk_score || b.analog.fwd21_mean - a.analog.fwd21_mean)
      .slice(0, 6);

    const scored = rows
      .map((r) => {
        const parts = [r.ri_rvol, r.ri_rsi, r.ri_dist252].filter((x) => x !== null && x !== undefined);
        if (parts.length < 3) return { r, score: NaN };
        return { r, score: Math.min(...parts) };
      })
      .filter((x) => !Number.isNaN(x.score))
      .sort((a, b) => a.score - b.score)
      .slice(0, 5);

    const pbCandidates = rows
      .filter((r) => r.bull_stack === true && r.ri_rsi !== null && r.ri_rsi !== undefined)
      .sort((a, b) => a.ri_rsi - b.ri_rsi)
      .slice(0, 5);

    const container = document.getElementById("signal-feed");
    clear(container);

    container.appendChild(feedSection("Seen today (signal tape)",
      memoryToday.slice(0, 8).map((item) => feedItem(
        item.ticker,
        `${item.label} · ${item.active ? "active" : "faded"} · ${timeLabel(item.first_seen_at)}`,
        signalMemoryClass(item),
        [
          `first ${timeLabel(item.first_seen_at)}`,
          `last ${timeLabel(item.last_seen_at)}`,
          item.last_entry != null ? `entry ${item.last_entry} ${item.currency || ""}` : "",
          item.intraday_status || "",
        ].filter(Boolean).join(" · ")
      ))
    ));
    container.appendChild(feedSection("Decision queue",
      decisions.map((x) => feedItem(
        x.r.ticker,
        `${x.action.label} · q${x.quality}`,
        x.action.cls,
        x.action.reason
      ))
    ));
    container.appendChild(feedSection("95% regime edge",
      regimeEdges.map((r) => {
        const primary = regimePrimary(r);
        return feedItem(
          r.ticker,
          primary.label,
          regimeClass(primary.tone),
          [primary.verdict, primary.reason, primary.stats].filter(Boolean).join(" · ")
        );
      })
    ));
    container.appendChild(feedSection("Signal freshness (decay)",
      freshSignals.map((r) => feedItem(
        r.ticker,
        `${r.freshness.best_type} D+${r.freshness.best_age} · ${r.freshness.best_state}`,
        freshnessClass(r.freshness.best_state),
        `freshness score ${r.freshness.score} · lookback ${r.freshness.lookback}d`
      ))
    ));
    container.appendChild(feedSection("Analog context (not a filter)",
      analogTailwinds.map((r) => feedItem(
        r.ticker,
        `${r.analog.verdict} · ${pct(r.analog.fwd21_mean, 1)} · n${r.analog.n}`,
        analogClass(r.analog.verdict),
        `hit ${(r.analog.fwd21_hit_rate * 100).toFixed(0)}% · MAE p10 ${pct(r.analog.mae21_p10, 1)}`
      ))
    ));
    container.appendChild(feedSection("Path-risk flags",
      pathRisks.map((r) => feedItem(
        r.ticker,
        `${r.analog.path_risk} · MAE ${pct(r.analog.mae21_p10, 1)}`,
        pathRiskClass(r.analog.path_risk),
        `shake5 ${pct(r.analog.shakeout_5pct_rate, 0)} · fwd21 ${pct(r.analog.fwd21_mean, 1)}`
      ))
    ));
    container.appendChild(feedSection("Active CAP signals",
      cap.map((r) => feedItem(r.ticker, "CAP", "flag-cap")),
      sigBadge("cap")
    ));
    container.appendChild(feedSection("Active PB signals (SMA21)",
      pb.map((r) => feedItem(r.ticker, "PB", "flag-pb")),
      sigBadge("pb")
    ));
    container.appendChild(feedSection("Active PB signals (SMA126)",
      pb126.map((r) => feedItem(r.ticker, "PB126", "flag-pb")),
      sigBadge("pb126")
    ));
    container.appendChild(feedSection("Closest to capitulation",
      scored.map((s) => feedItem(s.r.ticker, `min ri=${fmt(s.score, 2)}`, zClass(s.score)))
    ));
    container.appendChild(feedSection("Bull-stack near pullback",
      pbCandidates.map((r) => feedItem(r.ticker, `ri_rsi=${fmt(r.ri_rsi, 2)}`, zClass(r.ri_rsi)))
    ));
    const caveats = rows
      .map((r) => ({ r, caveats: caveatsFor(r), quality: rowQualityScore(r) }))
      .filter((x) => x.caveats.length > 0)
      .sort((a, b) => a.quality - b.quality || a.r.ticker.localeCompare(b.r.ticker))
      .slice(0, 6);
    container.appendChild(feedSection("Data / execution caveats",
      caveats.map((x) => feedItem(x.r.ticker, x.caveats.slice(0, 2).join(" · "), "action-muted", x.caveats.join(" · ")))
    ));
  }

  // ----- Detail panel -----
  function renderDetail() {
    const wrap = document.getElementById("detail-panel");
    clear(wrap);
    if (!state.selectedTicker) {
      wrap.appendChild(el("div", { className: "detail-empty", text: "Click a row in the scanner grid for full instrument detail." }));
      return;
    }
    const stockR = stockRowByTicker(state.selectedTicker);
    const r = (data.rows || []).find((x) => x.ticker === state.selectedTicker);
    if (stockR && (state.selectedSource === "stock" || !r || state.viewMode === "stocks")) {
      wrap.appendChild(renderStockThesisDetail(stockR));
      if (!r || state.selectedSource === "stock" || state.viewMode === "stocks") return;
    }
    if (!r) {
      wrap.appendChild(el("div", { className: "detail-empty", text: "Ticker not found." }));
      return;
    }
    const smaCls = (sma) => (r.close != null && sma != null && r.close > sma ? "z-pos" : "z-neg");
    const quality = rowQualityScore(r);
    const action = actionFor(r);
    const caveats = caveatsFor(r);
    const analog = r.analog || {};
    const freshness = r.freshness || {};
    const caseEngine = r.case_engine || {};
    const qtPrime = r.qt_prime || {};
    const primaryRegime = regimePrimary(r);

    const header = el("div", { className: "detail-header" }, [
      el("div", { className: "t", text: r.ticker }),
      el("div", { className: "n", text: `${r.name || ""} — ${r.region || ""} — ${r.bars || "?"} bars` }),
      el("div", { className: "badge-row" }, [
        el("span", { className: `action-pill ${action.cls}`, text: action.label, attrs: { title: action.reason } }),
        el("span", { className: `quality-pill ${qualityClass(quality)}`, text: `Q ${quality}` }),
        ...caveats.slice(0, 4).map((c) => el("span", { className: "caveat-pill", text: c })),
      ]),
    ]);
    wrap.appendChild(header);
    wrap.appendChild(renderAlignmentBoard(r));

    const dl = el("dl");
    const pairs = [
      ["Action", `${action.label} · ${action.reason}`, action.cls],
      ["Case engine", caseEngine.state ? `${caseEngine.state} · ${caseEngine.label || ""}` : "—", caseEngine.state === "TRADE_CANDIDATE" ? "action-go" : caseEngine.state === "WAIT_REPAIR_NEEDED" ? "action-watch" : "action-muted"],
      ["Case next", Array.isArray(caseEngine.next) ? caseEngine.next.join(" · ") : "—", caseEngine.blockers?.length ? "delay-delayed-text" : null],
      ["QT Prime", qtPrime.label ? `${qtPrime.label} · ${qtPrime.phase || "—"} · ${qtPrime.wait_label || "—"}` : "—", qtPrime.label === "QT_SUPPORT" ? "z-pos" : qtPrime.label === "QT_BLOCK" ? "z-neg" : "z-neutral"],
      ["QT z / Δ", qtPrime.z != null ? `${fmt(qtPrime.z, 2)} / ${fmt(qtPrime.z_delta, 2)} · ${qtPrime.confirmation || "—"}` : "—", zClass(qtPrime.z_delta)],
      ["Quality", `${quality}/100`, qualityClass(quality)],
      ["Regime 95", primaryRegime ? `${primaryRegime.label} · ${primaryRegime.verdict}` : "NO 95 REGIME", primaryRegime ? regimeClass(primaryRegime.tone) : "regime-neutral"],
      ["Signal freshness", freshness.status === "OK" ? `${freshness.best_type} D+${freshness.best_age} · ${freshness.best_state} · score ${freshness.score}` : (freshness.status || "—"), freshnessClass(freshness.best_state)],
      ["Analog context", analog.status === "OK" ? `${analog.verdict} · ${analog.confidence}` : (analog.status || "—"), analogClass(analog.verdict)],
      ["Analog fwd21", analog.status === "OK" ? `${pct(analog.fwd21_mean, 1)} mean · ${(analog.fwd21_hit_rate * 100).toFixed(0)}% hit · n${analog.n}` : "—", analogClass(analog.verdict)],
      ["Path risk", analog.status === "OK" ? `${analog.path_risk} · score ${analog.path_risk_score}` : "—", pathRiskClass(analog.path_risk)],
      ["Path MFE/MAE", analog.status === "OK" ? `MFE med ${pct(analog.mfe21_median, 1)} · MAE p10 ${pct(analog.mae21_p10, 1)} · delay ${fmt(analog.best_entry_delay_median, 1)}d` : "—", pathRiskClass(analog.path_risk)],
      ["Shakeout", analog.status === "OK" ? `>5% ${pct(analog.shakeout_5pct_rate, 0)} · >10% ${pct(analog.shakeout_10pct_rate, 0)}` : "—", pathRiskClass(analog.path_risk)],
      ["Close", fmt(r.close, 3), null],
      ["Open gap", r.open_gap_pct == null ? "—" : `${fmt(r.open_gap_pct, 2)}% · ${r.gap_risk || "OK"}`, r.gap_risk === "GAP_BLOCK_REVIEW" ? "delay-delayed-text" : (r.gap_risk === "GAP_REVIEW" ? "action-watch" : null)],
      ["Data", `${r.data_latency_label || "?"} · ${compactTime(r.quote_time || r.last_close_date)}`, isDelayed(r) ? "delay-delayed-text" : "delay-rt-text"],
      ["Source", r.price_source || "—", null],
      ["Overlay", r.intraday_status || "—", null],
      ["Execution", r.execution_ticker ? `${r.execution_ticker} · ${r.execution_market || ""} · ${r.execution_broker || ""}` : "same/unknown", r.execution_ticker ? "delay-realtime-text" : null],
      ["Trade checks", Array.isArray(r.manual_checks) ? r.manual_checks.join(" · ") : (r.trade_readiness || "—"), r.trade_readiness === "MANUAL_CHECK_REQUIRED" ? "delay-delayed-text" : null],
      ["Exchange", r.exchange || "—", null],
      ["Currency", r.currency || "—", r.currency === "USD" ? "delay-delayed-text" : null],
      ["ISIN", r.isin || "—", null],
      ["TER", r.ter != null ? `${Number(r.ter).toFixed(2)}%` : "—", Number(r.ter) > 0.3 ? "delay-delayed-text" : null],
      ["SMA21", fmt(r.sma21, 2), smaCls(r.sma21)],
      ["SMA52", fmt(r.sma52, 2), smaCls(r.sma52)],
      ["SMA126", fmt(r.sma126, 2), smaCls(r.sma126)],
      ["SMA252", fmt(r.sma252, 2), smaCls(r.sma252)],
      ["RSI9", fmt(r.rsi9, 1), null],
      ["RSI9 Δ1d", fmt(r.rsi9_delta_1d, 2), zClass(r.rsi9_delta_1d)],
      ["RSI9 Δ5d", fmt(r.rsi9_delta_5d, 2), zClass(r.rsi9_delta_5d)],
      ["RSI21", fmt(r.rsi21, 1), null],
      ["RSI21 Δ5d", fmt(r.rsi21_delta_5d, 2), zClass(r.rsi21_delta_5d)],
      ["RSI21 Δ21d", fmt(r.rsi21_delta_21d, 2), zClass(r.rsi21_delta_21d)],
      ["RSI63", fmt(r.rsi63, 1), null],
      ["RSI63 Δ21d", fmt(r.rsi63_delta_21d, 2), zClass(r.rsi63_delta_21d)],
      ["RSI63 Δ42d", fmt(r.rsi63_delta_42d, 2), zClass(r.rsi63_delta_42d)],
      ["DirRVOL63", fmt(r.dirvol63, 3), zClass(r.dirvol63)],
      ["DirLogVolZ63", fmt(r.dir_logvolz63, 3), zClass(r.dir_logvolz63)],
      ["DirRVOL Δ1d", fmt(r.dir_rvol_delta_1d, 3), zClass(r.dir_rvol_delta_1d)],
      ["DirRVOL Δ5d", fmt(r.dir_rvol_delta_5d, 3), zClass(r.dir_rvol_delta_5d)],
      ["Volume trust", r.volume_trust || "—", r.volume_trust === "BLOCKED" ? "delay-delayed-text" : (r.volume_trust === "PROXY" ? "delay-realtime-text" : null)],
      ["Volume source", r.volume_source_ticker || r.ticker || "—", r.volume_trust === "PROXY" ? "delay-realtime-text" : null],
      ["Volume policy", r.volume_reason || r.threshold_mode || "—", r.volume_trust === "BLOCKED" ? "delay-delayed-text" : null],
      ["dist_sma21", `${fmt(r.dist_sma21, 2)}%`, null],
      ["dist_sma252", `${fmt(r.dist_sma252, 2)}%`, null],
      ["dist_sma126", `${fmt(r.dist_sma126, 2)}%`, null],
      ["Regim", regimLabel(r).label, regimLabel(r).cls],
      ["ri_rvol", fmt(r.ri_rvol, 3), zClass(r.ri_rvol)],
      ["ri_rsi", fmt(r.ri_rsi, 3), zClass(r.ri_rsi)],
      ["ri_rsi21", fmt(r.ri_rsi21, 3), zClass(r.ri_rsi21)],
      ["ri_rsi63", fmt(r.ri_rsi63, 3), zClass(r.ri_rsi63)],
      ["ri_rsi63_d42", fmt(r.ri_rsi63_delta_42d, 3), zClass(r.ri_rsi63_delta_42d)],
      ["ri_logvolz", fmt(r.ri_logvolz, 3), zClass(r.ri_logvolz)],
      ["ri_rvol_d5", fmt(r.ri_rvol_delta_5d, 3), zClass(r.ri_rvol_delta_5d)],
      ["ri_dist252", fmt(r.ri_dist252, 3), zClass(r.ri_dist252)],
      ["ri_dist21", fmt(r.ri_dist21, 3), zClass(r.ri_dist21)],
      ["ri_dist126", fmt(r.ri_dist126, 3), zClass(r.ri_dist126)],
    ];
    pairs.forEach(([k, v, cls]) => {
      dl.appendChild(el("dt", { text: k }));
      dl.appendChild(el("dd", { className: cls || "", text: v }));
    });

    dl.appendChild(el("dt", { text: "Bull-stack" }));
    const ddBull = el("dd", null, [bullNode(r.bull_stack)]);
    dl.appendChild(ddBull);

    dl.appendChild(el("dt", { text: "Capitulation" }));
    dl.appendChild(el("dd", null, [
      r.cap ? el("span", { className: "flag-cap", text: "YES" }) : el("span", { className: "dim", text: "no" }),
    ]));

    dl.appendChild(el("dt", { text: "Pullback" }));
    dl.appendChild(el("dd", null, [
      r.pb ? el("span", { className: "flag-pb", text: "YES" }) : el("span", { className: "dim", text: "no" }),
    ]));

    wrap.appendChild(dl);

    if (r.note) {
      wrap.appendChild(el("div", { className: "detail-note" }, [
        el("strong", { text: "Universe note" }),
        el("span", { text: r.note }),
      ]));
    }

    const actions = el("div", { className: "detail-actions" });
    const btn = el("button", { className: "btn", text: "Prefill sizer with this ticker", attrs: { type: "button", id: "prefill-sizer" } });
    btn.addEventListener("click", () => {
      document.getElementById("size-ticker").value = r.ticker;
      if (r.close != null) document.getElementById("size-entry").value = r.close.toFixed(2);
      document.getElementById("size-ticker").focus();
    });
    actions.appendChild(btn);
    wrap.appendChild(actions);
  }

  // ----- Position sizer -----
  function safeNum(v) {
    if (v == null) return null;
    const s = String(v).trim();
    if (s === "") return null;
    const n = parseFloat(s.replace(",", "."));
    return Number.isNaN(n) ? null : n;
  }

  async function onSizerSubmit(e) {
    e.preventDefault();
    const form = e.target;
    const ticker = (form.ticker.value || "").trim();
    const entry = safeNum(form.entry.value);
    const stop = safeNum(form.stop.value);
    const portfolio = safeNum(form.portfolio.value);
    const riskPct = safeNum(form.risk_pct.value);
    const volCap = safeNum(form.vol_cap.value);
    const payload = { ticker, entry, portfolio };
    if (stop !== null) payload.stop = stop;
    if (riskPct !== null) payload.risk_pct = riskPct / 100;
    if (volCap !== null) payload.vol_cap = volCap / 100;
    const out = document.getElementById("sizer-output");
    const status = document.getElementById("sizer-status");
    const submitBtn = document.getElementById("sizer-submit");
    submitBtn.disabled = true;
    status.textContent = "calling /api/size…";
    out.classList.remove("empty");
    clear(out);
    out.appendChild(el("div", { className: "dim", text: "Calculating…" }));

    try {
      const res = await fetch("/api/size", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await res.json();
      clear(out);
      if (!res.ok) {
        out.appendChild(el("div", { className: "row error" }, [
          el("span", { className: "k", text: "Error" }),
          el("span", { className: "v", text: String(j.error || res.statusText) }),
        ]));
        status.textContent = `error ${res.status}`;
        return;
      }

      const REQUIRED = ["units", "risk_value", "risk_pct", "atr20_pct", "daily_var_pct"];
      const badFields = REQUIRED.filter((f) => j[f] == null || typeof j[f] !== "number");
      if (badFields.length) {
        out.appendChild(el("div", { className: "row error" }, [
          el("span", { className: "k", text: "Bad response" }),
          el("span", { className: "v", text: `unexpected: ${badFields.join(", ")}` }),
        ]));
        status.textContent = "invalid response";
        return;
      }

      function row(k, v, cls) {
        return el("div", { className: `row ${cls || ""}` }, [
          el("span", { className: "k", text: k }),
          el("span", { className: "v", text: v }),
        ]);
      }

      const riskLabel = j.risk_label || "Risk";
      out.appendChild(row("Position", `${j.units} units`, "headline"));
      if (typeof j.position_value === "number") {
        out.appendChild(row("Position SEK", j.position_value.toLocaleString("sv-SE", { maximumFractionDigits: 0 })));
      }
      out.appendChild(row(`${riskLabel} SEK`, j.risk_value.toLocaleString("sv-SE", { maximumFractionDigits: 0 })));
      out.appendChild(row(`${riskLabel} %`, `${(j.risk_pct * 100).toFixed(2)}%`));
      out.appendChild(row("ATR20%", `${(j.atr20_pct * 100).toFixed(2)}%`));
      out.appendChild(row("Daily VaR%", `${(j.daily_var_pct * 100).toFixed(2)}%`));
      out.appendChild(row("Constraint", j.constraint || "—"));
      if (j.mode) out.appendChild(row("Mode", j.mode));
      if (j.currency && j.currency !== "SEK" && typeof j.fx_rate === "number") {
        out.appendChild(row("FX", `${j.currency} → SEK @ ${j.fx_rate.toFixed(4)}`));
      }
      if (j.warning) out.appendChild(row("Warning", String(j.warning), "warn"));
      status.textContent = `ok @ ${new Date().toLocaleTimeString("sv-SE")}`;
    } catch (err) {
      clear(out);
      out.appendChild(el("div", { className: "row error" }, [
        el("span", { className: "k", text: "Error" }),
        el("span", { className: "v", text: err.message }),
      ]));
      status.textContent = "request failed";
    } finally {
      submitBtn.disabled = false;
    }
  }

  // ----- Scan data health check -----
  function checkScanDataHealth() {
    const warn = document.getElementById("scan-data-warning");
    if (!warn) return;
    let msg = "";
    if (window.SCAN_DATA == null) {
      msg = "⚠ SCAN DATA SAKNAS — scan_data.js laddades inte. Pipeline kan ha failat.";
    } else if (!data.rows || data.rows.length === 0) {
      msg = "⚠ INGA INSTRUMENT — scan_data.js laddat men 0 rader.";
    } else if (data.meta && data.meta.scanned != null && data.meta.scanned !== data.rows.length) {
      msg = `⚠ DATA-MISMATCH — meta säger ${data.meta.scanned} instrument men UI fick ${data.rows.length} rader.`;
    } else if (data.meta && data.meta.errors > 0) {
      msg = `⚠ FETCH-FEL — ${data.meta.errors} av ${data.meta.scanned || 0} instrument failade i senaste scan.`;
    } else if (data.meta && data.meta.data_quality_score != null && data.meta.data_quality_score < 90) {
      msg = `⚠ DATA QUALITY ${data.meta.data_quality_score}% — kontrollera scan/logg innan trade.`;
    }
    if (msg) {
      warn.textContent = msg;
      warn.removeAttribute("hidden");
    } else {
      warn.setAttribute("hidden", "");
    }
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...(options || {}), signal: controller.signal })
      .finally(() => window.clearTimeout(timer));
  }

  async function getScanStatus() {
    if (location.protocol !== "http:" && location.protocol !== "https:") return null;
    try {
      const r = await fetchWithTimeout("/scan/status", { method: "GET", cache: "no-store" }, 4000);
      if (!r.ok) return null;
      return await r.json();
    } catch (_e) {
      return null;
    }
  }

  function loadScanDataScript() {
    return new Promise((resolve, reject) => {
      const cacheBuster = Date.now() + "-" + Math.random().toString(36).slice(2, 8);
      const s = document.createElement("script");
      s.src = "scan_data.js?t=" + cacheBuster;
      let done = false;
      s.onload = () => {
        if (done) return;
        done = true;
        const fresh = window.SCAN_DATA || { meta: {}, rows: [] };
        if (!fresh.rows || fresh.rows.length === 0) {
          reject(new Error("scan_data.js har 0 rader"));
          return;
        }
        document.querySelectorAll('script[src^="scan_data.js"]').forEach((tag) => {
          if (tag !== s) tag.remove();
        });
        resolve(fresh);
      };
      s.onerror = () => {
        if (done) return;
        done = true;
        reject(new Error("scan_data.js kunde inte laddas"));
      };
      document.head.appendChild(s);
    });
  }

  function scanDataGeneratedAtMs(fresh) {
    const value = fresh?.meta?.generated;
    if (!value) return null;
    const parsed = Date.parse(String(value).replace(" ", "T"));
    return Number.isFinite(parsed) ? parsed : null;
  }

  async function waitForFreshScanData(previousGenerated, timeoutMs, expectedScan) {
    const deadline = Date.now() + timeoutMs;
    const expectedScanId = expectedScan?.scan_id || null;
    const startedAtMs = expectedScan?.started_at ? Date.parse(expectedScan.started_at) : null;
    let latest = null;
    while (Date.now() < deadline) {
      const status = await getScanStatus();
      const isExpectedScan = !expectedScanId || (status && status.scan_id === expectedScanId);

      if (expectedScanId && isExpectedScan) {
        if (status && ["failed", "timeout"].includes(status.status)) {
          throw new Error(status.error || `scan ${status.status}`);
        }
        if (status && ["ok", "partial"].includes(status.status)) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          latest = await loadScanDataScript();
          const generatedAtMs = scanDataGeneratedAtMs(latest);
          if (!startedAtMs || (generatedAtMs && generatedAtMs >= startedAtMs - 2000)) return latest;
          throw new Error("scan klar men scan_data.js timestamp är äldre än scan-start");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        continue;
      }

      latest = await loadScanDataScript();
      if (!previousGenerated || latest.meta?.generated !== previousGenerated) return latest;
      if (status && ["failed", "timeout"].includes(status.status)) {
        throw new Error(status.error || `scan ${status.status}`);
      }
      if (status && ["ok", "partial"].includes(status.status)) {
        latest = await loadScanDataScript();
        if (latest.meta?.generated !== previousGenerated) return latest;
        throw new Error("scan klar men scan_data.js timestamp ändrades inte");
      }
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
    }
    throw new Error("scan timeout: data blev inte färsk i tid");
  }

  function applyFreshScanData(fresh) {
    data.meta = fresh.meta;
    data.rows = fresh.rows;
    if (state.selectedTicker && !data.rows.find((row) => row.ticker === state.selectedTicker) && !stockRowByTicker(state.selectedTicker)) {
      state.selectedTicker = null;
      state.selectedSource = "scanner";
    }
    checkScanDataHealth();
    renderMeta();
    renderMarketDeck();
    renderStockPanel();
    renderHeaderSort();
    renderGrid();
    renderFeed();
    renderDetail();
    renderAlignmentPanel();
  }

  // ----- Keyboard navigation -----
  function navigateRows(dir) {
    const rows = document.querySelectorAll("#scanner-tbody tr");
    if (!rows.length) return;
    const tickers = Array.from(rows).map((tr) => tr.dataset.ticker);
    const currentIdx = state.selectedTicker ? tickers.indexOf(state.selectedTicker) : -1;
    let nextIdx = currentIdx + dir;
    if (nextIdx < 0) nextIdx = 0;
    if (nextIdx >= tickers.length) nextIdx = tickers.length - 1;
    state.selectedTicker = tickers[nextIdx];
    renderGrid();
    renderDetail();
    renderAlignmentPanel();
    const nextRow = Array.from(document.querySelectorAll("#scanner-tbody tr"))
      .find((tr) => tr.dataset.ticker === state.selectedTicker);
    if (nextRow) nextRow.scrollIntoView({ block: "nearest" });
  }

  // ----- Live clock + market status -----
  function tickClock() {
    const now = new Date();
    const timeEl = document.getElementById("live-clock-time");
    if (!timeEl) return;
    const fmt = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Europe/Stockholm",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    timeEl.textContent = fmt.format(now);
    const tzEl = document.getElementById("live-clock-tz");
    if (tzEl) {
      const tzName = new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Europe/Stockholm",
        timeZoneName: "short",
      }).formatToParts(now).find((p) => p.type === "timeZoneName");
      tzEl.textContent = tzName ? tzName.value : "SE";
    }
  }

  function getMarketStatus() {
    const now = new Date();
    function minutesInTZ(tz) {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: tz, hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false,
      }).formatToParts(now);
      const h = parseInt(parts.find((p) => p.type === "hour").value, 10);
      const m = parseInt(parts.find((p) => p.type === "minute").value, 10);
      const wd = parts.find((p) => p.type === "weekday").value; // Mon..Sun
      return { mins: h * 60 + m, weekend: (wd === "Sat" || wd === "Sun") };
    }
    const sth = minutesInTZ("Europe/Stockholm");
    if (sth.weekend) return { open: false, label: "WEEKEND" };
    const ny = minutesInTZ("America/New_York");
    const hk = minutesInTZ("Asia/Hong_Kong");
    if (sth.mins >= 9 * 60 && sth.mins < 17 * 60 + 30) return { open: true, label: "OMX OPEN" };
    if (ny.mins >= 9 * 60 + 30 && ny.mins < 16 * 60) return { open: true, label: "US OPEN" };
    if (hk.mins >= 9 * 60 + 30 && hk.mins < 16 * 60) return { open: true, label: "ASIA OPEN" };
    return { open: false, label: "MARKETS CLOSED" };
  }

  function updateMarketStatus() {
    const wrap = document.getElementById("market-status");
    const label = document.getElementById("market-status-label");
    if (!wrap || !label) return;
    const s = getMarketStatus();
    label.textContent = s.label;
    wrap.classList.toggle("is-open", s.open);
    wrap.classList.toggle("is-closed", !s.open);
  }

  // ----- Wire up -----
  function init() {
    loadPersistedState();
    checkScanDataHealth();
    renderMeta();
    renderMarketDeck();
    renderStockPanel();
    renderHeaderSort();
    renderGrid();
    renderFeed();
    renderDetail();
    renderAlignmentPanel();
    applyStateToDOM();
    tickClock();
    updateMarketStatus();
    setInterval(tickClock, 1000);
    setInterval(updateMarketStatus, 60000);

    function sortByHeader(th) {
      const k = th.dataset.sort;
      if (!k) return;
      if (state.sortKey === k) state.sortAsc = !state.sortAsc;
      else { state.sortKey = k; state.sortAsc = true; }
      persistState();
      renderHeaderSort();
      renderGrid();
    }

    document.querySelectorAll("table.grid thead th").forEach((th) => {
      th.addEventListener("click", () => sortByHeader(th));
      th.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        sortByHeader(th);
      });
    });

    document.querySelectorAll(".view-tab[data-terminal-view]").forEach((button) => {
      button.addEventListener("click", () => setTerminalView(button.dataset.terminalView));
      button.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        const tabs = Array.from(document.querySelectorAll(".view-tab[data-terminal-view]"));
        const idx = tabs.indexOf(button);
        const next = e.key === "ArrowRight"
          ? (idx + 1) % tabs.length
          : (idx - 1 + tabs.length) % tabs.length;
        tabs[next].focus();
        setTerminalView(tabs[next].dataset.terminalView);
      });
    });

    document.getElementById("filter-region").addEventListener("change", (e) => { state.filterRegion = e.target.value; persistState(); renderGrid(); });
    document.getElementById("filter-bull").addEventListener("change", (e) => { state.filterBull = e.target.value; persistState(); renderGrid(); });
    document.getElementById("filter-flag").addEventListener("change", (e) => { state.filterFlag = e.target.value; persistState(); renderGrid(); });
    document.getElementById("filter-search").addEventListener("input", (e) => { state.filterSearch = e.target.value; persistState(); renderGrid(); });
    document.getElementById("filter-regim").addEventListener("change", (e) => { state.filterRegim = e.target.value; persistState(); renderGrid(); });
    document.getElementById("filter-volcap").addEventListener("change", (e) => { state.filterVolcap = e.target.value; persistState(); renderGrid(); });
    document.getElementById("stock-filter-tier").addEventListener("change", (e) => { state.stockFilterTier = e.target.value; persistState(); renderStockGrid(); });
    document.getElementById("stock-filter-market").addEventListener("change", (e) => { state.stockFilterMarket = e.target.value; persistState(); renderStockGrid(); });
    document.getElementById("stock-filter-action").addEventListener("change", (e) => { state.stockFilterAction = e.target.value; persistState(); renderStockGrid(); });
    document.getElementById("stock-filter-search").addEventListener("input", (e) => { state.stockFilterSearch = e.target.value; persistState(); renderStockGrid(); });

    document.getElementById("btn-reset-filters").addEventListener("click", () => {
      state.filterRegion = "";
      state.filterBull = "";
      state.filterFlag = "";
      state.filterSearch = "";
      state.filterRegim = "";
      state.filterVolcap = "";
      applyStateToDOM();
      persistState();
      renderGrid();
    });

    document.getElementById("btn-reset-stock-filters").addEventListener("click", () => {
      state.stockFilterTier = "";
      state.stockFilterMarket = "";
      state.stockFilterAction = "";
      state.stockFilterSearch = "";
      applyStateToDOM();
      persistState();
      renderStockGrid();
    });

    document.getElementById("scanner-tbody").addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      if (!tr) return;
      state.selectedTicker = tr.dataset.ticker;
      state.selectedSource = "scanner";
      renderGrid();
      renderStockGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("scanner-tbody").addEventListener("keydown", (e) => {
      const tr = e.target.closest("tr");
      if (!tr) return;
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      state.selectedTicker = tr.dataset.ticker;
      state.selectedSource = "scanner";
      renderGrid();
      renderStockGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("stock-tbody").addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      if (!tr || !tr.dataset.ticker) return;
      state.selectedTicker = tr.dataset.ticker;
      state.selectedSource = "stock";
      renderStockGrid();
      renderGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("stock-tbody").addEventListener("keydown", (e) => {
      const tr = e.target.closest("tr");
      if (!tr || !tr.dataset.ticker) return;
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      state.selectedTicker = tr.dataset.ticker;
      state.selectedSource = "stock";
      renderStockGrid();
      renderGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("stock-current").addEventListener("click", (e) => {
      const card = e.target.closest("[data-ticker]");
      if (!card) return;
      state.selectedTicker = card.dataset.ticker;
      state.selectedSource = "stock";
      renderStockGrid();
      renderGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("stock-current").addEventListener("keydown", (e) => {
      const card = e.target.closest("[data-ticker]");
      if (!card) return;
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      state.selectedTicker = card.dataset.ticker;
      state.selectedSource = "stock";
      renderStockGrid();
      renderGrid();
      renderDetail();
      renderAlignmentPanel();
    });

    document.getElementById("signal-feed").addEventListener("click", (e) => {
      const t = e.target.closest("[data-jump]");
      if (!t) return;
      state.selectedTicker = t.dataset.jump;
      state.selectedSource = "scanner";
      renderGrid();
      renderStockGrid();
      renderDetail();
      renderAlignmentPanel();
      const row = Array.from(document.querySelectorAll("#scanner-tbody tr"))
        .find((tr) => tr.dataset.ticker === state.selectedTicker);
      if (row) row.scrollIntoView({ block: "nearest" });
    });

    document.getElementById("sizer-form").addEventListener("submit", onSizerSubmit);

    // Reload: triggers POST /scan on the server (which runs daily_scan.py),
    // then re-injects scan_data.js to pick up fresh rows. If the HTTP response
    // is lost while the scan keeps running, poll scan_data.js instead of
    // leaving the button stuck in "Scanning".
    let reloadInFlight = false;
    document.getElementById("btn-reload").addEventListener("click", async () => {
      if (reloadInFlight) return;
      reloadInFlight = true;
      const btn = document.getElementById("btn-reload");
      const originalText = btn.textContent;
      btn.textContent = "Scanning...";
      btn.disabled = true;
      const grid = document.getElementById("scanner-grid");
      grid.classList.add("loading");
      const staleBadge = document.getElementById("stale-badge");
      if (staleBadge) staleBadge.classList.add("refreshing");

      const restore = () => {
        btn.textContent = originalText;
        btn.disabled = false;
        grid.classList.remove("loading");
        if (staleBadge) staleBadge.classList.remove("refreshing");
        reloadInFlight = false;
      };
      const fail = (msg) => {
        console.error("[reload]", msg);
        btn.textContent = "Fail: " + String(msg).slice(0, 40);
        btn.disabled = false;
        grid.classList.remove("loading");
        if (staleBadge) staleBadge.classList.remove("refreshing");
        reloadInFlight = false;
        setTimeout(() => { btn.textContent = originalText; }, 5000);
      };

      const previousGenerated = data.meta?.generated || null;
      let scanRan = false;
      let scanPending = false;
      let scanUnavailable = false;
      let expectedScan = null;
      const canRequestScan = location.protocol === "http:" || location.protocol === "https:";

      if (canRequestScan) {
        try {
          btn.textContent = "Starting scan...";
          const r = await fetchWithTimeout("/scan", { method: "POST", cache: "no-store" }, 8000);
          if (r.ok) {
            const payload = await r.json().catch(() => ({}));
            expectedScan = payload;
            if (["ok", "partial"].includes(payload.status)) {
              scanRan = true;
            } else {
              scanPending = true;
            }
          } else if (r.status === 409) {
            expectedScan = await r.json().catch(() => null);
            scanPending = true;
            console.warn("[reload] another scan already running, just refreshing data");
          } else if ([404, 405, 501].includes(r.status)) {
            scanUnavailable = true;
            console.warn("[reload] /scan endpoint not available, just refreshing data");
          } else {
            const body = await r.text().catch(() => "");
            return fail(`/scan failed ${r.status}: ${body.slice(0, 120)}`);
          }
        } catch (e) {
          if (e.name === "AbortError") {
            scanPending = true;
            console.warn("[reload] /scan still running or no response; polling scan_data.js");
          } else {
            scanUnavailable = true;
            console.warn("[reload] /scan network error — refreshing cached data:", e.message);
          }
        }
      } else {
        scanUnavailable = true;
        console.warn("[reload] dashboard server not available from this protocol; refreshing scan_data.js only");
      }

      try {
        btn.textContent = scanPending ? "Waiting for scan..." : "Loading data...";
        const fresh = scanPending
          ? await waitForFreshScanData(previousGenerated, 180000, expectedScan)
          : await loadScanDataScript();
        applyFreshScanData(fresh);
        const isNew = !previousGenerated || fresh.meta?.generated !== previousGenerated;
        if (scanRan || isNew) {
          btn.textContent = "Scan ok";
          setTimeout(restore, 1500);
        } else if (scanUnavailable) {
          btn.textContent = "Cached data";
          setTimeout(restore, 1800);
        } else {
          btn.textContent = "Data reloaded";
          setTimeout(restore, 1200);
        }
      } catch (e) {
        fail(e.message || String(e));
      }
    });

    // Keyboard navigation
    document.addEventListener("keydown", (e) => {
      const active = document.activeElement;
      const isInput = active && (active.tagName === "INPUT" || active.tagName === "SELECT" || active.tagName === "TEXTAREA");

      if (e.key === "/" && !isInput) {
        e.preventDefault();
        const target = state.viewMode === "stocks" ? "stock-filter-search" : "filter-search";
        document.getElementById(target).focus();
        return;
      }
      if (e.key === "Escape") {
        if (isInput) {
          active.blur();
        } else {
          state.selectedTicker = null;
          renderGrid();
          renderDetail();
          renderAlignmentPanel();
        }
        return;
      }
      if (e.key === "r" && !isInput) {
        e.preventDefault();
        document.getElementById("btn-reload").click();
        return;
      }
      if (/^[1-6]$/.test(e.key) && !isInput) {
        const views = ["command", "scanner", "stocks", "signals", "trade", "detail"];
        setTerminalView(views[Number(e.key) - 1]);
        return;
      }
      if ((e.key === "j" || e.key === "ArrowDown") && !isInput) {
        e.preventDefault();
        navigateRows(1);
        return;
      }
      if ((e.key === "k" || e.key === "ArrowUp") && !isInput) {
        e.preventDefault();
        navigateRows(-1);
        return;
      }
      if (e.key === "Enter" && !isInput && state.selectedTicker) {
        document.getElementById("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
