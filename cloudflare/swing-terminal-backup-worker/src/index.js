const STATE_KEY = "cloudflare-backup-clock-state";
const STOCKHOLM_TZ = "Europe/Stockholm";

const DEFAULTS = {
  githubRepo: "bilhaz03-design/macro-dashboard",
  githubRef: "main",
  etfStaleMinutes: 35,
  stocksStaleMinutes: 90,
  etfThrottleMinutes: 25,
  stocksThrottleMinutes: 60,
};

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runBackupClock(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return jsonResponse(200, {
        ok: true,
        service: "swing-terminal-backup-clock",
        stockholm: stockholmParts(new Date()),
      });
    }

    if (url.pathname === "/run" && request.method === "POST") {
      const adminToken = env.ADMIN_TOKEN || "";
      if (!adminToken || request.headers.get("x-swing-terminal-admin-token") !== adminToken) {
        return jsonResponse(403, { ok: false, error: "forbidden" });
      }
      const mode = url.searchParams.get("mode") || "etf";
      const noNotify = url.searchParams.get("no_notify") === "true";
      const result = await dispatchScanner(env, mode, {
        fetchFn: fetch,
        force: true,
        noNotify,
      });
      return jsonResponse(200, result);
    }

    return jsonResponse(404, { ok: false, error: "not_found" });
  },
};

export async function runBackupClock(env, options = {}) {
  const now = options.now || new Date();
  const fetchFn = options.fetchFn || fetch;
  const settings = readSettings(env);
  const local = stockholmParts(now);

  if (!inScanWindow(local)) {
    return {
      ok: true,
      skipped: "outside_stockholm_scan_window",
      stockholm: local,
      actions: [],
    };
  }

  const runs = await fetchLatestRuns(env, fetchFn);
  const latestEtf = latestForMode(runs, "etf");
  const latestStocks = latestForMode(runs, "stocks");
  const state = await loadState(env, fetchFn);
  const actions = [];
  const decisions = {
    etfAgeMinutes: ageMinutes(latestEtf?.created_at, now),
    stocksAgeMinutes: ageMinutes(latestStocks?.created_at, now),
  };

  if (isStale(decisions.etfAgeMinutes, settings.etfStaleMinutes)) {
    if (recentlyDispatched(state, "etf", now, settings.etfThrottleMinutes)) {
      actions.push({ mode: "etf", action: "throttled" });
    } else {
      const dispatch = await dispatchScanner(env, "etf", { fetchFn, force: false, noNotify: false });
      actions.push({ mode: "etf", action: "dispatched", request: dispatch });
      rememberDispatch(state, "etf", now);
    }
  }

  if (isStale(decisions.stocksAgeMinutes, settings.stocksStaleMinutes)) {
    if (recentlyDispatched(state, "stocks", now, settings.stocksThrottleMinutes)) {
      actions.push({ mode: "stocks", action: "throttled" });
    } else {
      const dispatch = await dispatchScanner(env, "stocks", { fetchFn, force: false, noNotify: false });
      actions.push({ mode: "stocks", action: "dispatched", request: dispatch });
      rememberDispatch(state, "stocks", now);
    }
  }

  state.last_checked_at = now.toISOString();
  state.stockholm = local;
  state.decisions = decisions;
  state.actions = actions;
  await saveState(env, fetchFn, state, now);

  return { ok: true, stockholm: local, decisions, actions };
}

function readSettings(env) {
  return {
    githubRepo: env.GITHUB_REPO || DEFAULTS.githubRepo,
    githubRef: env.GITHUB_REF || DEFAULTS.githubRef,
    etfStaleMinutes: envNumber(env.ETF_STALE_MINUTES, DEFAULTS.etfStaleMinutes),
    stocksStaleMinutes: envNumber(env.STOCKS_STALE_MINUTES, DEFAULTS.stocksStaleMinutes),
    etfThrottleMinutes: envNumber(env.ETF_THROTTLE_MINUTES, DEFAULTS.etfThrottleMinutes),
    stocksThrottleMinutes: envNumber(env.STOCKS_THROTTLE_MINUTES, DEFAULTS.stocksThrottleMinutes),
  };
}

function envNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function requiredEnv(env, name) {
  const value = env[name];
  if (!value) {
    throw new Error(`missing_env:${name}`);
  }
  return value;
}

