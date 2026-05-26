# Swing Terminal Backup Clock

Cloudflare Workers backup orchestrator for the swing terminal.

It does not run the Python scanner. It watches Supabase `scan_runs` every 15
minutes and dispatches the GitHub Actions scanner only when the latest OK run is
stale during the Stockholm scan window.

## Required Cloudflare secrets

Set these in Cloudflare Workers before deploy:

```bash
npx wrangler secret put SUPABASE_URL --config cloudflare/swing-terminal-backup-worker/wrangler.toml
npx wrangler secret put SUPABASE_PUBLISHABLE_KEY --config cloudflare/swing-terminal-backup-worker/wrangler.toml
npx wrangler secret put SUPABASE_INGEST_TOKEN --config cloudflare/swing-terminal-backup-worker/wrangler.toml
npx wrangler secret put GITHUB_ACTIONS_TOKEN --config cloudflare/swing-terminal-backup-worker/wrangler.toml
```

Optional manual endpoint protection:

```bash
npx wrangler secret put ADMIN_TOKEN --config cloudflare/swing-terminal-backup-worker/wrangler.toml
```

If deploying through GitHub Actions, set these repository secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CF_SUPABASE_URL`
- `CF_SUPABASE_PUBLISHABLE_KEY`
- `CF_SUPABASE_INGEST_TOKEN`
- `CF_GITHUB_ACTIONS_TOKEN`
- `CF_ADMIN_TOKEN` optional, only needed for protected manual `/run`

## Behavior

- Runs every 15 minutes by Cloudflare Cron.
- Skips outside Monday-Friday 09:00-22:15 Europe/Stockholm.
- Dispatches ETF scan if the latest OK ETF/all scan is older than 35 minutes.
- Dispatches stock scan if the latest OK stock/all scan is older than 90 minutes.
- Stores its throttle state in Supabase `scanner_artifacts` under
  `cloudflare-backup-clock-state`.

## Endpoints

- `GET /health` returns readiness, missing required env names, settings, and current Stockholm clock parts. It does not return secret values.
- `POST /run?mode=etf|stocks|all&no_notify=true|false` dispatches a forced scanner run when `ADMIN_TOKEN` exists and the request includes header `x-swing-terminal-admin-token: <ADMIN_TOKEN>`.
