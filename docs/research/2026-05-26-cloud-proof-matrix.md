# Cloud Proof Matrix — Swing Terminal scanner

Date: 2026-05-26

## Scope

This matrix is for the requirement: Bobbo can leave the laptop/browser and still receive Telegram alerts when scanner signals or scanner failures occur.

It does not claim live cloud success until the exact provider runtime has executed `scripts/cloud_scan_worker.py` and published a fresh Supabase heartbeat.

## Current proof package

Canonical runtime artifact:

- `ops/cloud_job/Dockerfile`
- `ops/cloud_job/cloud_job_entrypoint.sh`
- `ops/cloud_job/README.md`
- `.dockerignore`

The image deliberately excludes `.env`, local logs, caches, large research artifacts, and secrets. Runtime secrets must be injected by the provider.

## Candidate matrix

| Candidate | Role | Why it is interesting | Main blocker / falsifier | Proof command/path |
|---|---|---|---|---|
| Self-owned home runner | Primary | Most ownership; no cloud free-tier rug-pull | home power/internet/hardware can fail | `ops/self_runner/install_macos_launchd.sh` or Linux systemd install, then `ops/self_runner/healthcheck.sh` |
| Oracle Always Free A1 | Primary VM | Best raw free VM capacity found | capacity unavailable; idle reclaim | Linux systemd install; verify scheduled ETF/stocks heartbeat |
| Google Cloud Run Jobs | Managed job proof | Good fit for short containerized jobs if free-tier stays inside limits | image/build/storage costs; runtime exceeds free budget | build/push image, run Cloud Run Job, verify Supabase `scan_runs` |
| Northflank Sandbox | Managed job/cron proof | Advertises free always-on + free cron jobs | unknown free resource limits for scanner | deploy Dockerfile job/cron, verify run logs + Supabase heartbeat |
| IBM Code Engine | Managed job proof | Job-oriented container runtime with free tier | account-specific quotas/pricing need confirmation | deploy image as Code Engine job, verify heartbeat |
| Google e2-micro | Sentinel | Always-free small VM in supported US regions | too small for full scanner; region-limited | run only `cloud_watchdog.py` / lightweight healthcheck |
| AWS Lambda + EventBridge | Fallback job | Free tier may fit short jobs; scheduler available | 15-min timeout, package/container constraints | Lambda container dry-run + scheduled invocation |
| Azure Functions | Fallback job | Free execution grants may fit timer jobs | packaging/storage-account/runtime proof needed | timer trigger proof |
| Cloudflare Workers / Supabase Cron / cron-job.org | Watchdog/trigger | Very useful external heartbeat/trigger | not full Python scanner runtime | HTTP trigger/check stale Supabase heartbeat |
| GitHub/GitLab CI | Disaster backup | Easy scheduled CI | queue delay/drop/minute limits; GitHub dispatch already failed once | scheduled workflow only as backup |

## Provider decision rule

1. First prove the image builds and starts locally without secrets.
2. Then prove an ETF dry-run in the container.
3. Then pick one provider and run one real `--mode etf` with Supabase upload.
4. Only after that, schedule ETF and stocks separately.
5. Only after a scheduled run produces a fresh heartbeat may we call that provider path working for that scope.

## Active falsification requirements

A provider candidate fails as primary if any of these happen:

- container cannot build or start
- runtime lacks enough memory/CPU for ETF or stocks
- scheduler cannot run at needed cadence
- secrets cannot be injected safely
- run finishes but does not publish a fresh Supabase heartbeat
- failure alerts do not reach Telegram
- provider free tier cannot be constrained to zero recurring cost for our measured cadence

## Local proof update — 2026-05-26 15:55–19:12 Europe/Stockholm

Verified locally:

- `ops/cloud_job/cloud_job_entrypoint.sh` and `ops/self_runner/*.sh` pass `bash -n`.
- `.dockerignore` explicitly excludes `.env`, `.env.*`, `*.env`, `*.key`, `*.pem`, `*.sqlite`, `*.db`, `logs/`, `.venv/`, and `.git/`.
- Docker CLI exists (`Docker version 29.2.0`), Docker Desktop daemon was started, and `docker build -f ops/cloud_job/Dockerfile -t swing-terminal-cloud-job:proof .` completed successfully.
- `docker run --rm swing-terminal-cloud-job:proof --help` reached the cloud worker CLI.
- Docker unsafe dry-run guard returned exit code `6` when `--daily-dry-run` was run without `--no-upload` and `--no-notify`.
- Docker safe ETF dry-run completed 5/5 tickers and skipped the production ETF coverage gate for smoke mode.
- Docker timezone check returned `CEST:+0200`, and the safe dry-run produced a Stockholm-time report stamp.
- Docker full ETF scan completed production mode with `ETF coverage OK: total=44 errors=0 skips=0 signals=0` and no upload/notify/restore.
- Docker stock scan completed with mounted cached MLPB events: `stock coverage OK: ok=108 fail=0 stocks=108 current=88`, MLPB gate wrote current gate, and stock terminal export wrote `stock_data.js`.
- Docker all-mode scan completed ETF + stocks together in `2:16.13` with the same no-upload/no-notify/no-restore constraints and mounted cached MLPB events.
- Isolated temp-root smoke copied only intended runtime files; no obvious secret-like files were found.
- Temp-root unsafe dry-run guard returned exit code `6` when `--daily-dry-run` was run without `--no-upload` and `--no-notify`.
- Safe temp-root ETF dry-run completed 5/5 tickers and wrote dry-run report/watchlist without upload/notify/restore.
- `python3 scripts/secret_scan.py` passed after changes: `secret_scan: ok (89 files)`.
- Full test suite passed after Python changes: `240 passed in 5.97s`; focused post-Dockerfile tests passed: `31 passed in 0.04s`.
- `ops/self_runner/healthcheck.sh` passed at 2026-05-26 19:12 CEST with fresh Supabase heartbeats and Telegram readiness:
  - ETF fresh age `3m/50m`, run `20260526T190616-local`, scanned `152`
  - stocks fresh age `3m/130m`, run `20260526T190616-local`, scanned `152`
  - Telegram `OK`