function supabaseHeaders(env) {
  const key = requiredEnv(env, "SUPABASE_PUBLISHABLE_KEY");
  return {
    "Accept": "application/json",
    "apikey": key,
    "Authorization": `Bearer ${key}`,
    "x-swing-terminal-token": requiredEnv(env, "SUPABASE_INGEST_TOKEN"),
  };
}

function supabaseRestUrl(env, path, query = {}) {
  const base = requiredEnv(env, "SUPABASE_URL").replace(/\/$/, "");
  const url = new URL(`${base}/rest/v1/${path}`);
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function fetchLatestRuns(env, fetchFn) {
  const response = await fetchFn(supabaseRestUrl(env, "scan_runs", {
    select: "run_id,created_at,mode,status,exit_code,total_scanned,error_count",
    status: "eq.OK",
    order: "created_at.desc",
    limit: "20",
  }), { headers: supabaseHeaders(env) });

  if (!response.ok) {
    throw new Error(`supabase_scan_runs_failed:${response.status}`);
  }
  return response.json();
}

async function loadState(env, fetchFn) {
  const response = await fetchFn(supabaseRestUrl(env, "scanner_artifacts", {
    select: "payload",
    artifact_key: `eq.${STATE_KEY}`,
    limit: "1",
  }), { headers: supabaseHeaders(env) });

  if (!response.ok) {
    throw new Error(`supabase_state_read_failed:${response.status}`);
  }

  const rows = await response.json();
  return rows?.[0]?.payload && typeof rows[0].payload === "object" ? rows[0].payload : {};
}

async function saveState(env, fetchFn, state, now) {
  const response = await fetchFn(supabaseRestUrl(env, "scanner_artifacts", {
    on_conflict: "artifact_key",
  }), {
    method: "POST",
    headers: {
      ...supabaseHeaders(env),
      "Content-Type": "application/json",
      "Prefer": "resolution=merge-duplicates",
    },
    body: JSON.stringify([{
      artifact_key: STATE_KEY,
      kind: "backup_clock_state",
      scan_date: stockholmDate(now),
      updated_at: now.toISOString(),
      source_path: "cloudflare/swing-terminal-backup-worker",
      payload: state,
    }]),
  });

  if (!response.ok) {
    throw new Error(`supabase_state_write_failed:${response.status}`);
  }
}

async function dispatchScanner(env, mode, options) {
  if (!["etf", "stocks", "all"].includes(mode)) {
    throw new Error(`invalid_mode:${mode}`);
  }
  const settings = readSettings(env);
  const response = await options.fetchFn(
    `https://api.github.com/repos/${settings.githubRepo}/actions/workflows/swing-terminal-cloud.yml/dispatches`,
    {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": `Bearer ${requiredEnv(env, "GITHUB_ACTIONS_TOKEN")}`,
        "Content-Type": "application/json",
        "User-Agent": "swing-terminal-cloudflare-backup-clock",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({
        ref: settings.githubRef,
        inputs: {
          mode,
          force: options.force ? "true" : "false",
          no_notify: options.noNotify ? "true" : "false",
        },
      }),
    },
  );

  if (response.status !== 204) {
    const text = await response.text();
    throw new Error(`github_dispatch_failed:${response.status}:${text.slice(0, 200)}`);
  }

  return { status: 204, workflow: "swing-terminal-cloud.yml", mode };
}

function latestForMode(runs, mode) {
  return runs.find((run) => run.status === "OK" && (run.mode === mode || run.mode === "all"));
}

function isStale(age, threshold) {
  return age === null || age > threshold;
}

function ageMinutes(iso, now) {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return Math.floor((now.getTime() - parsed.getTime()) / 60000);
}

function recentlyDispatched(state, mode, now, thresholdMinutes) {
  const iso = state?.last_dispatch_at?.[mode];
  const age = ageMinutes(iso, now);
  return age !== null && age < thresholdMinutes;
}

function rememberDispatch(state, mode, now) {
  state.last_dispatch_at = state.last_dispatch_at || {};
  state.last_dispatch_at[mode] = now.toISOString();
}

function stockholmParts(date) {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone: STOCKHOLM_TZ,
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  return {
    weekday: parts.weekday,
    isodow: { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 }[parts.weekday],
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}`,
  };
}

function stockholmDate(date) {
  return stockholmParts(date).date;
}

function inScanWindow(local) {
  if (!local.isodow || local.isodow >= 6) {
    return false;
  }
  const minutes = local.hour * 60 + local.minute;
  return minutes >= 9 * 60 && minutes <= 22 * 60 + 15;
}

function jsonResponse(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
