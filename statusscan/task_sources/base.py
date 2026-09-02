"""TaskSource adapter interface.

To add a new PM tool (ClickUp, Jira, Wrike, ...), create a new file in this package that
subclasses TaskSource and implements the abstract methods below. Nothing else in the codebase
needs to change - main.py discovers active sources from config and instantiates them by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Dict, List, Optional

from statusscan.models import Task


class TaskSource(ABC):
    """One TaskSource instance represents one configured PM-tool connection
    (a specific workspace/board scope), driven by the config passed to __init__.
    """

    source_platform: str = "unknown"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    # -- required adapter methods --------------------------------------------
    # These are the platform-specific primitives every adapter must implement.

    @abstractmethod
    def get_open_tasks(self) -> List[Any]:
        """Return raw (platform-native) open/incomplete task objects within this
        adapter's configured scope (projects/boards)."""

    @abstractmethod
    def get_due_date(self, raw_task: Any) -> Optional[date]:
        """Extract the due date (date, no time) from a raw task, or None if unset."""

    @abstractmethod
    def get_assignee(self, raw_task: Any) -> Optional[str]:
        """Extract a human-readable assignee name/email from a raw task, or None."""

    @abstractmethod
    def get_tags(self, raw_task: Any) -> List[str]:
        """Extract tag/label strings from a raw task."""

    # -- remaining fields needed to fill out the common Task shape -----------
    # Given reasonable defaults so simple adapters can skip overriding them,
    # but most real adapters will want to override get_id/get_name/get_url.

    @abstractmethod
    def get_id(self, raw_task: Any) -> str:
        """Stable unique id for the task within its platform."""

    @abstractmethod
    def get_name(self, raw_task: Any) -> str:
        """Human-readable task title."""

    def get_project(self, raw_task: Any) -> Optional[str]:
        return None

    def get_url(self, raw_task: Any) -> Optional[str]:
        return None

    def get_client_facing(self, raw_task: Any) -> bool:
        """Whether this task is client-facing. Default heuristic: a 'client-facing' tag.
        Adapters may override with a platform-specific custom field lookup."""
        return any(t.strip().lower() == "client-facing" for t in self.get_tags(raw_task))

    # -- normalization entry point used by main.py ---------------------------

    def fetch_tasks(self) -> List[Task]:
        """Pull raw tasks and normalize every one into the common Task shape."""
        normalized: List[Task] = []
        for raw in self.get_open_tasks():
            normalized.append(
                Task(
                    id=self.get_id(raw),
                    name=self.get_name(raw),
                    project=self.get_project(raw),
                    due_date=self.get_due_date(raw),
                    assignee=self.get_assignee(raw),
                    tags=self.get_tags(raw),
                    client_facing=self.get_client_facing(raw),
                    url=self.get_url(raw),
                    source_platform=self.source_platform,
                )
            )
        return normalized
