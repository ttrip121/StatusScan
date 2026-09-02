"""Lightweight run-history log so the settings UI can show recent digest runs. Not an audit
trail - just enough state for settings_app.py to render "last 5 runs"."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

RUN_HISTORY_PATH_DEFAULT = "run_history.json"

# Keep the file small - the UI only ever shows the last 5 entries.
MAX_ENTRIES = 200


def load_run_history(path: str = RUN_HISTORY_PATH_DEFAULT) -> List[Dict[str, Any]]:
    """Return logged runs oldest-first, or an empty list if none have been logged yet."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def record_run(flagged_count: int, sent_count: int, path: str = RUN_HISTORY_PATH_DEFAULT) -> None:
    """Append one entry after a digest run completes. Written atomically (write-then-rename)
    so a concurrent reader never sees a half-written file."""
    entries = load_run_history(path)
    entries.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "flagged": flagged_count,
            "sent": sent_count,
        }
    )
    entries = entries[-MAX_ENTRIES:]

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp_path, path)
