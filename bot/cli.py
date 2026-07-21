"""Command-line interface.

In GitHub Actions, the workflow triggers `--once` every hour — the runner
is ephemeral, so no long-lived scheduler is needed there. The built-in
APScheduler mode remains available for running the bot on your own
machine or server instead.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from .config import PARIS_TZ, log
from .pipeline import run_cycle
from .poster import build_x_client


def run_scheduler(client, dry_run: bool, run_immediately: bool) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=PARIS_TZ)

    def job():
        try:
            run_cycle(client, dry_run=dry_run)
        except Exception:  # noqa: BLE001
            # A single cycle's unexpected failure must never take the
            # whole scheduler down — log it and wait for the next tick.
            log.exception("Unhandled error during scheduled cycle; will retry next hour.")

    scheduler.add_job(
        job,
        trigger=CronTrigger(minute=0, timezone=PARIS_TZ),
        id="hourly_post",
        next_run_time=datetime.now(PARIS_TZ) if run_immediately else None,
    )

    log.info(
        "Scheduler started: posting on the hour, Europe/Paris time%s.",
        " (running first cycle now)" if run_immediately else "",
    )
    scheduler.start()


def _parse_now_override(now_str: str) -> datetime | None:
    """
    Parse a datetime string (ISO 8601 format) into a timezone-aware datetime.

    Used for testing feed switching with specific times. Only available in debug mode.

    Examples:
      "2026-07-21T10:00:00+02:00"  (WORLD window: 07:00-18:00 Paris)
      "2026-07-21T22:00:00+02:00"  (USA window: 18:00-07:00 Paris)
    """
    try:
        dt = datetime.fromisoformat(now_str)
        if dt.tzinfo is None:
            raise ValueError("Datetime must include timezone (e.g., +02:00)")
        return dt
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime format: {now_str}. "
            f"Use ISO 8601 with timezone (e.g., '2026-07-21T10:00:00+02:00'): {exc}"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated News-to-Tweet Bot")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit (used by GitHub Actions / cron / systemd).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the full pipeline but do not actually post to X.",
    )
    parser.add_argument(
        "--no-immediate", action="store_true",
        help="When starting the local scheduler, wait for the next hour mark instead of posting right away.",
    )
    parser.add_argument(
        "--now", type=str, default=None,
        help=(
            "DEBUG ONLY: Override current time for testing feed switching. "
            "Use ISO 8601 format with timezone, e.g., '2026-07-21T10:00:00+02:00'. "
            "Ignored in GitHub Actions (GITHUB_ACTIONS env var set)."
        ),
    )
    args = parser.parse_args()

    # Prevent --now from accidentally being used in production
    if args.now and os.environ.get("GITHUB_ACTIONS"):
        log.error("--now flag is not allowed in GitHub Actions. Exiting.")
        sys.exit(1)

    # Parse --now or DEBUG_NOW env var override
    now_override = None
    if args.now or os.environ.get("DEBUG_NOW"):
        now_str = args.now or os.environ.get("DEBUG_NOW")
        try:
            now_override = _parse_now_override(now_str)
            log.info("DEBUG: Overriding current time to %s", now_override.isoformat())
        except ValueError as exc:
            log.error(str(exc))
            sys.exit(1)

    try:
        client = build_x_client()
    except RuntimeError as exc:
        log.error(str(exc))
        sys.exit(1)

    if args.once:
        run_cycle(client, dry_run=args.dry_run, now_override=now_override)
    else:
        run_scheduler(client, dry_run=args.dry_run, run_immediately=not args.no_immediate)


if __name__ == "__main__":
    main()
