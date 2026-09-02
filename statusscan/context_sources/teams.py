"""Microsoft Teams ContextSource adapter, via the Microsoft Graph Search API.

Required Graph application permissions (admin consent required), granted to the same Azure AD
app registration used by OutlookSource:
  - ChannelMessage.Read.All
  - Chat.Read.All
  - Team.ReadBasic.All (to resolve channel/team display names for permalinks)

Config shape (see config/config.example.yaml):

    context_sources:
      teams:
        active: true
        # microsoft_graph block is shared with outlook - see config.example.yaml
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from statusscan.context_sources.base import ContextSource
from statusscan.context_sources.graph_auth import GraphClient
from statusscan.models import Message


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", text or "").strip()


class TeamsSource(ContextSource):
    platform = "teams"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.graph = GraphClient(config["microsoft_graph"])

    def search(self, keywords: List[str], lookback_days: int) -> List[Message]:
        if not keywords:
            return []
        query_string = " OR ".join(f'"{kw}"' if " " in kw else kw for kw in keywords)
        body = {
            "requests": [
                {
                    "entityTypes": ["chatMessage"],
                    "query": {"queryString": query_string},
                    "from": 0,
                    "size": 25,
                }
            ]
        }
        payload = self.graph.post("/search/query", body)
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        messages: List[Message] = []
        for response in payload.get("value", []):
            for container in response.get("hitsContainers", []):
                for hit in container.get("hits", []):
                    resource = hit.get("resource", {})
                    msg = self._to_message(resource)
                    if msg and msg.timestamp >= cutoff:
                        messages.append(msg)

        messages.sort(key=lambda m: m.timestamp, reverse=True)
        return messages

    def _to_message(self, resource: Dict[str, Any]) -> Optional[Message]:
        created = resource.get("createdDateTime") or resource.get("lastModifiedDateTime")
        if not created:
            return None
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        from_user = (resource.get("from") or {}).get("user", {})
        channel_identity = resource.get("channelIdentity") or {}
        chat_id = resource.get("chatId")
        channel_or_thread = channel_identity.get("channelId") or chat_id or "unknown"
        body = (resource.get("body") or {}).get("content", "")

        return Message(
            platform=self.platform,
            channel_or_thread=channel_or_thread,
            author=from_user.get("displayName"),
            timestamp=timestamp,
            text=_strip_html(body),
            permalink=resource.get("webUrl"),
        )
