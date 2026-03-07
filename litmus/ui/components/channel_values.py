"""Live channel values table.

Shows a live-updating table of latest channel values,
auto-discovering channels from the JSONL event log.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from nicegui import ui

from litmus.data.event_reader import EventReader, find_session_log
from litmus.ui.shared.timestamps import format_time_short

_INSTRUMENT_EVENT_TYPES = {"instrument.read", "instrument.set"}


class ChannelPoint(NamedTuple):
    """A single channel data point extracted from an instrument event."""

    timestamp: str
    value: float
    units: str


def extract_channel_points(
    events: list[dict],
) -> dict[str, list[ChannelPoint]]:
    """Extract channel time-series points from instrument events.

    Returns {channel_id: [ChannelPoint, ...]}.
    Events with missing channel_id or non-numeric values are silently skipped.
    """
    channels: dict[str, list[ChannelPoint]] = {}
    for evt in events:
        if evt.get("event_type") not in _INSTRUMENT_EVENT_TYPES:
            continue
        ch = evt.get("channel_id")
        val = evt.get("value")
        if ch is None or val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        ts = evt.get("occurred_at") or evt.get("received_at") or ""
        units = evt.get("units") or ""
        channels.setdefault(ch, []).append(ChannelPoint(ts, val, units))
    return channels


def create_channel_values_panel(
    events_dir: Path, poll_interval: float = 0.5
) -> tuple[ui.column, ui.timer]:
    """Auto-discover channels and show a live-values table.

    Returns (container, timer) so the caller can deactivate the timer.
    """

    reader: EventReader | None = None
    # channel_id → (value_label, units_label, timestamp_label)
    channel_rows: dict[str, tuple[ui.label, ui.label, ui.label]] = {}

    container = ui.column().classes("w-full gap-2")

    with container:
        placeholder = ui.label("No instrument data yet").classes(
            "text-sm text-slate-400 italic"
        )
        # Table header (hidden until first data)
        header = ui.row().classes("w-full px-3 py-1 border-b border-slate-200 hidden")
        with header:
            ui.label("Channel").classes("w-1/3 text-xs font-semibold text-slate-500")
            ui.label("Value").classes("w-1/4 text-xs font-semibold text-slate-500")
            ui.label("Units").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Last Update").classes("w-1/4 text-xs font-semibold text-slate-500")
        rows_container = ui.column().classes("w-full gap-0")

    placeholder_removed = False

    def poll():
        nonlocal reader, placeholder_removed

        if reader is None:
            path = find_session_log(events_dir)
            if path is None:
                return
            reader = EventReader(path)

        new_events = reader.read_new()
        if not new_events:
            return

        points = extract_channel_points(new_events)
        if not points:
            return

        # Remove placeholder on first data
        if not placeholder_removed:
            placeholder_removed = True
            placeholder.delete()
            header.classes(remove="hidden")

        for ch_id, ch_points in points.items():
            latest_ts, latest_val, latest_units = ch_points[-1]
            ts_short = format_time_short(latest_ts)

            if ch_id in channel_rows:
                val_lbl, units_lbl, ts_lbl = channel_rows[ch_id]
                val_lbl.set_text(f"{latest_val:.4g}")
                units_lbl.set_text(latest_units)
                ts_lbl.set_text(ts_short)
            else:
                with rows_container:
                    with ui.row().classes(
                        "w-full px-3 py-1.5 border-b border-slate-100 items-center"
                    ):
                        ui.label(ch_id).classes(
                            "w-1/3 text-sm font-mono text-slate-700"
                        )
                        val_lbl = ui.label(f"{latest_val:.4g}").classes(
                            "w-1/4 text-sm font-mono font-semibold"
                        )
                        units_lbl = ui.label(latest_units).classes(
                            "w-1/6 text-xs text-slate-500"
                        )
                        ts_lbl = ui.label(ts_short).classes(
                            "w-1/4 text-xs text-slate-400"
                        )
                    channel_rows[ch_id] = (val_lbl, units_lbl, ts_lbl)

    timer = ui.timer(poll_interval, poll)
    return container, timer
