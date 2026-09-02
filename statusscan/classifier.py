"""Calls the Anthropic API to classify why a flagged task appears stuck, using the task's
own details plus the top matching context message(s) pulled from Slack/Teams/Outlook.

The `detail_level` setting ("some" | "more" | "most", from settings.json - see settings.py)
controls how much narrative framing goes into each classification's `reason`, and whether an
additional cross-task synthesis_pass() runs after every task has been classified.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

from statusscan.models import Classification, FlaggedTask, Message, Synthesis, Task
from statusscan.settings import DEFAULT_DETAIL_LEVEL, VALID_DETAIL_LEVELS

# How many of the most recent matched messages to include in the prompt per task - keeps the
# request small and focused on the freshest signal.
MAX_MESSAGES_IN_PROMPT = 3

SYSTEM_PROMPT_BASE = """You are helping a project manager (PM) triage a list of overdue or \
due-today tasks. For each task you are given the task's details and the most relevant \
messages found in Slack, Microsoft Teams, and Outlook that mention it.

Decide whether the task is stuck because of something the PM needs to do (blocked_on: "pm"), \
because it is waiting on someone or something else (blocked_on: "external"), or because the \
messages don't make it clear (blocked_on: "unclear").

If blocked_on is "external", extract who or what it is waiting on (a person, team, or \
external entity) into waiting_on - be as specific as the messages allow (e.g. "Legal team", \
"client's IT department", "director approval"). Otherwise waiting_on should be null.

Set confidence to "high" only when the messages directly and unambiguously explain the \
holdup, "medium" when they strongly suggest it, and "low" when you are mostly guessing."""

# Only the `reason` field's style changes between tiers - the structured schema below (and
# therefore what fields the digest can render) is identical across all three detail levels.
DETAIL_LEVEL_PROMPT_ADDENDA = {
    "some": (
        'Write `reason` as exactly one flat, factual sentence - state what is blocking the '
        "task and nothing else. No framing, no narrative, no editorializing."
    ),
    "more": (
        "Write `reason` as 2-3 sentences of narrative framing - give the PM enough context "
        "to understand the situation at a glance, not just the bare fact."
    ),
    "most": (
        "Write `reason` as 2-3 sentences of narrative framing - give the PM enough context "
        "to understand the situation at a glance, not just the bare fact."
    ),
}

SYNTHESIS_SYSTEM_PROMPT = """You are helping a project manager (PM) spot patterns across a \
batch of stuck tasks that have already been triaged. You will be given every flagged task \
along with its blocked_on classification, who/what it's waiting on, and the reason.

Identify cross-task patterns worth the PM's attention - for example, several tasks blocked on \
the same person or team, a recurring type of holdup, or a project with a cluster of stuck \
tasks. Only list a pattern if it's actually supported by two or more tasks; if there are none, \
return an empty list rather than inventing one.

Then suggest 2 to 4 concrete, specific next steps the PM could take today, ordered by impact. \
Each step should name who to contact or what to do - not a vague generality."""


class ClassificationResult(BaseModel):
    blocked_on: Literal["pm", "external", "unclear"]
    waiting_on: Optional[str] = None
    reason: str
    confidence: Literal["high", "medium", "low"]


class SynthesisResult(BaseModel):
    patterns: List[str]
    next_steps: List[str]


def _format_messages(messages: List[Message]) -> str:
    if not messages:
        return "(no matching messages found)"
    lines = []
    for msg in messages[:MAX_MESSAGES_IN_PROMPT]:
        lines.append(
            f"- [{msg.platform} | {msg.channel_or_thread} | {msg.timestamp.isoformat()} | "
            f"{msg.author or 'unknown author'}]: {msg.text.strip()[:1000]}"
        )
    return "\n".join(lines)


def _build_prompt(task: Task, matched_messages: List[Message], today: date) -> str:
    days_late = task.days_late(today)
    lateness = "due today" if days_late <= 0 else f"{days_late} day(s) late"
    return f"""Task: {task.name}
Project: {task.project or "(none)"}
Assignee: {task.assignee or "(unassigned)"}
Tags: {", ".join(task.tags) or "(none)"}
Client-facing: {"yes" if task.client_facing else "no"}
Status: {lateness}
Source: {task.source_platform}

Matching messages:
{_format_messages(matched_messages)}"""


def _format_flagged_task_for_synthesis(flagged: FlaggedTask, today: date) -> str:
    task = flagged.task
    days_late = task.days_late(today)
    lateness = "due today" if days_late <= 0 else f"{days_late} day(s) late"
    if flagged.classification:
        c = flagged.classification
        status = f'blocked_on={c.blocked_on}, waiting_on={c.waiting_on or "n/a"}, reason="{c.reason}"'
    else:
        status = "no matching context found"
    return f"- {task.name} ({task.project or 'no project'}, {lateness}): {status}"


class Classifier:
    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        detail_level: str = DEFAULT_DETAIL_LEVEL,
    ):
        if detail_level not in VALID_DETAIL_LEVELS:
            raise ValueError(
                f"Invalid detail_level {detail_level!r}; must be one of {VALID_DETAIL_LEVELS}"
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.detail_level = detail_level

    def classify(self, task: Task, matched_messages: List[Message], today: date) -> Classification:
        """Classify one flagged task. Caller is expected to have already filtered out tasks
        with no matched context - those are marked no_context_found without an API call."""
        prompt = _build_prompt(task, matched_messages, today)
        system_prompt = f"{SYSTEM_PROMPT_BASE}\n\n{DETAIL_LEVEL_PROMPT_ADDENDA[self.detail_level]}"

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            output_format=ClassificationResult,
        )
        result = response.parsed_output

        return Classification(
            blocked_on=result.blocked_on,
            waiting_on=result.waiting_on,
            reason=result.reason,
            confidence=result.confidence,
        )

    def synthesis_pass(self, flagged_tasks: List[FlaggedTask], today: date) -> Optional[Synthesis]:
        """Only meaningful at detail_level "most": after every flagged task has been
        classified, send the full set to Claude in one additional call to surface cross-task
        patterns and concrete next steps. Returns None if there's nothing to synthesize."""
        if not flagged_tasks:
            return None

        task_lines = "\n".join(
            _format_flagged_task_for_synthesis(f, today) for f in flagged_tasks
        )
        prompt = f"Flagged tasks:\n{task_lines}"

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=SynthesisResult,
        )
        result = response.parsed_output

        return Synthesis(patterns=result.patterns, next_steps=result.next_steps)
