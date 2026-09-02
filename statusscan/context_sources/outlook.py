"""Outlook ContextSource adapter, via Microsoft Graph. Searches the PM's own mailbox AND every
configured shared mailbox.

Required Graph application permission (admin consent required), granted to the same Azure AD
app registration used by TeamsSource:
  - Mail.Read

Mail.Read at the application level grants read access to every mailbox in the tenant by
default. It is strongly recommended to scope this down with an Exchange Online
"application access policy" restricting the app to only the PM mailbox + the shared mailboxes
listed below - see README.md for the exact PowerShell command.

Config shape (see config/config.example.yaml):

    context_sources:
      outlook:
        active: true
        pm_mailbox: "pm@example.com"
        shared_mailboxes:
          - "projects@example.com"
          - "support@example.com"
        match_folder_names: true   # optional: also pull recent mail from folders whose name
                                    # matches a keyword, in addition to full-text search
        # microsoft_graph block is shared with teams - see config.example.yaml
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from statusscan.context_sources.base import ContextSource
from statusscan.context_sources.graph_auth import GraphClient
from statusscan.models import Message


def _tokenize(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", keyword.lower())


class OutlookSource(ContextSource):
    platform = "outlook"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.graph = GraphClient(config["microsoft_graph"])
        self.pm_mailbox = config["pm_mailbox"]
        self.shared_mailboxes: List[str] = config.get("shared_mailboxes", [])
        self.match_folder_names = config.get("match_folder_names", True)

    def search(self, keywords: List[str], lookback_days: int) -> List[Message]:
        if not keywords:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        mailboxes = [self.pm_mailbox, *self.shared_mailboxes]

        messages: List[Message] = []
        seen_ids = set()
        for mailbox in mailboxes:
            for msg in self._search_mailbox(mailbox, keywords, cutoff):
                if msg["id"] in seen_ids:
                    continue
                seen_ids.add(msg["id"])
                messages.append(self._to_message(mailbox, msg))

            if self.match_folder_names:
                for msg in self._search_matching_folders(mailbox, keywords, cutoff):
                    if msg["id"] in seen_ids:
                        continue
                    seen_ids.add(msg["id"])
                    messages.append(self._to_message(mailbox, msg))

        messages.sort(key=lambda m: m.timestamp, reverse=True)
        return messages

    def _search_mailbox(
        self, mailbox: str, keywords: List[str], cutoff: datetime
    ) -> List[Dict[str, Any]]:
        query = " OR ".join(f'"{kw}"' for kw in keywords)
        payload = self.graph.get(
            f"/users/{mailbox}/messages",
            {"$search": f'"{query}"', "$top": 25},
        )
        return [m for m in payload.get("value", []) if self._received_after(m, cutoff)]

    def _search_matching_folders(
        self, mailbox: str, keywords: List[str], cutoff: datetime
    ) -> List[Dict[str, Any]]:
        tokens = [_tokenize(kw) for kw in keywords if _tokenize(kw)]
        if not tokens:
            return []
        folders_payload = self.graph.get(f"/users/{mailbox}/mailFolders", {"$top": 100})
        matched_folder_ids = [
            f["id"]
            for f in folders_payload.get("value", [])
            if any(tok and tok in _tokenize(f.get("displayName", "")) for tok in tokens)
        ]

        results: List[Dict[str, Any]] = []
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        for folder_id in matched_folder_ids:
            payload = self.graph.get(
                f"/users/{mailbox}/mailFolders/{folder_id}/messages",
                {"$filter": f"receivedDateTime ge {cutoff_str}", "$top": 25},
            )
            results.extend(payload.get("value", []))
        return results

    @staticmethod
    def _received_after(message: Dict[str, Any], cutoff: datetime) -> bool:
        received = message.get("receivedDateTime")
        if not received:
            return False
        return datetime.fromisoformat(received.replace("Z", "+00:00")) >= cutoff

    def _to_message(self, mailbox: str, raw: Dict[str, Any]) -> Message:
        sender = (raw.get("from") or {}).get("emailAddress", {})
        received = raw.get("receivedDateTime")
        timestamp = (
            datetime.fromisoformat(received.replace("Z", "+00:00"))
            if received
            else datetime.now(timezone.utc)
        )
        subject = raw.get("subject", "(no subject)")
        return Message(
            platform=self.platform,
            channel_or_thread=f"{mailbox}: {subject}",
            author=sender.get("name") or sender.get("address"),
            timestamp=timestamp,
            text=raw.get("bodyPreview", ""),
            permalink=raw.get("webLink"),
        )
