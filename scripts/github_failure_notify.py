#!/usr/bin/env python3
"""Send a Telegram alert when a GitHub Actions scanner job fails."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SWING_TERMINAL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))


def compact(value: str | None, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text else default


def short_sha(value: str | None) -> str:
    text = compact(value, "")
    return text[:12] if text else "unknown"


def build_failure_message(args: argparse.Namespace) -> tuple[str, str, str]:
    workflow = compact(args.workflow)
    job = compact(args.job)
    title = f"{workflow} FAILED"
    subtitle = job
    lines = [
        f"Repo: {compact(args.repo)}",
        f"Event: {compact(args.event)}",
        f"Mode: {compact(args.mode)}",
        f"Actor: {compact(args.actor)}",
        f"SHA: {short_sha(args.sha)}",
        f"Run: {compact(args.run_url)}",
    ]
    if args.extra:
        lines.append(f"Extra: {args.extra.strip()}")
    return title, subtitle, "\n".join(lines)


def push_telegram(title: str, subtitle: str, body: str) -> None:
    import run_scan_notify  # noqa: WPS433

    run_scan_notify.push_telegram(title, subtitle, body)


def notify_failure(args: argparse.Namespace) -> int:
    title, subtitle, body = build_failure_message(args)
    if args.dry_run or os.environ.get("GITHUB_FAILURE_NOTIFY_DRY_RUN") == "1":
        print("\n".join([title, subtitle, body]), flush=True)
        return 0

    try:
        push_telegram(title, subtitle, body)
    except Exception as exc:
        print(f"[github_failure_notify] telegram failed: {exc}", file=sys.stderr, flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notify Telegram about a GitHub Actions workflow failure")
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", "GitHub Actions"))
    parser.add_argument("--job", default=os.environ.get("GITHUB_JOB", "job"))
    parser.add_argument("--mode", default=os.environ.get("SWING_TERMINAL_MODE", "unknown"))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "unknown"))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "unknown"))
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", "unknown"))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--extra", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    return notify_failure(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
