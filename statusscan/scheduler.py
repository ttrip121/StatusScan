"""Poller: checks settings.json every 15 minutes and runs the digest pipeline whenever the
current time has reached a configured sweep slot. This replaces fixed-time cron scheduling so
that frequency changes made in settings_app.py (the Streamlit settings UI) take effect
without restarting this process.

Usage:

    python -m statusscan.scheduler [--config config/config.yaml] [--settings settings.json]

Runs in the foreground; use your process manager of choice (systemd, supervisor, tmux, a
Docker restart policy, ...) to keep it alive. See README.md for a systemd unit example.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from typing import List, Set, Tuple

from statusscan.config import Config
from statusscan.main import run_once
from statusscan.settings import SETTINGS_PATH_DEFAULT, load_or_bootstrap_settings, load_settings

logger = logging.getLogger("statusscan.scheduler")

POLL_INTERVAL_SECONDS = 15 * 60

# (date-of-slot, "HH:MM") pairs already fired - prevents re-firing the same slot on every
# poll tick for the rest of the day once it's due.
FiredKey = Tuple[str, str]


def _parse_hhmm(value: str):
    hour_str, minute_str = value.strip().split(":")
    return int(hour_str), int(minute_str)


def due_slots(sweep_times: List[str], now: datetime, fired_today: Set[FiredKey]) -> List[Tuple[FiredKey, str]]:
    """Return the (key, slot) pairs that are due as of `now` and haven't fired yet today.
    A slot is due once `now` has reached it - this "catch up" semantics means a slot is never
    missed just because the poller happened to check a few minutes after it, at the cost of
    up to ~15 minutes of delivery lag versus the exact configured time."""
    today_key = now.date().isoformat()
    due = []
    for slot in sweep_times:
        try:
            hour, minute = _parse_hhmm(slot)
        except ValueError:
            logger.warning("Ignoring malformed sweep time %r in settings.json", slot)
            continue
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        key = (today_key, slot)
        if now >= slot_dt and key not in fired_today:
            due.append((key, slot))
    return due


def poll_forever(config_path: str = "config/config.yaml", settings_path: str = SETTINGS_PATH_DEFAULT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Make sure settings.json exists before the loop starts, so the poller doesn't sit idle
    # with an empty sweep_times list just because the settings UI has never been opened.
    load_or_bootstrap_settings(Config.load(config_path), settings_path)

    fired_today: Set[FiredKey] = set()
    last_date = datetime.now().date()

    logger.info(
        "StatusScan poller started - checking %s every %d minutes",
        settings_path,
        POLL_INTERVAL_SECONDS // 60,
    )

    try:
        while True:
            now = datetime.now()
            if now.date() != last_date:
                fired_today.clear()
                last_date = now.date()

            settings = load_settings(settings_path) or {}
            sweep_times = settings.get("sweep_times", [])

            for key, slot in due_slots(sweep_times, now, fired_today):
                logger.info("Sweep slot %s is due - running the digest pipeline", slot)
                fired_today.add(key)
                try:
                    run_once(config_path=config_path, settings_path=settings_path)
                except Exception:
                    logger.exception("Digest run failed for slot %s", slot)

            time.sleep(POLL_INTERVAL_SECONDS)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Poller stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the StatusScan settings poller.")
    parser.add_argument(
        "--config", default="config/config.yaml", help="Path to config.yaml (default: config/config.yaml)"
    )
    parser.add_argument(
        "--settings", default=SETTINGS_PATH_DEFAULT, help="Path to settings.json (default: settings.json)"
    )
    args = parser.parse_args()
    poll_forever(config_path=args.config, settings_path=args.settings)


if __name__ == "__main__":
    main()
