"""Events browser — filter the event log across sessions and time."""

from __future__ import annotations

import html
import json
from typing import Any

from nicegui import ui

# Local ``json`` is used by the per-event detail dialog; ``push_url_state``
# imports its own json on demand.
from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_header,
    page_layout,
    push_url_state,
    render_no_data_card,
    session_filter_banner,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import query_events

# Curated event-type list. The actual event store can hold any value
# (see ``src/litmus/data/events.py`` for the full ~25-class set); the
# entries below are the categories worth a one-click filter. Every
# value here must match the literal ``event_type`` of an actual event
# class in events.py — drift quietly breaks the filter (no rows
# returned).
_EVENT_TYPE_OPTIONS: list[str] = [
    "(any)",
    "session.started",
    "session.ended",
    "run.started",
    "run.ended",
    "test.step_started",
    "test.step_ended",
    "test.measurement",
    "channel.started",
    "instrument.set",
    "fixture.instrument_connected",
    "fixture.instrument_disconnected",
    "dialog.opened",
    "dialog.responded",
    "diagnostic.warning",
    "diagnostic.error",
]


@ui.page("/events")
def events_page(
    session_id: str = "",
    event_type: str = "",
    role: str = "",
    since: str = "",
    limit: int = 100,
) -> None:
    """Browse the event log with filter widgets + refresh.

    Filter state is mirrored into the URL via ``history.replaceState``
    so views are bookmarkable and shareable. Page-load reads URL
    params back into the widgets so a deep link opens at the same
    filtered state. Same pattern as ``/metrics`` and ``/explore``.
    """
    create_layout("Events")

    initial_event_type = event_type if event_type in _EVENT_TYPE_OPTIONS else "(any)"

    with page_layout():
        page_header("Event Log", icon="event_note")
        ui.label(
            "Browse every event the platform recorded — session lifecycle, "
            "instrument reads, test measurements, dialogs."
        ).classes("text-sm text-slate-500")

        # Session scoping is URL-only — no widget. The param is set
        # by deep-links from pages that already know the session
        # (e.g. /results/{run_id} → /events?session_id=...). The
        # banner is the only affordance to clear the scoping; there
        # is no add/change picker. UUIDs never appear in the UI.
        session_filter_banner(session_id, clear_path="/events")

        filters = _Filters()

        def refresh() -> None:
            current_limit = filters.limit()
            push_url_state(
                "/events",
                {
                    # session_id is URL-only — preserved across refresh
                    # via the page-level param, not the filter widgets.
                    "session_id": session_id,
                    "event_type": filters.event_type(),
                    "role": filters.role(),
                    "since": filters.since(),
                    "limit": current_limit if current_limit != 100 else "",
                },
            )
            payload = query_events(
                session_id=session_id or None,
                event_type=filters.event_type() or None,
                role=filters.role() or None,
                since=filters.since() or None,
                limit=current_limit,
            )
            _render_table(table_slot, payload)

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        # Filters render FIRST (above the table) so they read in
        # natural top-down order. The table slot is reserved second.
        with ui.card().classes("w-full").props('data-testid="events-filters"'):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.event_type_select = ui.select(
                    _EVENT_TYPE_OPTIONS,
                    value=initial_event_type,
                    label="Event type",
                    on_change=lambda _: refresh(),
                ).classes("w-56")
                filters.role_input = ui.input(
                    "Role", value=role, on_change=lambda _: refresh()
                ).classes("w-40")
                filters.since_input = ui.input(
                    "Since (ISO)", value=since, on_change=lambda _: refresh()
                ).classes("w-56")
                filters.limit_input = ui.number(
                    "Limit",
                    value=limit if limit and limit > 0 else 100,
                    min=1,
                    max=10_000,
                    step=50,
                    on_change=lambda _: refresh(),
                ).classes("w-28")
                ui.button("Refresh", icon="refresh", on_click=lambda: refresh()).props(
                    "color=primary"
                )

        table_slot = (
            ui.column().classes("w-full flex-1 min-h-0 gap-0").props('data-testid="events-table"')
        )

        refresh()


