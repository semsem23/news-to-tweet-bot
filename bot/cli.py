"""Command-line interface.

In GitHub Actions, the workflow triggers `--once` every hour — the runner
is ephemeral, so no long-lived scheduler is needed there. The built-in
APScheduler mode remains available for running the bot on your own
machine or server instead.
"""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()

    try:
        client = build_x_client()
    except RuntimeError as exc:
        log.error(str(exc))
        sys.exit(1)

    if args.once:
        run_cycle(client, dry_run=args.dry_run)
    else:
        run_scheduler(client, dry_run=args.dry_run, run_immediately=not args.no_immediate)


if __name__ == "__main__":
    main()
