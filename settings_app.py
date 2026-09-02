"""StatusScan settings UI - lets the PM adjust sweep frequency and digest detail level, plus
convenience fields (lookback window, recipients, active project/board scope), without editing
config.yaml directly. Settings are persisted to settings.json, which both this app and the
pipeline (main.py / scheduler.py) read - changes here take effect on the poller's next
15-minute check, no restart needed.

Run with:

    streamlit run settings_app.py
"""

from datetime import time

import streamlit as st

from statusscan.config import Config
from statusscan.run_history import load_run_history
from statusscan.settings import (
    DETAIL_LEVEL_DESCRIPTIONS,
    DETAIL_LEVEL_LABELS,
    VALID_DETAIL_LEVELS,
    load_or_bootstrap_settings,
    save_settings,
)

CONFIG_PATH = "config/config.yaml"

st.set_page_config(page_title="StatusScan Settings", page_icon="📋")
st.title("StatusScan Settings")

try:
    config = Config.load(CONFIG_PATH)
except FileNotFoundError:
    st.error(
        f"Couldn't find {CONFIG_PATH}. Copy config/config.example.yaml to config/config.yaml "
        "and fill it in first - this UI only edits the operational settings layered on top "
        "of it (sweep frequency, detail level, lookback window, recipients, active scope)."
    )
    st.stop()

settings = load_or_bootstrap_settings(config)

# -- Sweep frequency ----------------------------------------------------------------------

st.header("Sweep frequency")
st.caption(
    "Times of day StatusScan checks tasks and sends a digest. The poller checks settings.json "
    "every 15 minutes, so a slot fires on the next check at or after its time."
)

if "sweep_slots" not in st.session_state:
    st.session_state.sweep_slots = [
        {"id": i, "time": t} for i, t in enumerate(settings["sweep_times"])
    ]
    st.session_state.next_slot_id = len(st.session_state.sweep_slots)

remove_id = None
for slot in st.session_state.sweep_slots:
    col_time, col_remove = st.columns([4, 1])
    hour, minute = (int(p) for p in slot["time"].split(":"))
    new_value = col_time.time_input(
        f"Slot {slot['id']}",
        value=time(hour, minute),
        key=f"slot_time_{slot['id']}",
        label_visibility="collapsed",
    )
    slot["time"] = new_value.strftime("%H:%M")
    if col_remove.button("Remove", key=f"remove_{slot['id']}"):
        remove_id = slot["id"]

if remove_id is not None:
    st.session_state.sweep_slots = [
        s for s in st.session_state.sweep_slots if s["id"] != remove_id
    ]
    st.rerun()

if st.button("+ Add time slot"):
    st.session_state.sweep_slots.append({"id": st.session_state.next_slot_id, "time": "09:00"})
    st.session_state.next_slot_id += 1
    st.rerun()

# -- Detail level ---------------------------------------------------------------------------

st.header("Digest detail level")
detail_level = st.radio(
    "How much detail should each digest include?",
    options=VALID_DETAIL_LEVELS,
    format_func=lambda v: DETAIL_LEVEL_LABELS[v],
    index=VALID_DETAIL_LEVELS.index(settings.get("detail_level", "more")),
)
st.caption(DETAIL_LEVEL_DESCRIPTIONS[detail_level])

# -- Convenience fields -----------------------------------------------------------------

st.header("Other settings")

lookback_days = st.number_input(
    "Context lookback window (days)",
    min_value=1,
    max_value=90,
    value=int(settings["lookback_days"]),
    help="How far back to search Slack/Teams/Outlook for messages mentioning a flagged task.",
)

recipients_text = st.text_area(
    "Email recipients (one per line)",
    value="\n".join(settings["recipients"]),
    height=100,
)

st.subheader("Active projects / boards")
current_scope = settings.get("active_scope", {}) or {}
new_active_scope = {}

asana_cfg = config.get("task_sources", "asana", default={}) or {}
if asana_cfg.get("active"):
    current_gids = current_scope.get("asana", {}).get("project_gids", [])
    text = st.text_area("Asana project GIDs (one per line)", value="\n".join(current_gids))
    new_active_scope["asana"] = {
        "project_gids": [line.strip() for line in text.splitlines() if line.strip()]
    }
else:
    st.caption("Asana is not active in config.yaml - nothing to scope here.")

monday_cfg = config.get("task_sources", "monday", default={}) or {}
if monday_cfg.get("active"):
    current_ids = current_scope.get("monday", {}).get("board_ids", [])
    text = st.text_area(
        "Monday.com board IDs (one per line)", value="\n".join(str(b) for b in current_ids)
    )
    board_ids = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        board_ids.append(int(line) if line.isdigit() else line)
    new_active_scope["monday"] = {"board_ids": board_ids}
else:
    st.caption("Monday.com is not active in config.yaml - nothing to scope here.")

# -- Save -----------------------------------------------------------------------------------

if st.button("Save settings", type="primary"):
    sweep_times = sorted({s["time"] for s in st.session_state.sweep_slots})
    new_settings = {
        "sweep_times": sweep_times,
        "detail_level": detail_level,
        "lookback_days": int(lookback_days),
        "recipients": [line.strip() for line in recipients_text.splitlines() if line.strip()],
        "active_scope": new_active_scope,
    }
    save_settings(new_settings)
    st.success(
        "Settings saved. The poller picks up changes on its next 15-minute check - no "
        "restart needed."
    )

# -- Recent runs ------------------------------------------------------------------------

st.header("Recent runs")
history = load_run_history()
if not history:
    st.caption("No run history yet - it will appear here after StatusScan's first run.")
else:
    recent = list(reversed(history[-5:]))
    st.table(
        [
            {"Time": h["timestamp"], "Flagged": h["flagged"], "Sent": h["sent"]}
            for h in recent
        ]
    )