Not yet verified:

- Managed container/VM provider runtime: Oracle A1, Google Cloud Run Jobs, Northflank, IBM Code Engine, AWS Lambda, Azure Functions.
- Scanner-generated signal message from an external provider path.
- Docker stock/all-mode proof used a mounted local `mlpb_final_falsification_events.json`; the exact cloud restore path for that heavy cache still needs a provider run with Supabase restore enabled.

## External proof update — 2026-05-26 19:33–19:47 Europe/Stockholm

Verified on GitHub Actions:

- `Swing Terminal Readiness` run `26464496483` completed successfully from `workflow_dispatch`.
- Readiness checked GitHub Actions secrets without exposing values:
  - `SUPABASE_URL` present
  - `SUPABASE_PUBLISHABLE_KEY` present
  - `SUPABASE_INGEST_TOKEN` present
  - `TELEGRAM_BOT_TOKEN` present
  - `TELEGRAM_CHAT_ID` present
  - Supabase REST reachable
  - Telegram bot OK: `@Swingscanneretf_bot`
  - Telegram send OK: `sendMessage`
- `Swing Terminal Cloud Scanner` run `26464710691` completed successfully from `workflow_dispatch` on branch `main`.
- The external scanner restored state from Supabase, ran ETF scan, got `ETF coverage OK: total=44 errors=0 skips=0 signals=0`, and published state to Supabase.
- Post-run healthcheck confirmed the external ETF heartbeat:
  - ETF fresh age `1m/50m`, run `20260526T193803-26464710691`, scanned `44`
  - stocks fresh age `31m/130m`, run `20260526T190616-local`, scanned `152`
  - Telegram `OK`
- `Swing Terminal Cloud Scanner` run `26464917760` completed `stocks` mode successfully from `workflow_dispatch` on branch `main`.
- The external stock scanner got `stock coverage OK: ok=108 fail=0 stocks=108 current=88`, exported stock terminal data, and published state to Supabase.
- Post-stock healthcheck confirmed the external stock heartbeat:
  - ETF fresh age `8m/50m`, run `20260526T193803-26464710691`, scanned `44`
  - stocks fresh age `0m/130m`, run `20260526T194210-26464917760`, scanned `108`
  - Telegram `OK`

Still not proven by this external proof:

- Docker image deployment to a managed container provider such as Cloud Run Jobs, Northflank, IBM Code Engine, or Oracle A1.
- A scanner-generated signal message from GitHub Actions, because this ETF proof run had `0` signals. Telegram send itself was proven by readiness.
- MLPB optional gate on GitHub Actions: the stock run skipped it because `scripts/mlpb_final_falsification_research.py` and `scripts/mlpb_current_trade_gate.py` were missing on branch `main`.

## MLPB restore hardening update — 2026-05-26 22:03–22:07 Europe/Stockholm

Verified locally:

- `scripts/supabase_io.py` now compresses large JSON artifacts before Supabase upload and decodes them on restore.
- Focused compression/retry/MLPB cloud-gate tests passed as part of `python3 -m pytest -q tests/test_supabase_io.py tests/test_cloud_scan_worker.py tests/test_mlpb_current_trade_gate.py tests/test_secret_scan.py`: `46 passed in 0.26s`.
- Full local suite passed after the changes: `246 passed in 5.66s`.
- Secret scan passed after the changes: `secret_scan: ok (89 files)`.
- Local MLPB current gate executed against the current local event set and wrote:
  - `data/mlpb_current_trade_gate_2026-05-26.txt`
  - `data/mlpb_current_trade_gate.json`
- Local MLPB gate output had `68` current candidates:
  - labels: `56 WATCH_PULLBACK`, `6 NO_TRADE`, `6 EVENT_BLOCKED`
  - Prime tiers: `32 B_WATCH`, `24 A_WATCH`, `6 FAILED_STRUCTURE`, `6 EVENT_BLOCKED`
- Supabase upload/restore path for the heavy MLPB event artifact was tested live:
  - uploaded `mlpb-final-events` with `25409` events
  - compact JSON `raw_bytes=53336269`
  - compressed payload `compressed_bytes=8833696`
  - restored back from Supabase with `25409` events and `68` current candidates
  - restore wrote decoded JSON; the restored payload did not contain the compression marker at top level

Still not proven after this update:

- GitHub Actions branch run with `scripts/mlpb_current_trade_gate.py` present and Supabase compressed artifact restore enabled.
- Managed container/VM provider runtime.
- Scanner-generated signal message from a real scanner signal.
