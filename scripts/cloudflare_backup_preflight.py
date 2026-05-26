#!/usr/bin/env python3
"""Preflight checks for the Cloudflare backup Worker deployment path.

This script intentionally prints secret *names* and readiness only. It must not
print secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))

from env_loader import load_default_env  # noqa: E402


DEPLOY_REQUIRED_REPO_SECRETS = (
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CF_SUPABASE_URL",
    "CF_SUPABASE_PUBLISHABLE_KEY",
    "CF_SUPABASE_INGEST_TOKEN",
    "CF_GITHUB_ACTIONS_TOKEN",
)

OPTIONAL_REPO_SECRETS = (
    "CF_ADMIN_TOKEN",
)

LOCAL_TO_REPO_SECRET = {
    "SUPABASE_URL": "CF_SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY": "CF_SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_INGEST_TOKEN": "CF_SUPABASE_INGEST_TOKEN",
}


def run_cmd(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
    )


def gh_secret_names(repo: str) -> set[str]:
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI saknas")
    completed = run_cmd(["gh", "secret", "list", "--repo", repo])
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[0] if detail else "gh secret list failed")
    names: set[str] = set()
    for line in completed.stdout.splitlines():
        if line.strip():
            names.add(line.split()[0])
    return names


def sync_supabase_repo_secrets(repo: str, *, dry_run: bool = False) -> list[dict]:
    results: list[dict] = []
    for local_name, repo_name in LOCAL_TO_REPO_SECRET.items():
        value = os.environ.get(local_name, "")
        if not value:
            results.append({"repo_secret": repo_name, "source": local_name, "status": "missing_local"})
            continue
        if dry_run:
            results.append({"repo_secret": repo_name, "source": local_name, "status": "would_set"})
            continue
        completed = run_cmd(["gh", "secret", "set", repo_name, "--repo", repo], input_text=value)
        results.append({
            "repo_secret": repo_name,
            "source": local_name,
            "status": "set" if completed.returncode == 0 else "failed",
            "detail": "" if completed.returncode == 0 else (completed.stderr or completed.stdout).strip().splitlines()[:1],
        })
    return results


def build_status(repo: str) -> dict:
    load_default_env()
    names = gh_secret_names(repo)
    required_missing = [name for name in DEPLOY_REQUIRED_REPO_SECRETS if name not in names]
    optional_missing = [name for name in OPTIONAL_REPO_SECRETS if name not in names]
    local_supabase_ready = {
        local_name: bool(os.environ.get(local_name))
        for local_name in LOCAL_TO_REPO_SECRET
    }
    return {
        "repo": repo,
        "github_cli": bool(shutil.which("gh")),
        "required_repo_secrets": list(DEPLOY_REQUIRED_REPO_SECRETS),
        "required_missing": required_missing,
        "optional_missing": optional_missing,
        "local_supabase_ready": local_supabase_ready,
        "deploy_ready": len(required_missing) == 0,
        "health_url_configured": bool(os.environ.get("CLOUDFLARE_BACKUP_HEALTH_URL")),
    }


def print_text(status: dict, sync_results: Iterable[dict] = ()) -> None:
    print("Cloudflare backup preflight")
    print(f"Repo: {status['repo']}")
    print(f"Deploy ready: {'YES' if status['deploy_ready'] else 'no'}")
    print()
    print("Required GitHub secrets")
    for name in status["required_repo_secrets"]:
        print(f"  {name:<32} {'MISSING' if name in status['required_missing'] else 'OK'}")
    print()
    print("Optional GitHub secrets")
    for name in OPTIONAL_REPO_SECRETS:
        print(f"  {name:<32} {'MISSING' if name in status['optional_missing'] else 'OK'}")
    print()
    print("Local reusable Supabase env")
    for name, ok in status["local_supabase_ready"].items():
        print(f"  {name:<32} {'OK' if ok else 'MISSING'}")
    print(f"  {'CLOUDFLARE_BACKUP_HEALTH_URL':<32} {'OK' if status['health_url_configured'] else 'MISSING'}")
    for item in sync_results:
        print(f"sync {item['repo_secret']}: {item['status']}")
    print()
    if not status["deploy_ready"]:
        print("Next missing required secrets:")
        for name in status["required_missing"]:
            print(f"  - {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Cloudflare backup Worker deploy prerequisites")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "bilhaz03-design/macro-dashboard"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--sync-supabase-secrets",
        action="store_true",
        help="Set CF_SUPABASE_* repository secrets from local SUPABASE_* env values without printing values.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        status = build_status(args.repo)
        sync_results: list[dict] = []
        if args.sync_supabase_secrets:
            sync_results = sync_supabase_repo_secrets(args.repo, dry_run=args.dry_run)
            status = build_status(args.repo)
        if args.json:
            print(json.dumps({"status": status, "sync_results": sync_results}, indent=2, ensure_ascii=False))
        else:
            print_text(status, sync_results)
        return 0 if status["deploy_ready"] else 2
    except Exception as exc:
        print(f"cloudflare_backup_preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
