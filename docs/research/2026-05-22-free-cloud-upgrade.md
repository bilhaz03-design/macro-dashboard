# Free Cloud Upgrade - 2026-05-22

## Decision

Keep GitHub Actions as the Python compute runner and add Cloudflare Workers as
an independent backup clock.

The upgraded chain is:

1. Supabase Cron remains the primary scheduler.
2. Cloudflare Worker runs every 15 minutes as a second scheduler.
3. The Worker reads `scan_runs` in Supabase.
4. If ETF or stock scans are stale during the Stockholm scan window, the Worker
   dispatches the GitHub Actions scanner.
5. GitHub Actions runs the Python scanner and writes state back to Supabase.
6. Telegram remains the notification layer for real scanner events.

## Why Cloudflare Workers

Cloudflare Workers Free currently gives 100,000 requests per day, 5 Cron
Triggers per account, 50 subrequests per invocation, and 10 ms CPU time
according to the official Workers limits page:
https://developers.cloudflare.com/workers/platform/limits/

Our backup clock uses one Cron Trigger, about 96 invocations per day, and
normally 2-4 subrequests per invocation.

GitHub Actions remains the compute engine because the scanner is Python and the
repository is public. GitHub's docs say standard GitHub-hosted runners are free
for public repositories:
https://docs.github.com/en/actions/learn-github-actions/usage-limits-billing-and-administration

Oracle Always Free has stronger raw compute on paper, but it adds VM operations,
capacity risk, SSH hardening, package maintenance, and a broader attack surface.
Oracle's Always Free page is here:
https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

That is not the best first upgrade for this scanner.

Deno Deploy is attractive but would duplicate the same edge-orchestrator role as
Cloudflare. Deno's Deploy docs show cron support:
https://docs.deno.com/deploy/

Cloudflare has the cleaner fit because the free Cron Trigger limit is enough for
this job with one simple Worker.

## Remaining Activation Step

The Worker code is ready, tested, and deployable. To activate it, add Cloudflare
secrets in GitHub and run the manual `Deploy Cloudflare Backup Clock` workflow.
