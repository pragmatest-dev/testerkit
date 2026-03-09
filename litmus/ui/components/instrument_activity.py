"""Live instrument activity table component.

Displays a scrollable, auto-updating table of instrument read/set/configure
events from the shared EventStore. Shows cross-process activity.

Usage::

    with ui.card().classes("w-full"):
        ui.label("My Activity Log").classes("text-lg")
        create_instrument_activity(store)
"""

from __future__ import annotations

from datetime import UTC, datetime

from nicegui import ui

from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    STICKY_TABLE_CSS,
    litmus_table,
    table_cell_slot,
    table_col,
)
from litmus.ui.shared.event_binding import ui_subscribe
from litmus.utils import local_time

_INSTRUMENT_TYPES = {
    "instrument.read", "instrument.set", "instrument.configure",
}
_MAX_DETAIL_LEN = 40


def _truncate(val: object) -> str:
    s = str(val)
    if len(s) > _MAX_DETAIL_LEN:
        return s[: _MAX_DETAIL_LEN - 3] + "..."
    return s



def create_instrument_activity(
    store: EventStore,
    *,
    height: str = "250px",
) -> ui.table:
    """Create a live instrument activity table with sticky header.

    Place inside any container (card, column, etc). Returns
    the ``ui.table`` so callers can further style it.

    Args:
        height: CSS height for the scrollable area (e.g. "250px").
    """
    ui.add_css(STICKY_TABLE_CSS)
    label_cache: dict[str, str] = {}

    def _session_label(sid: str) -> str:
        if sid not in label_cache:
            short = sid[:4]
            for s in store.sessions():
                if str(s.get("session_id", "")) == sid:
                    client = (
                        s.get("client")
                        or s.get("session_type")
                        or "session"
                    )
                    label_cache[sid] = f"{client} #{short}"
                    break
            else:
                label_cache[sid] = f"#{short}"
        return label_cache[sid]

    def _format_row(evt: dict) -> dict | None:
        et = evt.get("event_type", "")
        if et not in _INSTRUMENT_TYPES:
            return None
        ts = evt.get("received_at") or evt.get("occurred_at") or ""
        sid = str(evt.get("session_id", ""))
        if et == "instrument.read":
            detail = (
                f"{evt.get('channel_id', '')} = "
                f"{_truncate(evt.get('value', ''))}"
            )
        elif et == "instrument.set":
            detail = (
                f"{evt.get('channel_id', '')}.{evt.get('attribute', '')} = "
                f"{_truncate(evt.get('value', ''))}"
            )
        elif et == "instrument.configure":
            role = evt.get("instrument_role", "")
            detail = f"{role}.{evt.get('method', '')}()"
        else:
            detail = ""
        return {
            "time": local_time(str(ts)) if ts else "",
            "event": et,
            "source": _session_label(sid),
            "detail": detail,
        }

    table = litmus_table([
        table_col("time", "Time", width="80px"),
        table_col("event", "Event", width="100px"),
        table_col("source", "Source", width="180px"),
        table_col("detail", "Detail"),
    ], per_page=0)
    table.classes("litmus-sticky-table")
    table.style(f"height: {height}")
    table_cell_slot(table, "time", "cell-muted")
    table.add_slot("body-cell-event", """
        <q-td :props="props">
            <span :class="'event-badge ' + props.value.split('.')[1]">
                {{ props.value.split('.')[1] }}
            </span>
        </q-td>
    """)
    table_cell_slot(table, "source", "cell-dim")

    max_rows = 200
    all_rows: list[dict] = []
    row_idx = [0]
    dirty = [False]

    def _on_activity(evt: dict) -> None:
        row = _format_row(evt)
        if row is None:
            return
        row["idx"] = row_idx[0]
        row_idx[0] += 1
        all_rows.insert(0, row)
        del all_rows[max_rows:]
        dirty[0] = True

    def _flush_table() -> None:
        if dirty[0]:
            dirty[0] = False
            table.update_rows(all_rows)

    ui.timer(0.5, _flush_table)
    ui_subscribe(store, _on_activity, since=datetime.now(UTC))

    return table
