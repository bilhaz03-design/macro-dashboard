# Cloud Scanner Architecture — 2026-05-21

## Decision

Best current path: keep the existing Python scanner and run it as a cloud cron
job, with Supabase as durable state and Telegram as the human interrupt channel.

Do not rewrite the scanner into Supabase Edge Functions first. The production
scanner is Python/pandas/yfinance; Supabase Edge Functions are a Deno/TypeScript
runtime. Supabase should be the database and restore layer, not the strategy
engine.

## Runtime

Primary deployment target:

- Render Cron Jobs: ETF scanner every 15 minutes, stock scanner hourly.
- Supabase Postgres for scanner state.
- Telegram for new/faded/close-check alerts.
- Manual broker execution only.

The ETF Render schedule in `render.yaml` intentionally runs a broad UTC window:

```cron
*/15 6-21 * * 1-5
```

The stock scanner runs separately:

```cron
5 7-22 * * 1-5
```

`scripts/cloud_scan_worker.py` then applies a Europe/Stockholm guard and only
runs Monday-Friday 09:00-22:15 local time. This avoids DST drift while still
catching EU hours, late US context, faded intraday signals, and close checks.
Stocks are hourly because their current-signal refresh is materially heavier
than the ETF scan.

## State Contract

Before scanning, the cloud runner restores these JSON artifacts from Supabase:

- `signal-journal` -> `data/signal-journal.json`
- `latest-signals` -> `data/latest-signals.json`
- `stock-signal-journal` -> `data/stock-signal-journal.json`
- `signal-notify-state` -> `data/signal-notify-state.json`
- `live-trades` -> `data/live-trades.json`
- `execution-map` -> `data/execution_map.json`

After scanning, it publishes:

- latest ETF scanner payload
- ETF signal journal
- stock signal journal
- stock current scan coverage
- `dashboard/scan_data.js`
- `dashboard/stock_data.js`
- notification dedupe state
- live trade journal
- execution map
- flattened signal event rows
- flattened live trade rows
- one scan run summary

That restore-before-run step is the important part. Without it, a stateless cron
runner can forget that a signal fired at 11:00 and then disappeared at 16:00.

When the Mac comes back online, run:

```bash
cd "/Users/bobbo/Desktop/Finans Projects"
.venv/bin/python scripts/sync_cloud_state.py
```

That pulls the latest cloud artifacts into the local terminal files.

## Deployment Steps

1. Create a Supabase project.
2. Run `supabase/migrations/20260521_swing_terminal_cloud.sql`.
3. Run `supabase/migrations/20260521_swing_terminal_ingest_rls.sql`.
4. Add cloud secrets from `config/cloud.env.example`.
5. Deploy `render.yaml` as a Render Blueprint.
6. Confirm Telegram receives the setup test message.

## Live Supabase Status

2026-05-21 update: Codex Supabase connector is active against the Garderob
project in `eu-north-1`.

Applied migration:

- `20260521170611_swing_terminal_cloud_state`
- `20260521171716_swing_terminal_ingest_rls`

Verified rows after ETF and stock cloud smoke runs:

- `scanner_artifacts`: 8
- `signal_events`: 9
- `live_trades`: 1
- `execution_map`: 1
- `scan_runs`: 4

Signal state seeded:

- ETF: 1 `FADED_INTRADAY` event, `FLXC.DE` PB126.
- Stocks: 8 `ACTIVE_NOW` stock scanner events.

REST path verified:

- `SUPABASE_PUBLISHABLE_KEY` + `SUPABASE_INGEST_TOKEN` authorizes through
  `public.scanner_ingest_authorized()`.
- `scripts/cloud_scan_worker.py --mode etf --no-notify --force` completed a
  full 44-instrument scan and published state to Supabase.
- `scripts/cloud_scan_worker.py --mode stocks --no-notify --force` refreshed
  108 stock current checks, exported terminal stock data, and published state to
  Supabase. A follow-up audit corrected Roche from Yahoo ticker `ROG.SW` to
  `RO.SW`.
- `scripts/sync_cloud_state.py` restored JSON and dashboard JS artifacts from
  Supabase back to local disk.

This proves the database side and REST ingest are live. Render does not need a
broad `SUPABASE_SERVICE_ROLE_KEY`; that key is now optional fallback only.

Required secrets:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `SUPABASE_INGEST_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Garderob currently contains a real Supabase project URL in
`/Users/bobbo/Desktop/Garderob/www/js/supabase-sync.js`, but the exposed key
there is a public client key. That is now enough when paired with the private
scanner ingest token. The bootstrap script imports the URL and publishable key:

```bash
cd "/Users/bobbo/Desktop/Finans Projects"
.venv/bin/python scripts/bootstrap_cloud_env_from_garderob.py
```

Then paste private values into:

- `~/.config/swing-terminal/cloud.env`
- `~/.config/swing-terminal/alerts.env`

The ingest token can be generated/rotated locally:

```bash
.venv/bin/python scripts/rotate_supabase_ingest_token.py
```

Apply the printed hash SQL through the Supabase connector. The raw token stays
only in `~/.config/swing-terminal/cloud.env`.

After creating a Telegram bot and sending it one message:

```bash
.venv/bin/python scripts/setup_telegram_alerts.py
```

Readiness check:

```bash
.venv/bin/python scripts/check_cloud_readiness.py
```

## Operational Guarantees

- Signals are not lost when they fade intraday.
- Telegram duplicate suppression is restored in cloud runs.
- The scanner can run without the Mac being awake.
- No automatic broker order placement exists in the cloud path.
- Exact P/L remains blocked until broker fill, fees, and FX are confirmed.

## Local Smoke Commands

```bash
cd "/Users/bobbo/Desktop/Finans Projects"
python3 -m py_compile scripts/cloud_scan_worker.py scripts/supabase_io.py
.venv/bin/python scripts/cloud_scan_worker.py --mode etf --no-notify --force
.venv/bin/python scripts/cloud_scan_worker.py --mode stocks --no-notify --force
.venv/bin/python scripts/sync_cloud_state.py
```
