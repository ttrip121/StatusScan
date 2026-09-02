"""Slack ContextSource adapter.

Required scope: Slack's search.messages endpoint only works with a *user* token (xoxp-...),
not a bot token - bots cannot search. Create the token via a Slack app installed with the
`search:read` user scope, authorized by the PM's own account (search results are scoped to
whatever that user can see, which is the desired behavior here). To also match on channel
names, the token additionally needs the `channels:read` and `groups:read` user scopes.

Config shape (see config/config.example.yaml):

    context_sources:
      slack:
        active: true
        user_token: ${SLACK_USER_TOKEN}
        match_channel_names: true   # optional: also pull recent history from channels whose
                                     # name matches a keyword, in addition to full-text search
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

from statusscan.context_sources.base import ContextSource
from statusscan.models import Message

SLACK_API_BASE = "https://slack.com/api"


def _tokenize(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", keyword.lower())


class SlackSource(ContextSource):
    platform = "slack"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.user_token = config["user_token"]
        self.match_channel_names = config.get("match_channel_names", True)
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self.user_token}"})

    def _call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._session.get(f"{SLACK_API_BASE}/{method}", params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Slack API error on {method}: {payload.get('error')}")
        return payload

    def search(self, keywords: List[str], lookback_days: int) -> List[Message]:
        if not keywords:
            return []
        messages: List[Message] = []
        seen_keys = set()
        after_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        quoted = [f'"{kw}"' if " " in kw else kw for kw in keywords]
        query = f"({' OR '.join(quoted)}) after:{after_date}"
        payload = self._call(
            "search.messages", {"query": query, "sort": "timestamp", "count": 20}
        )
        for match in payload.get("messages", {}).get("matches", []):
            key = (match.get("channel", {}).get("id"), match.get("ts"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            messages.append(self._to_message(match))

        if self.match_channel_names:
            messages.extend(
                self._search_matching_channels(keywords, lookback_days, seen_keys)
            )

        messages.sort(key=lambda m: m.timestamp, reverse=True)
        return messages

    def _to_message(self, match: Dict[str, Any]) -> Message:
        channel = match.get("channel", {})
        ts = float(match.get("ts", "0"))
        return Message(
            platform=self.platform,
            channel_or_thread=channel.get("name") or channel.get("id", "unknown"),
            author=match.get("username") or match.get("user"),
            timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
            text=match.get("text", ""),
            permalink=match.get("permalink"),
        )

    def _search_matching_channels(
        self, keywords: List[str], lookback_days: int, seen_keys: set
    ) -> List[Message]:
        tokens = [_tokenize(kw) for kw in keywords if _tokenize(kw)]
        if not tokens:
            return []

        matched_channel_ids = []
        cursor = None
        while True:
            params = {"types": "public_channel,private_channel", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload = self._call("conversations.list", params)
            for channel in payload.get("channels", []):
                name = _tokenize(channel.get("name", ""))
                if any(tok and tok in name for tok in tokens):
                    matched_channel_ids.append(channel["id"])
            cursor = payload.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        oldest = time.time() - lookback_days * 86400
        results: List[Message] = []
        for channel_id in matched_channel_ids:
            payload = self._call(
                "conversations.history", {"channel": channel_id, "oldest": oldest, "limit": 50}
            )
            for msg in payload.get("messages", []):
                key = (channel_id, msg.get("ts"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append(
                    Message(
                        platform=self.platform,
                        channel_or_thread=channel_id,
                        author=msg.get("user"),
                        timestamp=datetime.fromtimestamp(float(msg.get("ts", "0")), tz=timezone.utc),
                        text=msg.get("text", ""),
                        permalink=None,
                    )
                )
        return results
