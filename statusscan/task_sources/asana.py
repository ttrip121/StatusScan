"""Asana TaskSource adapter.

Required scope: a Personal Access Token (Asana Settings -> Apps -> Manage Developer Apps ->
Personal access tokens), or an OAuth token with the `default` scope. The token's owner must
have read access to every project listed in config under task_sources.asana.project_gids.

Config shape (see config/config.example.yaml):

    task_sources:
      asana:
        active: true
        access_token: ${ASANA_ACCESS_TOKEN}
        project_gids:
          - "1201234567890123"
          - "1201234567890456"
        client_facing_tag: "client-facing"   # optional, defaults to "client-facing"
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

from statusscan.task_sources.base import TaskSource

ASANA_API_BASE = "https://app.asana.com/api/1.0"

# Fields requested per task via opt_fields, kept minimal but complete enough for normalization.
TASK_FIELDS = ",".join(
    [
        "name",
        "due_on",
        "assignee.name",
        "assignee.email",
        "tags.name",
        "permalink_url",
        "completed",
        "memberships.project.name",
    ]
)


class AsanaSource(TaskSource):
    source_platform = "asana"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.access_token = config["access_token"]
        self.project_gids: List[str] = config["project_gids"]
        self.client_facing_tag = config.get("client_facing_tag", "client-facing").lower()
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self.access_token}"})

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self._session.get(f"{ASANA_API_BASE}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_open_tasks(self) -> List[Any]:
        tasks: List[Any] = []
        for project_gid in self.project_gids:
            params = {
                "completed_since": "now",  # Asana idiom for "only incomplete tasks"
                "opt_fields": TASK_FIELDS,
                "limit": 100,
            }
            path = f"/projects/{project_gid}/tasks"
            while True:
                payload = self._get(path, params)
                tasks.extend(payload.get("data", []))
                next_page = (payload.get("next_page") or {}).get("offset")
                if not next_page:
                    break
                params = dict(params)
                params["offset"] = next_page
        return tasks

    def get_id(self, raw_task: Any) -> str:
        return raw_task["gid"]

    def get_name(self, raw_task: Any) -> str:
        return raw_task.get("name", "(untitled task)")

    def get_due_date(self, raw_task: Any) -> Optional[date]:
        due_on = raw_task.get("due_on")
        if not due_on:
            return None
        return datetime.strptime(due_on, "%Y-%m-%d").date()

    def get_assignee(self, raw_task: Any) -> Optional[str]:
        assignee = raw_task.get("assignee")
        if not assignee:
            return None
        return assignee.get("name") or assignee.get("email")

    def get_tags(self, raw_task: Any) -> List[str]:
        return [tag["name"] for tag in raw_task.get("tags", []) if tag.get("name")]

    def get_project(self, raw_task: Any) -> Optional[str]:
        memberships = raw_task.get("memberships") or []
        for membership in memberships:
            project = membership.get("project")
            if project and project.get("name"):
                return project["name"]
        return None

    def get_url(self, raw_task: Any) -> Optional[str]:
        return raw_task.get("permalink_url")

    def get_client_facing(self, raw_task: Any) -> bool:
        tags = [t.lower() for t in self.get_tags(raw_task)]
        return self.client_facing_tag in tags
