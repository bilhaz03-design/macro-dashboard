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

## Behavior

- Runs every 15 minutes by Cloudflare Cron.
- Skips outside Monday-Friday 09:00-22:15 Europe/Stockholm.
- Dispatches ETF scan if the latest OK ETF/all scan is older than 35 minutes.
- Dispatches stock scan if the latest OK stock/all scan is older than 90 minutes.
- Stores its throttle state in Supabase `scanner_artifacts` under
  `cloudflare-backup-clock-state`.
