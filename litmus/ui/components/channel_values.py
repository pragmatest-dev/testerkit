"""Live channel values table.

Shows a live-updating table of latest channel values,
auto-discovering channels from EventStore instrument events.
Uses push-based subscriptions (no polling).
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from litmus.data.event_store import EventStore
from litmus.ui.shared.event_binding import ui_subscribe
from litmus.ui.shared.timestamps import format_time_short

_INSTRUMENT_EVENT_TYPES = {"instrument.read", "instrument.set"}


def create_channel_values_panel(
    store: EventStore,
) -> tuple[ui.column, Callable[[], None]]:
    """Auto-discover channels and show a live-values table.

    Returns (container, unsubscribe) so the caller can stop updates.
    """

    # channel_id → (value_label, units_label, timestamp_label)
    channel_rows: dict[str, tuple[ui.label, ui.label, ui.label]] = {}

    container = ui.column().classes("w-full gap-2")

    with container:
        placeholder = ui.label("No instrument data yet").classes("text-sm text-slate-400 italic")
        # Table header (hidden until first data)
        header = ui.row().classes("w-full px-3 py-1 border-b border-slate-200 hidden")
        with header:
            ui.label("Channel").classes("w-1/3 text-xs font-semibold text-slate-500")
            ui.label("Value").classes("w-1/4 text-xs font-semibold text-slate-500")
            ui.label("Units").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Last Update").classes("w-1/4 text-xs font-semibold text-slate-500")
        rows_container = ui.column().classes("w-full gap-0")

    placeholder_removed = False

    def _on_event(evt: dict) -> None:
        nonlocal placeholder_removed

        if evt.get("event_type") not in _INSTRUMENT_EVENT_TYPES:
            return

        ch_id = evt.get("channel_id")
        val = evt.get("value")
        if ch_id is None or val is None:
            return
        try:
            val = float(val)
        except (TypeError, ValueError):
            return

        ts = evt.get("occurred_at") or evt.get("received_at") or ""
        units = evt.get("units") or ""
        ts_short = format_time_short(ts)

        if not placeholder_removed:
            placeholder_removed = True
            placeholder.delete()
            header.classes(remove="hidden")

        if ch_id in channel_rows:
            val_lbl, units_lbl, ts_lbl = channel_rows[ch_id]
            val_lbl.set_text(f"{val:.4g}")
            units_lbl.set_text(units)
            ts_lbl.set_text(ts_short)
        else:
            with rows_container:
                with ui.row().classes("w-full px-3 py-1.5 border-b border-slate-100 items-center"):
                    ui.label(ch_id).classes("w-1/3 text-sm font-mono text-slate-700")
                    val_lbl = ui.label(f"{val:.4g}").classes(
                        "w-1/4 text-sm font-mono font-semibold"
                    )
                    units_lbl = ui.label(units).classes("w-1/6 text-xs text-slate-500")
                    ts_lbl = ui.label(ts_short).classes("w-1/4 text-xs text-slate-400")
                channel_rows[ch_id] = (val_lbl, units_lbl, ts_lbl)

    unsubscribe = ui_subscribe(store, _on_event)
    return container, unsubscribe
