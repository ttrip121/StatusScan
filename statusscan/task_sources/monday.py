"""Monday.com TaskSource adapter.

Required scope: a personal API token (Monday avatar -> Administration -> API, or per-user
under Profile -> Developers) with `boards:read` permission on every board listed in
task_sources.monday.board_ids.

Monday.com has no fixed schema for "due date" or "tags" - those are custom columns whose IDs
vary per board. Configure the column IDs to read from (find them via the board's "..." menu ->
"Manage columns", or by querying the API's `columns { id title type }` field for the board).

Config shape (see config/config.example.yaml):

    task_sources:
      monday:
        active: true
        api_token: ${MONDAY_API_TOKEN}
        board_ids:
          - 1234567890
        due_date_column_id: "date4"
        status_column_id: "status"
        done_status_labels: ["Done", "Complete"]   # items in these statuses are excluded
        tags_column_id: "tags"
        people_column_id: "people"
        client_facing_tag: "client-facing"
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

from statusscan.task_sources.base import TaskSource

MONDAY_API_URL = "https://api.monday.com/v2"

ITEMS_QUERY = """
query ($boardId: ID!, $cursor: String) {
  boards(ids: [$boardId]) {
    name
    items_page(limit: 100, cursor: $cursor) {
      cursor
      items {
        id
        name
        url
        column_values {
          id
          type
          text
          value
        }
      }
    }
  }
}
"""


class MondaySource(TaskSource):
    source_platform = "monday"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_token = config["api_token"]
        self.board_ids: List[int] = config["board_ids"]
        self.due_date_column_id = config["due_date_column_id"]
        self.status_column_id = config.get("status_column_id")
        self.done_status_labels = {s.lower() for s in config.get("done_status_labels", ["Done"])}
        self.tags_column_id = config.get("tags_column_id")
        self.people_column_id = config.get("people_column_id")
        self.client_facing_tag = config.get("client_facing_tag", "client-facing").lower()
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": self.api_token, "Content-Type": "application/json"}
        )

    def _column(self, raw_task: Dict[str, Any], column_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not column_id:
            return None
        for col in raw_task.get("column_values", []):
            if col["id"] == column_id:
                return col
        return None

    def get_open_tasks(self) -> List[Any]:
        tasks: List[Any] = []
        for board_id in self.board_ids:
            cursor = None
            board_name: Optional[str] = None
            while True:
                resp = self._session.post(
                    MONDAY_API_URL,
                    json={"query": ITEMS_QUERY, "variables": {"boardId": board_id, "cursor": cursor}},
                    timeout=30,
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("errors"):
                    raise RuntimeError(f"Monday API error: {payload['errors']}")
                boards = payload["data"]["boards"]
                if not boards:
                    break
                board = boards[0]
                board_name = board["name"]
                items_page = board["items_page"]
                for item in items_page["items"]:
                    item["_board_name"] = board_name
                    if not self._is_done(item):
                        tasks.append(item)
                cursor = items_page.get("cursor")
                if not cursor:
                    break
        return tasks

    def _is_done(self, raw_task: Dict[str, Any]) -> bool:
        status_col = self._column(raw_task, self.status_column_id)
        if not status_col or not status_col.get("text"):
            return False
        return status_col["text"].strip().lower() in self.done_status_labels

    def get_id(self, raw_task: Any) -> str:
        return raw_task["id"]

    def get_name(self, raw_task: Any) -> str:
        return raw_task.get("name", "(untitled item)")

    def get_due_date(self, raw_task: Any) -> Optional[date]:
        col = self._column(raw_task, self.due_date_column_id)
        if not col or not col.get("text"):
            return None
        try:
            return datetime.strptime(col["text"], "%Y-%m-%d").date()
        except ValueError:
            return None

    def get_assignee(self, raw_task: Any) -> Optional[str]:
        col = self._column(raw_task, self.people_column_id)
        if not col or not col.get("text"):
            return None
        # Monday's "people" column text is a comma-separated list of names; take the first.
        return col["text"].split(",")[0].strip() or None

    def get_tags(self, raw_task: Any) -> List[str]:
        col = self._column(raw_task, self.tags_column_id)
        if not col or not col.get("text"):
            return []
        return [t.strip() for t in col["text"].split(",") if t.strip()]

    def get_project(self, raw_task: Any) -> Optional[str]:
        return raw_task.get("_board_name")

    def get_url(self, raw_task: Any) -> Optional[str]:
        return raw_task.get("url")

    def get_client_facing(self, raw_task: Any) -> bool:
        tags = [t.lower() for t in self.get_tags(raw_task)]
        return self.client_facing_tag in tags
