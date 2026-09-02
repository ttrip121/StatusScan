"""APScheduler-based scheduler: runs the digest pipeline at the times configured in
config/config.yaml (schedule.times, schedule.timezone). Alternative to cron - see README.md
for the plain-cron setup if you'd rather not keep a long-running process.

Usage:

    python -m statusscan.scheduler [--config config/config.yaml]

Runs in the foreground; use your process manager of choice (systemd, supervisor, tmux, a
Docker restart policy, ...) to keep it alive.
"""

from __future__ import annotations

import argparse
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from statusscan.config import Config
from statusscan.main import run_once

logger = logging.getLogger("statusscan.scheduler")


def _parse_hhmm(value: str):
    hour_str, minute_str = value.strip().split(":")
    return int(hour_str), int(minute_str)


def start(config_path: str = "config/config.yaml") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = Config.load(config_path)
    scheduler = BlockingScheduler(timezone=config.timezone)

    for time_str in config.schedule_times:
        hour, minute = _parse_hhmm(time_str)
        scheduler.add_job(
            run_once,
            trigger="cron",
            hour=hour,
            minute=minute,
            kwargs={"config_path": config_path},
            id=f"digest-{time_str}",
            misfire_grace_time=900,
        )
        logger.info("Scheduled digest run at %02d:%02d %s", hour, minute, config.timezone)

    logger.info("Scheduler started. Press Ctrl+C to exit.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the StatusScan scheduler.")
    parser.add_argument(
        "--config", default="config/config.yaml", help="Path to config.yaml (default: config/config.yaml)"
    )
    args = parser.parse_args()
    start(config_path=args.config)


if __name__ == "__main__":
    main()
