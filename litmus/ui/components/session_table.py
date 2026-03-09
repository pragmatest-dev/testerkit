"""Live session table component.

Displays a scrollable, auto-updating table of station sessions
from the shared EventStore. Shows cross-process sessions.

Usage::

    with ui.card().classes("w-full"):
        ui.label("Sessions").classes("text-lg")
        create_session_table(store)
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


def create_session_table(
    store: EventStore,
    *,
    max_rows: int = 10,
    height: str = "200px",
) -> ui.table:
    """Create a live session table with sticky header.

    Place inside any container (card, column, etc). Returns
    the ``ui.table`` so callers can further style it.

    Args:
        height: CSS height for the scrollable area (e.g. "130px").
    """
    ui.add_css(STICKY_TABLE_CSS)
    ended_ids: set[str] = set()

    # Seed from existing events
    for e in store.events(event_type="session.ended"):
        ended_ids.add(str(e.get("session_id", "")))

    def _rows() -> list[dict]:
        rows = []
        for sess in store.sessions()[-max_rows:]:
            sid = str(sess.get("session_id", ""))
            if not sid:
                continue
            ts = sess.get("occurred_at") or sess.get("received_at") or ""
            rows.append({
                "started": local_time(str(ts)) if ts else "",
                "status": "ended" if sid in ended_ids else "active",
                "client": str(
                    sess.get("client")
                    or sess.get("session_type")
                    or "",
                ),
                "pid": str(sess.get("pid") or ""),
                "session": sid[:4],
            })
        rows.reverse()
        return rows

    table = litmus_table(
        [
            table_col("started", "Started", width="80px"),
            table_col("status", "", width="24px", align="center"),
            table_col("client", "Client", width="140px"),
            table_col("session", "ID", width="60px"),
            table_col("pid", "PID", width="60px"),
        ],
        rows=_rows(),
        row_key="session",
        per_page=0,
    )
    table.classes("litmus-sticky-table")
    table.style(f"height: {height}")
    table_cell_slot(table, "started", "cell-muted")
    table.add_slot("body-cell-status", """
        <q-td :props="props">
            <span :class="'status-dot ' + props.value"></span>
        </q-td>
    """)
    table.add_slot("body-cell-client", """
        <q-td :props="props">
            <span :class="'session-badge ' + props.value">
                {{ props.value }}
            </span>
        </q-td>
    """)
    table_cell_slot(table, "session", "cell-dim font-mono")
    table_cell_slot(table, "pid", "cell-muted")

    def _on_session(evt: dict) -> None:
        et = evt.get("event_type", "")
        if et == "session.ended":
            ended_ids.add(str(evt.get("session_id", "")))
            table.update_rows(_rows())
        elif et == "session.started":
            table.update_rows(_rows())

    ui_subscribe(store, _on_session, since=datetime.now(UTC))

    return table
