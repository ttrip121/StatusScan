"""Calls the Anthropic API to classify why a flagged task appears stuck, using the task's
own details plus the top matching context message(s) pulled from Slack/Teams/Outlook.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

from statusscan.models import Classification, Message, Task

# How many of the most recent matched messages to include in the prompt per task - keeps the
# request small and focused on the freshest signal.
MAX_MESSAGES_IN_PROMPT = 3

SYSTEM_PROMPT = """You are helping a project manager (PM) triage a list of overdue or \
due-today tasks. For each task you are given the task's details and the most relevant \
messages found in Slack, Microsoft Teams, and Outlook that mention it.

Decide whether the task is stuck because of something the PM needs to do (blocked_on: "pm"), \
because it is waiting on someone or something else (blocked_on: "external"), or because the \
messages don't make it clear (blocked_on: "unclear").

If blocked_on is "external", extract who or what it is waiting on (a person, team, or \
external entity) into waiting_on - be as specific as the messages allow (e.g. "Legal team", \
"client's IT department", "director approval"). Otherwise waiting_on should be null.

Always write a one-sentence, plain-English reason describing the holdup, written for a PM \
skimming a digest (e.g. "Waiting on director approval to access the client database").

Set confidence to "high" only when the messages directly and unambiguously explain the \
holdup, "medium" when they strongly suggest it, and "low" when you are mostly guessing."""


class ClassificationResult(BaseModel):
    blocked_on: Literal["pm", "external", "unclear"]
    waiting_on: Optional[str] = None
    reason: str
    confidence: Literal["high", "medium", "low"]


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


class Classifier:
    def __init__(self, api_key: str, model: str = "claude-opus-5"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def classify(self, task: Task, matched_messages: List[Message], today: date) -> Classification:
        """Classify one flagged task. Caller is expected to have already filtered out tasks
        with no matched context - those are marked no_context_found without an API call."""
        prompt = _build_prompt(task, matched_messages, today)

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
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
