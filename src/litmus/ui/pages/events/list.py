"""Events browser — filter the event log across sessions and time."""

from __future__ import annotations

import json
from typing import Any

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import query_events

# Curated event-type list. The actual event store can hold any value, but
# these are the categories worth a one-click filter.
_EVENT_TYPE_OPTIONS: list[str] = [
    "(any)",
    "session.started",
    "session.ended",
    "run.started",
    "run.ended",
    "test.step_started",
    "test.step_ended",
    "test.measurement",
    "instrument.read",
    "instrument.set",
    "instrument.connected",
    "instrument.disconnected",
    "dialog.requested",
    "dialog.responded",
    "diagnostic.warning",
    "diagnostic.error",
]


@ui.page("/events")
def events_page() -> None:
    """Browse the event log with filter widgets + refresh."""
    create_layout("Events")

    with ui.column().classes("w-full p-6 gap-4"):
        ui.label("Event Log").classes("text-2xl font-semibold")
        ui.label(
            "Browse every event the platform recorded — session lifecycle, "
            "instrument reads, test measurements, dialogs."
        ).classes("text-sm text-slate-500")

        filters = _Filters()
        table_card = ui.card().classes("w-full")

        def refresh() -> None:
            payload = query_events(
                session_id=filters.session_id() or None,
                event_type=filters.event_type() or None,
                role=filters.role() or None,
                since=filters.since() or None,
                limit=filters.limit(),
            )
            _render_table(table_card, payload)

        with ui.card().classes("w-full"):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.session_input = ui.input("Session ID").classes("w-64")
                filters.event_type_select = ui.select(
                    _EVENT_TYPE_OPTIONS, value="(any)", label="Event type"
                ).classes("w-56")
                filters.role_input = ui.input("Role").classes("w-40")
                filters.since_input = ui.input("Since (ISO)").classes("w-56")
                filters.limit_input = ui.number(
                    "Limit", value=100, min=1, max=10_000, step=50
                ).classes("w-28")
                ui.button("Refresh", icon="refresh", on_click=refresh).props("color=primary")

        # Initial load
        refresh()


class _Filters:
    """Tiny container so callbacks read filter values lazily."""

    session_input: ui.input
    event_type_select: ui.select
    role_input: ui.input
    since_input: ui.input
    limit_input: ui.number

    def session_id(self) -> str:
        return (self.session_input.value or "").strip()

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


def _render_table(card: ui.card, payload: dict[str, Any]) -> None:
    """Replace ``card`` content with a table of events."""
    card.clear()
    events = payload.get("events") or []
    count = payload.get("count", len(events))

    with card:
        with ui.card_section():
            ui.label(f"{count} event(s)").classes("text-sm text-slate-600")

        if not events:
            with ui.card_section():
                ui.label("No events match the current filters.").classes("text-slate-500 italic")
            return

        with ui.card_section().classes("p-0"):
            columns = [
                {
                    "name": "occurred_at",
                    "label": "Timestamp",
                    "field": "occurred_at",
                    "align": "left",
                },
                {
                    "name": "event_type",
                    "label": "Type",
                    "field": "event_type",
                    "align": "left",
                },
                {
                    "name": "session",
                    "label": "Session",
                    "field": "session",
                    "align": "left",
                },
                {"name": "run", "label": "Run", "field": "run", "align": "left"},
                {"name": "role", "label": "Role", "field": "role", "align": "left"},
                {
                    "name": "summary",
                    "label": "Summary",
                    "field": "summary",
                    "align": "left",
                },
            ]
            rows = [
                {
                    "id": str(idx),
                    "occurred_at": _format_timestamp(evt.get("occurred_at")),
                    "event_type": evt.get("event_type") or "",
                    "session": _short(evt.get("session_id")),
                    "run": _short(evt.get("run_id")),
                    "role": evt.get("instrument_role") or evt.get("role") or "",
                    "summary": _summarize(evt),
                    "_raw": evt,
                }
                for idx, evt in enumerate(events)
            ]
            table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
            table.on(
                "row-click",
                lambda e: _show_detail_dialog(e.args[1]["_raw"]),
            )


def _show_detail_dialog(event: dict[str, Any]) -> None:
    """Pop a dialog with the full event JSON."""
    with ui.dialog() as dialog, ui.card().classes("p-4 w-[min(900px,90vw)]"):
        ui.label(event.get("event_type") or "Event").classes("text-lg font-semibold mb-2")
        try:
            content = json.dumps(event, indent=2, default=str)
        except (TypeError, ValueError):
            content = repr(event)
        import html as _html

        ui.html(
            f'<pre class="text-xs whitespace-pre-wrap break-all" '
            f'style="max-height:60vh;overflow:auto">{_html.escape(content)}</pre>',
            sanitize=False,
        )
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _short(uuid_str: Any) -> str:
    if not uuid_str:
        return ""
    return str(uuid_str)[:8]


def _format_timestamp(ts: Any) -> str:
    if not ts:
        return ""
    s = str(ts)
    # Drop sub-second precision and timezone for compact display.
    if "T" in s:
        date, _, rest = s.partition("T")
        time = rest.split(".", 1)[0].split("+", 1)[0].split("-", 1)[0]
        return f"{date} {time}"
    return s


def _summarize(event: dict[str, Any]) -> str:
    """One-line summary for the table — picks salient fields per event type."""
    et = event.get("event_type") or ""
    if et == "instrument.read":
        ch = event.get("channel_id") or ""
        val = event.get("value")
        units = event.get("units") or ""
        return f"{ch} = {val} {units}".strip()
    if et == "instrument.set":
        ch = event.get("channel_id") or ""
        val = event.get("value")
        units = event.get("units") or ""
        return f"{ch} ← {val} {units}".strip()
    if et == "test.measurement":
        name = event.get("measurement_name") or ""
        val = event.get("value")
        outcome = event.get("outcome") or ""
        return f"{name} = {val} ({outcome})".strip()
    if et in ("session.started", "session.ended"):
        return event.get("station_id") or ""
    if et in ("run.started", "run.ended"):
        return event.get("dut_serial") or event.get("station_id") or ""
    if et.startswith("test.step"):
        return event.get("step_name") or ""
    return ""
