"""Live channel values table — Position 2 reshape.

Subscribes to :class:`~litmus.data.events.ChannelStarted` for
discovery (one row per channel that ever wrote in this run) and to
the per-channel ChannelStore Flight signal for live sample values
(value + units + last-update timestamp updated push-style on every
sample).

This is the canonical operator view: a stable list of channels with
their latest reading, no per-sample event flood. Replaces the
pre-Position-2 wiring that filtered ``instrument.read`` events
(retired in C1) and is the operator-side counterpart to the design
doc's "EventStore for discovery, sample transport for data"
split.

Push-based: no polling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from nicegui import ui

from litmus.data.channels.models import ChannelSample
from litmus.data.event_store import EventStore
from litmus.ui.shared.event_binding import ui_channel_data, ui_subscribe
from litmus.ui.shared.timestamps import format_time_short


def create_channel_values_panel(
    store: EventStore,
    *,
    run_id: UUID | None = None,
) -> tuple[ui.column, Callable[[], None]]:
    """Auto-discover channels and show a live-values table.

    ``run_id`` filters the ``ChannelStarted`` discovery subscription to
    the matching run — operators on ``/live/{run_id}`` only see channels
    from the run they're looking at. ``None`` shows everything.

    Live sample values come from :func:`ui_channel_data` (NiceGUI
    Event[ChannelSample]) — wired across all channels by the
    application's :func:`bind_channel_store` call at startup. No
    per-channel subscription bookkeeping in this component.

    Returns ``(container, unsubscribe)`` so the caller can stop
    discovery updates on page teardown.
    """
    # channel_id → (value_label, units_label, timestamp_label, sample_unsub)
    channel_rows: dict[str, tuple[ui.label, ui.label, ui.label, Callable[[], None]]] = {}

    container = ui.column().classes("w-full gap-2")

    with container:
        placeholder = ui.label("No channels yet").classes("text-sm text-slate-400 italic")
        # Table header (hidden until first data)
        header = ui.row().classes("w-full px-3 py-1 border-b border-slate-200 hidden")
        with header:
            ui.label("Channel").classes("w-1/3 text-xs font-semibold text-slate-500")
            ui.label("Value").classes("w-1/4 text-xs font-semibold text-slate-500")
            ui.label("Units").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Last Update").classes("w-1/4 text-xs font-semibold text-slate-500")
        rows_container = ui.column().classes("w-full gap-0")

    placeholder_removed = False

    def _format_value(value: object) -> str:
        if isinstance(value, (int, float)):
            return f"{value:.4g}"
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            # Array sample — show length, not the whole array
            return f"[{len(value)} samples]"
        return str(value)

    def _on_channel_started(evt: dict) -> None:
        """Discovery: add a row + subscribe to live samples for this channel."""
        nonlocal placeholder_removed

        ch_id = evt.get("channel_id")
        if not ch_id or ch_id in channel_rows:
            return

        if not placeholder_removed:
            placeholder_removed = True
            placeholder.delete()
            header.classes(remove="hidden")

        units_from_event = evt.get("units") or ""

        with rows_container:
            with ui.row().classes("w-full px-3 py-1.5 border-b border-slate-100 items-center"):
                ui.label(ch_id).classes("w-1/3 text-sm font-mono text-slate-700")
                val_lbl = ui.label("—").classes("w-1/4 text-sm font-mono font-semibold")
                units_lbl = ui.label(units_from_event).classes("w-1/6 text-xs text-slate-500")
                ts_lbl = ui.label("").classes("w-1/4 text-xs text-slate-400")

        # Subscribe to live samples for this channel — values stream over
        # the per-channel NiceGUI Event signal (wired in by the page's
        # bind_channel_store at startup; no Flight client per-component).
        def _on_sample(sample: ChannelSample) -> None:
            val_lbl.set_text(_format_value(sample.value))
            if sample.units:
                units_lbl.set_text(sample.units)
            ts_lbl.set_text(format_time_short(sample.received_at.isoformat()))

        signal = ui_channel_data(ch_id)
        signal.subscribe(_on_sample)

        def _sample_unsub() -> None:
            # NiceGUI Event has no .unsubscribe(callback); cleanup happens
            # on client disconnect. Track for completeness in case API
            # gains explicit unsub later.
            pass

        channel_rows[ch_id] = (val_lbl, units_lbl, ts_lbl, _sample_unsub)

    def _on_channel_closed(evt: dict) -> None:
        """Lifecycle close — mark the row visually (italicize timestamp)."""
        ch_id = evt.get("channel_id")
        if not ch_id or ch_id not in channel_rows:
            return
        _val_lbl, _units_lbl, ts_lbl, _ = channel_rows[ch_id]
        # Stamp close time + italicize so operators see "no more samples"
        ts_lbl.classes(add="italic")
        ts_lbl.set_text(f"closed {format_time_short(datetime.now(UTC).isoformat())}")

    unsub_started = ui_subscribe(
        store, _on_channel_started, event_type="channel.started", run_id=run_id
    )
    unsub_closed = ui_subscribe(
        store, _on_channel_closed, event_type="channel.closed", run_id=run_id
    )

    def unsubscribe() -> None:
        unsub_started()
        unsub_closed()

    return container, unsubscribe
