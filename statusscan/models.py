"""Common data shapes shared by every adapter and by the core pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional


@dataclass
class Task:
    """Normalized task shape produced by every TaskSource adapter."""

    id: str
    name: str
    project: Optional[str]
    due_date: Optional[date]
    assignee: Optional[str]
    tags: List[str]
    client_facing: bool
    url: Optional[str]
    source_platform: str

    def keywords(self) -> List[str]:
        """Keyword set used for context search: task name, project name, tags."""
        words: List[str] = []
        if self.name:
            words.append(self.name)
        if self.project:
            words.append(self.project)
        words.extend(self.tags)
        return [w for w in words if w]

    def days_late(self, today: date) -> int:
        """Positive integer number of days late; 0 if due today; negative should not occur
        for flagged tasks but is safe to compute."""
        if not self.due_date:
            return 0
        return (today - self.due_date).days


@dataclass
class Message:
    """Normalized message shape produced by every ContextSource adapter."""

    platform: str
    channel_or_thread: str
    author: Optional[str]
    timestamp: datetime
    text: str
    permalink: Optional[str]


class BlockedOn(str, Enum):
    PM = "pm"
    EXTERNAL = "external"
    UNCLEAR = "unclear"
    NO_CONTEXT_FOUND = "no_context_found"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Classification:
    """Result of running a flagged task + matched context through classifier.py."""

    blocked_on: str  # one of BlockedOn values
    waiting_on: Optional[str]
    reason: str
    confidence: Optional[str]  # one of Confidence values, None for no_context_found


@dataclass
class FlaggedTask:
    """A task that triggered the late/due-today rule, plus everything gathered about it."""

    task: Task
    matched_messages: List[Message] = field(default_factory=list)
    classification: Optional[Classification] = None


@dataclass
class Synthesis:
    """Result of classifier.py's synthesis_pass() - only produced at the "most" detail level.
    Rendered as the digest's "Insights & Suggested Next Steps" section."""

    patterns: List[str]
    next_steps: List[str]
