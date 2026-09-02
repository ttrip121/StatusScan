"""Orchestrates one full digest run: fetch tasks -> flag -> search context -> classify ->
rank -> build digest -> email it.

Run directly for a one-off pass:

    python -m statusscan.main

Or import run_once() from scheduler.py to run on a schedule.
"""

from __future__ import annotations

import argparse
import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Type

from statusscan.classifier import Classifier
from statusscan.config import Config
from statusscan.context_sources.base import ContextSource
from statusscan.context_sources.outlook import OutlookSource
from statusscan.context_sources.slack import SlackSource
from statusscan.context_sources.teams import TeamsSource
from statusscan.digest import build_html_digest, rank_flagged_tasks
from statusscan.models import FlaggedTask, Task
from statusscan.run_history import record_run
from statusscan.settings import DEFAULT_DETAIL_LEVEL, SETTINGS_PATH_DEFAULT, load_or_bootstrap_settings
from statusscan.task_sources.asana import AsanaSource
from statusscan.task_sources.base import TaskSource
from statusscan.task_sources.monday import MondaySource

logger = logging.getLogger("statusscan")

TASK_SOURCE_REGISTRY: Dict[str, Type[TaskSource]] = {
    "asana": AsanaSource,
    "monday": MondaySource,
}

CONTEXT_SOURCE_REGISTRY: Dict[str, Type[ContextSource]] = {
    "slack": SlackSource,
    "teams": TeamsSource,
    "outlook": OutlookSource,
}


def build_task_sources(config: Config) -> List[TaskSource]:
    sources = []
    for name, cfg in config.active_task_sources().items():
        cls = TASK_SOURCE_REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown task source '%s' in config, skipping", name)
            continue
        sources.append(cls(cfg))
    return sources


def build_context_sources(config: Config) -> List[ContextSource]:
    sources = []
    for name, cfg in config.active_context_sources().items():
        cls = CONTEXT_SOURCE_REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown context source '%s' in config, skipping", name)
            continue
        sources.append(cls(cfg))
    return sources


def fetch_all_tasks(task_sources: List[TaskSource]) -> List[Task]:
    tasks: List[Task] = []
    for source in task_sources:
        try:
            fetched = source.fetch_tasks()
            logger.info("Fetched %d open task(s) from %s", len(fetched), source.source_platform)
            tasks.extend(fetched)
        except Exception:
            logger.exception("Failed to fetch tasks from %s", source.source_platform)
    return tasks


def flag_late_or_due_today(tasks: List[Task], today: date) -> List[Task]:
    """Flag a task if its due date is today or in the past. No stage/status logic - a
    late/due-today due_date is the only trigger."""
    return [t for t in tasks if t.due_date is not None and t.due_date <= today]


def gather_context(
    task: Task, context_sources: List[ContextSource], lookback_days: int
) -> List:
    keywords = task.keywords()
    messages = []
    for source in context_sources:
        try:
            found = source.search(keywords, lookback_days)
            messages.extend(found)
        except Exception:
            logger.exception("Context search failed on %s for task '%s'", source.platform, task.name)
    messages.sort(key=lambda m: m.timestamp, reverse=True)
    return messages


def run_once(
    config_path: str = "config/config.yaml", settings_path: str = SETTINGS_PATH_DEFAULT
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = Config.load(config_path)
    settings = load_or_bootstrap_settings(config, settings_path)
    config.apply_settings_overrides(settings)
    today = date.today()

    task_sources = build_task_sources(config)
    context_sources = build_context_sources(config)

    if not task_sources:
        logger.warning("No active task sources configured - nothing to do.")
        return

    all_tasks = fetch_all_tasks(task_sources)
    flagged = flag_late_or_due_today(all_tasks, today)
    logger.info("%d of %d task(s) are late or due today", len(flagged), len(all_tasks))

    detail_level = settings.get("detail_level", DEFAULT_DETAIL_LEVEL)
    classifier = Classifier(
        api_key=config.anthropic_api_key, model=config.anthropic_model, detail_level=detail_level
    )

    flagged_tasks: List[FlaggedTask] = []
    for task in flagged:
        matched_messages = (
            gather_context(task, context_sources, config.lookback_days) if context_sources else []
        )
        flagged_task = FlaggedTask(task=task, matched_messages=matched_messages)

        if matched_messages:
            try:
                flagged_task.classification = classifier.classify(task, matched_messages, today)
            except Exception:
                logger.exception("Classification failed for task '%s'; treating as unclear", task.name)
        flagged_tasks.append(flagged_task)

    synthesis = None
    if detail_level == "most" and flagged_tasks:
        try:
            synthesis = classifier.synthesis_pass(flagged_tasks, today)
        except Exception:
            logger.exception("Synthesis pass failed; digest will omit the Insights section")

    buckets = rank_flagged_tasks(flagged_tasks, today)
    html_body = build_html_digest(buckets, today, synthesis=synthesis)

    sent_count = send_email(config, html_body, today)
    record_run(flagged_count=len(flagged_tasks), sent_count=sent_count)
    logger.info("Digest sent to %d recipient(s): %d waiting-on-others, %d needs-attention, %d no-context-found",
                sent_count, len(buckets["external"]), len(buckets["pm"]), len(buckets["no_context_found"]))


def send_email(config: Config, html_body: str, today: date) -> int:
    """Returns the number of recipients the digest was sent to (0 if skipped or failed)."""
    recipients = config.email_recipients
    if not recipients:
        logger.warning("No email recipients configured - skipping send. Digest was still built.")
        return 0

    smtp_cfg = config.smtp
    message = MIMEMultipart("alternative")
    message["Subject"] = f"StatusScan — {today.strftime('%b %d, %Y')}"
    message["From"] = smtp_cfg["from_address"]
    message["To"] = ", ".join(recipients)
    message.attach(MIMEText(html_body, "html"))

    host = smtp_cfg["host"]
    port = int(smtp_cfg.get("port", 587))
    use_tls = smtp_cfg.get("use_tls", True)

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            server.starttls()
        username = smtp_cfg.get("username")
        password = smtp_cfg.get("password")
        if username and password:
            server.login(username, password)
        server.sendmail(smtp_cfg["from_address"], recipients, message.as_string())

    return len(recipients)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one StatusScan pass.")
    parser.add_argument(
        "--config", default="config/config.yaml", help="Path to config.yaml (default: config/config.yaml)"
    )
    parser.add_argument(
        "--settings", default=SETTINGS_PATH_DEFAULT, help="Path to settings.json (default: settings.json)"
    )
    args = parser.parse_args()
    run_once(config_path=args.config, settings_path=args.settings)


if __name__ == "__main__":
    main()
