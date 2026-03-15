"""Live event timeline component.

Displays a scrollable, filterable list of session events
with color-coded categories and auto-scroll.  Uses EventStore
subscriptions for push-based updates (no polling).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from nicegui import ui

from litmus.data.event_store import EventStore
from litmus.ui.shared.event_binding import ui_subscribe
from litmus.ui.shared.timestamps import parse_iso_timestamp

# Category → (color-dot class, badge bg, label)
_CATEGORIES: dict[str, tuple[str, str, str]] = {
    "session": ("bg-blue-500", "bg-blue-100 text-blue-800", "Session"),
    "run": ("bg-blue-400", "bg-blue-50 text-blue-700", "Run"),
    "fixture": ("bg-purple-500", "bg-purple-100 text-purple-800", "Fixture"),
    "test": ("bg-orange-500", "bg-orange-100 text-orange-800", "Test"),
    "slot": ("bg-amber-500", "bg-amber-100 text-amber-800", "Slot"),
    "sync": ("bg-amber-400", "bg-amber-50 text-amber-700", "Sync"),
    "route": ("bg-teal-500", "bg-teal-100 text-teal-800", "Route"),
    "instrument": ("bg-emerald-500", "bg-emerald-100 text-emerald-800", "Instrument"),
    "diagnostic": ("bg-slate-400", "bg-slate-100 text-slate-600", "Diagnostic"),
    "stream": ("bg-cyan-500", "bg-cyan-100 text-cyan-800", "Stream"),
    "dialog": ("bg-pink-500", "bg-pink-100 text-pink-800", "Dialog"),
}


def _category_for(event_type: str) -> str:
    """Extract category from dotted event_type (e.g. 'test.measurement' → 'test')."""
    return event_type.split(".")[0] if "." in event_type else event_type


def _detail_measurement(e: dict) -> str:
    val = e.get("value")
    val_str = f"{val:.4g}" if isinstance(val, (int, float)) else str(val)
    units = e.get("units") or ""
    return f"{e.get('measurement_name', '')} = {val_str} {units} [{e.get('outcome', '')}]"


def _detail_read(e: dict) -> str:
    return f"{e.get('channel_id', '')} → {e.get('value', '')} {e.get('units', '')}"


def _detail_set(e: dict) -> str:
    return f"{e.get('channel_id', '')} . {e.get('attribute', '')} = {e.get('value', '')}"


def _detail_session_started(e: dict) -> str:
    return f"station={e.get('station_id', '')}"


def _detail_run_started(e: dict) -> str:
    return f"station={e.get('station_id', '')} dut={e.get('dut_serial', '')}"


def _detail_step_ended(e: dict) -> str:
    return f"{e.get('step_name', '')} ({e.get('outcome', '')})"


_DETAIL_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "test.measurement": _detail_measurement,
    "instrument.read": _detail_read,
    "instrument.set": _detail_set,
    "session.started": _detail_session_started,
    "run.started": _detail_run_started,
    "test.step_started": lambda e: e.get("step_name", ""),
    "test.step_ended": _detail_step_ended,
    "run.ended": lambda e: e.get("outcome", ""),
    "session.ended": lambda e: e.get("outcome", ""),
}


def _event_detail(evt: dict) -> str:
    """Extract a concise detail string from an event."""
    formatter = _DETAIL_FORMATTERS.get(evt.get("event_type", ""))
    return formatter(evt) if formatter else ""


def _relative_time(evt: dict, t0: str | None) -> str:
    """Format T+Ns relative timestamp."""
    ts = evt.get("occurred_at") or evt.get("received_at") or ""
    if not ts or not t0:
        return ts[:19] if ts else ""
    try:
        delta = (parse_iso_timestamp(ts) - parse_iso_timestamp(t0)).total_seconds()
        return f"T+{delta:.1f}s"
    except (ValueError, TypeError):
        return ts[:19]


def create_event_timeline(
    store: EventStore,
) -> tuple[ui.column, Callable[[], None]]:
    """Create a live-updating event timeline via EventStore subscription.

    Returns (container, unsubscribe) so the caller can stop updates.
    """

    # State
    all_events: list[dict] = []
    active_filters: set[str] = set(_CATEGORIES.keys())
    t0: str | None = None

    container = ui.column().classes("w-full")

    with container:
        # Filter chips
        with ui.row().classes("flex-wrap gap-1 mb-2"):
            chip_elements: dict[str, ui.button] = {}
            for cat, (dot_cls, badge_cls, label) in _CATEGORIES.items():

                def make_toggle(c: str, b_cls: str):
                    def toggle():
                        if c in active_filters:
                            active_filters.discard(c)
                            chip_elements[c].classes(
                                remove=b_cls, add="bg-slate-200 text-slate-400"
                            )
                        else:
                            active_filters.add(c)
                            chip_elements[c].classes(
                                remove="bg-slate-200 text-slate-400", add=b_cls
                            )
                        _rebuild_rows()

                    return toggle

                btn = ui.button(
                    label, on_click=make_toggle(cat, badge_cls)
                ).classes(
                    f"text-xs px-2 py-0.5 rounded-full {badge_cls}"
                ).props("flat dense")
                chip_elements[cat] = btn

        # Scrollable event rows
        scroll_area = ui.scroll_area().classes("w-full max-h-96")
        rows_container = ui.column().classes("w-full gap-0")
        rows_container.move(scroll_area)

    def _rebuild_rows():
        """Re-render visible rows based on active filters."""
        rows_container.clear()
        for evt in all_events:
            cat = _category_for(evt.get("event_type", ""))
            if cat not in active_filters:
                continue
            _render_event_row(evt, cat)

    def _render_event_row(evt: dict, cat: str):
        """Render a single event row in the rows container."""
        dot_cls, badge_cls, _ = _CATEGORIES.get(cat, _CATEGORIES["diagnostic"])
        et = evt.get("event_type", "unknown")
        detail = _event_detail(evt)
        rel = _relative_time(evt, t0)

        with rows_container:
            with ui.expansion().classes(
                "w-full border-b border-slate-100"
            ).props("dense header-class='py-1 px-2'") as exp:
                with exp.add_slot("header"):
                    with ui.row().classes("items-center gap-2 w-full text-sm"):
                        ui.element("div").classes(
                            f"w-2 h-2 rounded-full {dot_cls} flex-shrink-0"
                        )
                        ui.label(et).classes(
                            f"px-1.5 py-0.5 rounded text-xs font-mono {badge_cls}"
                        )
                        ui.label(rel).classes(
                            "text-xs text-slate-400 flex-shrink-0"
                        )
                        if detail:
                            ui.label(detail).classes(
                                "text-xs text-slate-600 truncate"
                            )
                ui.code(json.dumps(evt, indent=2, default=str)).classes(
                    "text-xs w-full"
                )

    def _on_event(evt: dict) -> None:
        nonlocal t0
        if t0 is None:
            t0 = evt.get("occurred_at") or evt.get("received_at")

        all_events.append(evt)
        cat = _category_for(evt.get("event_type", ""))
        if cat in active_filters:
            _render_event_row(evt, cat)
            scroll_area.scroll_to(percent=1.0)

    unsubscribe = ui_subscribe(store, _on_event)
    return container, unsubscribe
