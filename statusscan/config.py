"""Loads config.yaml, interpolates ${ENV_VAR} references (optionally from a .env file via
python-dotenv), and exposes typed accessors used across the pipeline.

Secrets (API tokens, SMTP passwords, etc.) should live in environment variables or a .env
file, never directly in config.yaml. Reference them from the YAML with ${VAR_NAME} syntax,
e.g.:

    task_sources:
      asana:
        access_token: ${ASANA_ACCESS_TOKEN}
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import yaml

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: "re.Match[str]") -> str:
            var_name = match.group(1)
            resolved = os.environ.get(var_name)
            if resolved is None:
                raise ValueError(
                    f"Config references ${{{var_name}}} but that environment variable is not set. "
                    f"Set it in your shell or .env file."
                )
            return resolved

        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


class Config:
    def __init__(self, raw: Dict[str, Any]):
        self._raw = raw

    @classmethod
    def load(cls, path: str = "config/config.yaml", dotenv_path: Optional[str] = ".env") -> "Config":
        if dotenv_path and os.path.exists(dotenv_path):
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path)
            except ImportError:
                pass  # python-dotenv is optional; env vars can also be set directly

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw = _interpolate_env(raw)
        return cls(raw)

    # -- generic access -----------------------------------------------------

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- typed sections used by main.py --------------------------------------

    @property
    def anthropic_api_key(self) -> str:
        return self.get("anthropic", "api_key")

    @property
    def anthropic_model(self) -> str:
        return self.get("anthropic", "model", default="claude-sonnet-5")

    @property
    def lookback_days(self) -> int:
        return int(self.get("context_search", "lookback_days", default=14))

    @property
    def email_recipients(self) -> List[str]:
        return self.get("email", "recipients", default=[])

    @property
    def smtp(self) -> Dict[str, Any]:
        return self.get("email", "smtp", default={})

    def active_task_sources(self) -> Dict[str, Any]:
        sources = self.get("task_sources", default={}) or {}
        return {name: cfg for name, cfg in sources.items() if cfg.get("active")}

    def active_context_sources(self) -> Dict[str, Any]:
        sources = self.get("context_sources", default={}) or {}
        return {name: cfg for name, cfg in sources.items() if cfg.get("active")}

    # -- settings.json overlay -----------------------------------------------
    # Sweep frequency, lookback window, recipients, and active project/board scope are owned
    # by settings.json once it exists (see settings.py) so the settings UI can change them
    # without touching config.yaml. This mutates the loaded config in place so every property
    # above stays the single source of truth for the rest of the pipeline.

    def apply_settings_overrides(self, settings: Dict[str, Any]) -> None:
        self._raw.setdefault("context_search", {})["lookback_days"] = settings["lookback_days"]
        self._raw.setdefault("email", {})["recipients"] = list(settings["recipients"])

        task_sources = self._raw.setdefault("task_sources", {})
        active_scope = settings.get("active_scope", {}) or {}

        asana_scope = active_scope.get("asana")
        if asana_scope is not None and "asana" in task_sources:
            task_sources["asana"]["project_gids"] = list(asana_scope.get("project_gids", []))

        monday_scope = active_scope.get("monday")
        if monday_scope is not None and "monday" in task_sources:
            task_sources["monday"]["board_ids"] = list(monday_scope.get("board_ids", []))
