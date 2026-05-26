# Cloudflare Backup Worker Deploy Runbook

Date: 2026-05-27 Europe/Stockholm

This runbook is for deploying `cloudflare/swing-terminal-backup-worker` as the free external backup clock for Swing Terminal.

## What this backup does

- It does **not** run the Python scanner itself.
- It checks Supabase `scan_runs` every 15 minutes using Cloudflare Cron.
- If ETF or stock scanner heartbeats are stale during the Stockholm scan window, it dispatches the GitHub `Swing Terminal Cloud Scanner` workflow.
- It writes its own state to Supabase under `cloudflare-backup-clock-state`.

## Why Cloudflare Worker fits the free backup role

Official sources checked:

- Cloudflare Cron Triggers use a `scheduled()` handler and can be configured in Wrangler TOML.
- Cron Trigger changes can take up to 15 minutes to propagate.
- Cloudflare Workers Free currently lists 100,000 requests/day and 5 Cron Triggers per account.
- Cloudflare's GitHub Actions guidance says CI deploys need `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, and recommends scoping the token narrowly.
- GitHub's workflow dispatch endpoint requires a token that can create workflow dispatch events; use a least-privilege token for this repo.

Sources:

- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event

## Already completed

These repository secrets were synced from local Supabase env without printing values:

- `CF_SUPABASE_URL`
- `CF_SUPABASE_PUBLISHABLE_KEY`
- `CF_SUPABASE_INGEST_TOKEN`

## Remaining required GitHub repository secrets

Set these in GitHub repo `bilhaz03-design/macro-dashboard`:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CF_GITHUB_ACTIONS_TOKEN`

Optional:

- `CF_ADMIN_TOKEN` — only needed if the protected manual Worker `/run` endpoint should be usable.

## Token guidance

### Cloudflare token

Create a Cloudflare API token using Cloudflare's `Edit Cloudflare Workers` permission policy and scope it to the one account used for this Worker.

Do not commit the token into the repo. Store it as GitHub repository secret `CLOUDFLARE_API_TOKEN`.

### GitHub token for `CF_GITHUB_ACTIONS_TOKEN`

Use a fine-grained GitHub token scoped to `bilhaz03-design/macro-dashboard` with only the permissions needed to dispatch the scanner workflow.

Do not reuse the broad local `gh auth` token unless explicitly accepted as a temporary trade-off.

## Preflight

Run:

```bash
python3 scripts/cloudflare_backup_preflight.py
```

Expected before deploy:

- `Deploy ready: YES`
- no required missing secrets

## Deploy

After preflight is ready:

```bash
gh workflow run deploy-cloudflare-backup-clock.yml --ref main
```

Then watch it:

```bash
gh run watch <RUN_ID> --exit-status
```

## After deploy

1. Find the Worker URL in the deploy output or Cloudflare dashboard.
2. Add it locally as:

```bash
CLOUDFLARE_BACKUP_HEALTH_URL=https://<worker-url>/health
```

3. Run:

```bash
python3 scripts/cloud_chain_status.py --strict
```

Expected:

- `cloudflare_health=OK`
- `cloudflare_backup=<timestamp>` after first scheduled/check run

## Proof required before calling backup live

- Deploy workflow success.
- HTTP `/health` returns ready.
- Strict chain sees `cloudflare_health=OK`.
- Supabase artifact `cloudflare-backup-clock-state` has `last_checked_at` after a real Worker run.
- A stale-run dispatch is tested or explicitly deferred.
