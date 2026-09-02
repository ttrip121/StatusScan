"""Ranks flagged tasks into the three digest sections and renders the HTML email body."""

from __future__ import annotations

import html
from datetime import date
from typing import Dict, List

from statusscan.models import FlaggedTask

SECTION_EXTERNAL = "external"
SECTION_PM = "pm"
SECTION_NO_CONTEXT = "no_context_found"

SECTION_TITLES = {
    SECTION_EXTERNAL: "Waiting on others — follow up needed",
    SECTION_PM: "Needs your attention",
    SECTION_NO_CONTEXT: "No context found — check manually",
}


def _sort_key(flagged: FlaggedTask, today: date):
    # Higher days-late first, client-facing tasks first within the same lateness.
    return (-flagged.task.days_late(today), 0 if flagged.task.client_facing else 1)


def rank_flagged_tasks(flagged_tasks: List[FlaggedTask], today: date) -> Dict[str, List[FlaggedTask]]:
    """Sort flagged tasks into the three digest buckets, each ranked by urgency."""
    buckets: Dict[str, List[FlaggedTask]] = {
        SECTION_EXTERNAL: [],
        SECTION_PM: [],
        SECTION_NO_CONTEXT: [],
    }

    for flagged in flagged_tasks:
        if flagged.classification is None:
            buckets[SECTION_NO_CONTEXT].append(flagged)
        elif flagged.classification.blocked_on == "external":
            buckets[SECTION_EXTERNAL].append(flagged)
        else:  # "pm" or "unclear" both land in "Needs your attention"
            buckets[SECTION_PM].append(flagged)

    for bucket in buckets.values():
        bucket.sort(key=lambda f: _sort_key(f, today))

    return buckets


def _lateness_label(flagged: FlaggedTask, today: date) -> str:
    days_late = flagged.task.days_late(today)
    if days_late <= 0:
        return "Due today"
    if days_late == 1:
        return "1 day late"
    return f"{days_late} days late"


def _task_row(flagged: FlaggedTask, today: date, show_reason: bool, show_waiting_on: bool) -> str:
    task = flagged.task
    name = html.escape(task.name)
    project = html.escape(task.project or "")
    assignee = html.escape(task.assignee or "Unassigned")
    lateness = _lateness_label(flagged, today)
    client_badge = (
        '<span style="background:#fde8e8;color:#c53030;border-radius:3px;'
        'padding:1px 6px;font-size:11px;margin-left:6px;">CLIENT-FACING</span>'
        if task.client_facing
        else ""
    )
    link = f'<a href="{html.escape(task.url)}">{name}</a>' if task.url else name

    extra_rows = ""
    if show_waiting_on and flagged.classification and flagged.classification.waiting_on:
        extra_rows += (
            f'<div style="color:#555;font-size:13px;">Waiting on: '
            f"<strong>{html.escape(flagged.classification.waiting_on)}</strong></div>"
        )
    if show_reason and flagged.classification:
        extra_rows += (
            f'<div style="color:#555;font-size:13px;">{html.escape(flagged.classification.reason)}</div>'
        )
        extra_rows += (
            f'<div style="color:#999;font-size:11px;">Confidence: '
            f"{html.escape(flagged.classification.confidence or 'n/a')}</div>"
        )

    permalink_html = ""
    if flagged.matched_messages:
        top_msg = flagged.matched_messages[0]
        if top_msg.permalink:
            permalink_html = (
                f'<div style="font-size:12px;">'
                f'<a href="{html.escape(top_msg.permalink)}">View source thread/email ({html.escape(top_msg.platform)})</a>'
                f"</div>"
            )

    return f"""
    <tr>
      <td style="padding:10px 8px;border-bottom:1px solid #eee;vertical-align:top;">
        <div style="font-weight:600;">{link}{client_badge}</div>
        <div style="color:#777;font-size:12px;">{project} • {assignee} • {task.source_platform}</div>
        {extra_rows}
        {permalink_html}
      </td>
      <td style="padding:10px 8px;border-bottom:1px solid #eee;vertical-align:top;white-space:nowrap;text-align:right;color:#c53030;font-weight:600;">
        {lateness}
      </td>
    </tr>"""


def _section_html(section_key: str, flagged_tasks: List[FlaggedTask], today: date) -> str:
    title = SECTION_TITLES[section_key]
    if not flagged_tasks:
        body = '<tr><td style="padding:10px 8px;color:#999;">Nothing here. 🎉</td></tr>'
    else:
        show_reason = section_key in (SECTION_EXTERNAL, SECTION_PM)
        show_waiting_on = section_key == SECTION_EXTERNAL
        body = "\n".join(
            _task_row(f, today, show_reason, show_waiting_on) for f in flagged_tasks
        )

    return f"""
    <h2 style="font-size:16px;margin:24px 0 8px;color:#222;">{html.escape(title)} ({len(flagged_tasks)})</h2>
    <table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:14px;">
      {body}
    </table>"""


def build_html_digest(buckets: Dict[str, List[FlaggedTask]], today: date) -> str:
    """Assemble the full HTML digest email body from the three ranked buckets."""
    total = sum(len(v) for v in buckets.values())
    sections_html = "".join(
        _section_html(key, buckets.get(key, []), today)
        for key in (SECTION_EXTERNAL, SECTION_PM, SECTION_NO_CONTEXT)
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7f7f7;">
  <div style="max-width:680px;margin:0 auto;padding:24px;background:#ffffff;font-family:Arial,Helvetica,sans-serif;">
    <h1 style="font-size:20px;margin:0 0 4px;color:#111;">StatusScan</h1>
    <div style="color:#777;font-size:13px;margin-bottom:8px;">{today.strftime('%A, %B %d, %Y')} • {total} flagged task(s)</div>
    {sections_html}
  </div>
</body>
</html>"""
