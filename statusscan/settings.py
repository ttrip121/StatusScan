"""settings.json: the small set of operational knobs a PM can change from settings_app.py
(the Streamlit settings UI) without touching config.yaml or restarting the poller. Both the
UI and the pipeline (main.py / scheduler.py) read this same file through the functions here.

The first time settings.json doesn't exist yet, it is bootstrapped from whatever is already
in config.yaml (sweep times, lookback window, recipients, and each active source's
project/board scope) via load_or_bootstrap_settings(), so switching to the settings UI never
silently resets anything. After that, settings.json is authoritative for these fields -
config.yaml is no longer consulted for them.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from statusscan.config import Config

SETTINGS_PATH_DEFAULT = "settings.json"

VALID_DETAIL_LEVELS = ("some", "more", "most")

DETAIL_LEVEL_LABELS = {
    "some": "Some",
    "more": "More",
    "most": "Most",
}

DETAIL_LEVEL_DESCRIPTIONS = {
    "some": "Structured fields only - each reason is one flat, factual sentence.",
    "more": "Same fields, with a short 2-3 sentence narrative reason.",
    "most": "Everything in More, plus an Insights & Suggested Next Steps section spotting patterns across tasks.",
}

DEFAULT_SWEEP_TIMES = ["08:00", "13:00", "18:00"]
DEFAULT_DETAIL_LEVEL = "more"
DEFAULT_LOOKBACK_DAYS = 14


def load_settings(path: str = SETTINGS_PATH_DEFAULT) -> Optional[Dict[str, Any]]:
    """Load settings.json as-is, or None if it hasn't been created yet. Prefer
    load_or_bootstrap_settings() in the pipeline/UI - use this directly only when you
    specifically need to distinguish "not created yet" from "created"."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings: Dict[str, Any], path: str = SETTINGS_PATH_DEFAULT) -> None:
    """Write settings.json atomically (write-then-rename) so a concurrent reader - the
    poller, checking every 15 minutes - never sees a half-written file."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _bootstrap_from_config(config: "Config") -> Dict[str, Any]:
    """Build an initial settings dict from an already-loaded Config, so the first save/read
    of settings.json reflects whatever was already configured in config.yaml."""
    active_scope: Dict[str, Any] = {}

    asana_cfg = config.get("task_sources", "asana", default={}) or {}
    if asana_cfg.get("active"):
        active_scope["asana"] = {"project_gids": list(asana_cfg.get("project_gids", []))}

    monday_cfg = config.get("task_sources", "monday", default={}) or {}
    if monday_cfg.get("active"):
        active_scope["monday"] = {"board_ids": list(monday_cfg.get("board_ids", []))}

    return {
        "sweep_times": list(config.get("schedule", "times", default=DEFAULT_SWEEP_TIMES)),
        "detail_level": DEFAULT_DETAIL_LEVEL,
        "lookback_days": config.lookback_days,
        "recipients": list(config.email_recipients),
        "active_scope": active_scope,
    }


def load_or_bootstrap_settings(config: "Config", path: str = SETTINGS_PATH_DEFAULT) -> Dict[str, Any]:
    """Load settings.json, creating it from config.yaml's current values on first use. Both
    settings_app.py and main.py call this so neither one can silently reset the other."""
    existing = load_settings(path)
    if existing is not None:
        return existing
    bootstrapped = _bootstrap_from_config(config)
    save_settings(bootstrapped, path)
    return bootstrapped
