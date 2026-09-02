"""ContextSource adapter interface.

To add a new communication platform, create a new file in this package that subclasses
ContextSource and implements search(). Nothing else in the codebase needs to change -
main.py discovers active sources from config and instantiates them by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from statusscan.models import Message


class ContextSource(ABC):
    """One ContextSource instance represents one configured communication-platform
    connection, driven by the config passed to __init__."""

    platform: str = "unknown"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def search(self, keywords: List[str], lookback_days: int) -> List[Message]:
        """Search this platform for messages matching any of `keywords` (task name, project
        name, tags - also matched against channel/thread/mailbox-folder names) within the
        last `lookback_days` days. Return normalized Message objects, most relevant/most
        recent first."""
