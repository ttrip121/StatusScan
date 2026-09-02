"""Shared Microsoft Graph app-only auth helper used by TeamsSource and OutlookSource.

Uses the OAuth2 client-credentials flow (app-only permissions) via MSAL, so both Teams and
Outlook access run under a single Azure AD app registration. See README.md for the exact
Graph API permissions each adapter needs and the admin-consent steps.

Config shape shared by teams/outlook (see config/config.example.yaml):

    microsoft_graph:
      tenant_id: ${MS_TENANT_ID}
      client_id: ${MS_CLIENT_ID}
      client_secret: ${MS_CLIENT_SECRET}
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import msal
import requests

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

_lock = threading.Lock()
_app_cache: Dict[str, msal.ConfidentialClientApplication] = {}


def _get_app(tenant_id: str, client_id: str, client_secret: str) -> msal.ConfidentialClientApplication:
    key = f"{tenant_id}:{client_id}"
    with _lock:
        if key not in _app_cache:
            _app_cache[key] = msal.ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
            )
        return _app_cache[key]


def get_access_token(graph_config: Dict[str, Any]) -> str:
    app = _get_app(
        graph_config["tenant_id"], graph_config["client_id"], graph_config["client_secret"]
    )
    result = app.acquire_token_silent(GRAPH_SCOPE, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Failed to acquire Microsoft Graph token: "
            f"{result.get('error')}: {result.get('error_description')}"
        )
    return result["access_token"]


class GraphClient:
    """Thin authenticated wrapper around requests for Graph API calls."""

    def __init__(self, graph_config: Dict[str, Any]):
        self.graph_config = graph_config
        self._session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        token = get_access_token(self.graph_config)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self._session.get(f"{GRAPH_API_BASE}{path}", headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._session.post(f"{GRAPH_API_BASE}{path}", headers=self._headers(), json=json_body, timeout=30)
        resp.raise_for_status()
        return resp.json()