class _Filters:
    """Tiny container so callbacks read filter values lazily.

    ``session_id`` is intentionally NOT here — it's URL-only and
    flows through the page-level parameter, never via a filter
    widget (see :func:`session_filter_banner`).
    """

    event_type_select: ui.select
    role_input: ui.input
    since_input: ui.input
    limit_input: ui.number

    def event_type(self) -> str:
        v = (self.event_type_select.value or "").strip()
        return "" if v == "(any)" else v

    def role(self) -> str:
        return (self.role_input.value or "").strip()

    def since(self) -> str:
        return (self.since_input.value or "").strip()

    def limit(self) -> int:
        try:
            return max(1, min(10_000, int(self.limit_input.value or 100)))
        except (TypeError, ValueError):
            return 100


def _render_table(slot: ui.column, payload: dict[str, Any]) -> None:
    """Replace ``slot`` content with a viewport-bound event table."""
    slot.clear()
    events = payload.get("events") or []
    count = payload.get("count", len(events))

    with slot:
        ui.label(f"{count} event(s)").classes("text-sm text-slate-600 px-2 py-1")

        if not events:
            render_no_data_card(
                ui.column().classes("w-full"),
                title="No events match the current filters.",
                reason="Clear the filters above (or widen the time window) to see more events.",
                icon="notifications_off",
            )
            return

        columns = [
            {"name": "occurred_at", "label": "Timestamp", "field": "occurred_at", "align": "left"},
            {"name": "event_type", "label": "Type", "field": "event_type", "align": "left"},
            {"name": "role", "label": "Role", "field": "role", "align": "left"},
            {"name": "summary", "label": "Summary", "field": "summary", "align": "left"},
        ]
        rows = [
            {
                "id": str(idx),
                "occurred_at": format_datetime(evt.get("occurred_at")),
                "event_type": evt.get("event_type") or "",
                "role": evt.get("instrument_role") or evt.get("role") or "",
                "summary": _summarize(evt),
                "_raw": evt,
            }
            for idx, evt in enumerate(events)
        ]
        data_table(
            columns=columns,
            rows=rows,
            row_key="id",
            on_row_click=lambda r: _show_detail_dialog(r["_raw"]),
            time_columns=["occurred_at"],
        )


def _show_detail_dialog(event: dict[str, Any]) -> None:
    """Pop a dialog with the full event JSON."""
    with ui.dialog() as dialog, ui.card().classes("p-4 w-[min(900px,90vw)]"):
        ui.label(event.get("event_type") or "Event").classes("text-lg font-semibold mb-2")
        try:
            content = json.dumps(event, indent=2, default=str)
        except (TypeError, ValueError):
            content = repr(event)
        ui.html(
            f'<pre class="text-xs whitespace-pre-wrap break-all" '
            f'style="max-height:60vh;overflow:auto">{html.escape(content)}</pre>',
            sanitize=False,
        )
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _summarize(event: dict[str, Any]) -> str:
    """One-line summary for the table — picks salient fields per event type."""
    et = event.get("event_type") or ""
    if et == "channel.started":
        ch = event.get("channel_id") or ""
        unit = event.get("unit") or ""
        return f"{ch} ({unit})" if unit else ch
    if et == "instrument.set":
        ch = event.get("channel_id") or ""
        val = event.get("value")
        unit = event.get("unit") or ""
        return f"{ch} ← {val} {unit}".strip()
    if et == "test.measurement":
        name = event.get("measurement_name") or ""
        val = event.get("value")
        outcome = event.get("outcome") or ""
        return f"{name} = {val} ({outcome})".strip()
    if et in ("session.started", "session.ended"):
        return event.get("station_id") or ""
    if et in ("run.started", "run.ended"):
        return event.get("uut_serial") or event.get("station_id") or ""
    if et.startswith("test.step"):
        return event.get("step_name") or ""
    return ""
