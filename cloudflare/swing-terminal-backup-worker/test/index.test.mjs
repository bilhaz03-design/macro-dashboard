import assert from "node:assert/strict";
import test from "node:test";

import { runBackupClock } from "../src/index.js";

const BASE_ENV = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  SUPABASE_INGEST_TOKEN: "ingest_test",
  GITHUB_ACTIONS_TOKEN: "github_test",
  GITHUB_REPO: "bilhaz03-design/macro-dashboard",
  GITHUB_REF: "main",
};

function okJson(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function makeFetch({ runs = [], state = {}, githubStatus = 204 } = {}) {
  const calls = [];
  const fetchFn = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes("/rest/v1/scan_runs")) {
      return okJson(runs);
    }
    if (url.includes("/rest/v1/scanner_artifacts") && (init.method || "GET") === "GET") {
      return okJson(Object.keys(state).length ? [{ payload: state }] : []);
    }
    if (url.includes("/rest/v1/scanner_artifacts") && init.method === "POST") {
      return okJson([]);
    }
    if (url.includes("api.github.com")) {
      return new Response(null, { status: githubStatus });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  return { fetchFn, calls };
}

test("skips outside Stockholm scan window", async () => {
  const { fetchFn, calls } = makeFetch();
  const result = await runBackupClock(BASE_ENV, {
    now: new Date("2026-05-21T22:30:00Z"),
    fetchFn,
  });

  assert.equal(result.skipped, "outside_stockholm_scan_window");
  assert.equal(calls.length, 0);
});

test("dispatches stale ETF and stocks once", async () => {
  const { fetchFn, calls } = makeFetch({
    runs: [{
      run_id: "old-etf",
      created_at: "2026-05-22T07:00:00Z",
      mode: "etf",
      status: "OK",
    }],
  });

  const result = await runBackupClock(BASE_ENV, {
    now: new Date("2026-05-22T10:00:00Z"),
    fetchFn,
  });

  assert.deepEqual(result.actions.map((action) => action.action), ["dispatched", "dispatched"]);
  const githubCalls = calls.filter((call) => call.url.includes("api.github.com"));
  assert.equal(githubCalls.length, 2);
  assert.match(githubCalls[0].init.body, /"mode":"etf"/);
  assert.match(githubCalls[1].init.body, /"mode":"stocks"/);
});

test("does nothing when latest runs are fresh", async () => {
  const { fetchFn, calls } = makeFetch({
    runs: [
      { run_id: "fresh-etf", created_at: "2026-05-22T09:45:00Z", mode: "etf", status: "OK" },
      { run_id: "fresh-stocks", created_at: "2026-05-22T09:20:00Z", mode: "stocks", status: "OK" },
    ],
  });

  const result = await runBackupClock(BASE_ENV, {
    now: new Date("2026-05-22T10:00:00Z"),
    fetchFn,
  });

  assert.deepEqual(result.actions, []);
  assert.equal(calls.filter((call) => call.url.includes("api.github.com")).length, 0);
});

test("throttles duplicate backup dispatches", async () => {
  const { fetchFn, calls } = makeFetch({
    runs: [],
    state: {
      last_dispatch_at: {
        etf: "2026-05-22T09:50:00Z",
        stocks: "2026-05-22T09:30:00Z",
      },
    },
  });

  const result = await runBackupClock(BASE_ENV, {
    now: new Date("2026-05-22T10:00:00Z"),
    fetchFn,
  });

  assert.deepEqual(result.actions.map((action) => action.action), ["throttled", "throttled"]);
  assert.equal(calls.filter((call) => call.url.includes("api.github.com")).length, 0);
});
