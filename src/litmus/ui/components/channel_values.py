"""Live channel values table — Position 2 reshape.

Subscribes to :class:`~litmus.data.events.ChannelStarted` for discovery
(one row per channel that ever wrote in this run) and to the per-channel
ChannelStore Flight signal for live sample values.

Follows the live-UI rule (``docs/_internal/explorations/live-ui-pattern.md``):
the sample callback writes only a plain per-row holder; a single
``ui.timer`` is the sole renderer. Discovery (adding a row) and close
(marking a row) are structural and run on the UI loop via
:func:`ui_subscribe`. So nothing off the UI loop touches an element, and
the per-sample path never mutates UI directly.

This is the canonical operator view: a stable list of channels with their
latest reading, no per-sample event flood.

Push-based: no polling for data — the timer only paints what already arrived.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from nicegui import ui

from litmus.data.channels.models import ChannelSample
from litmus.data.event_store import EventStore
from litmus.ui.shared.event_binding import ui_channel_data, ui_subscribe
from litmus.ui.shared.timestamps import format_time_short


@dataclass
class _ChannelRow:
    """Per-channel row: label handles + the latest reading the timer paints."""

    val_lbl: ui.label
    unit_lbl: ui.label
    ts_lbl: ui.label
    value: object = None
    unit: str = ""
    ts: str = ""
    closed: bool = False
    dirty: bool = True


def create_channel_values_panel(
    store: EventStore,
    *,
    run_id: UUID | None = None,
) -> tuple[ui.column, Callable[[], None]]:
    """Auto-discover channels and show a live-values table.

    ``run_id`` filters the ``ChannelStarted`` discovery subscription to the
    matching run — operators on ``/live/{run_id}`` only see channels from
    the run they're looking at. ``None`` shows everything.

    Live sample values come from :func:`ui_channel_data` (wired by the
    application's :func:`bind_channel_store` at startup). The sample
    callback only records the reading; a ``ui.timer`` paints it.

    Returns ``(container, unsubscribe)`` so the caller can stop discovery
    updates on page teardown.
    """
    rows: dict[str, _ChannelRow] = {}

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
        return "—" if value is None else str(value)

    def _on_channel_started(evt: dict) -> None:
        """Discovery: add a row + a sample subscription that fills its holder."""
        nonlocal placeholder_removed

        ch_id = evt.get("channel_id")
        if not ch_id or ch_id in rows:
            return

        if not placeholder_removed:
            placeholder_removed = True
            placeholder.delete()
            header.classes(remove="hidden")

        with rows_container:
            with ui.row().classes("w-full px-3 py-1.5 border-b border-slate-100 items-center"):
                ui.label(ch_id).classes("w-1/3 text-sm font-mono text-slate-700")
                val_lbl = ui.label("—").classes("w-1/4 text-sm font-mono font-semibold")
                unit_lbl = ui.label(evt.get("unit") or "").classes("w-1/6 text-xs text-slate-500")
                ts_lbl = ui.label("").classes("w-1/4 text-xs text-slate-400")

        row = _ChannelRow(val_lbl, unit_lbl, ts_lbl, unit=evt.get("unit") or "")
        rows[ch_id] = row

        # Sample callback: record the reading only — no UI mutation here.
        def _on_sample(sample: ChannelSample, _row: _ChannelRow = row) -> None:
            _row.value = sample.value
            if sample.unit:
                _row.unit = sample.unit
            _row.ts = format_time_short(sample.received_at.isoformat())
            _row.dirty = True

        ui_channel_data(ch_id).subscribe(_on_sample)

    def _on_channel_closed(evt: dict) -> None:
        """Lifecycle close — mark the row so the timer italicizes it."""
        ch_id = evt.get("channel_id")
        row = rows.get(ch_id) if ch_id else None
        if row is not None:
            row.closed = True
            row.dirty = True

    def _render() -> None:
        """Sole renderer: paint the rows whose holders changed."""
        for row in rows.values():
            if not row.dirty:
                continue
            row.dirty = False
            row.val_lbl.set_text(_format_value(row.value))
            row.unit_lbl.set_text(row.unit)
            if row.closed:
                row.ts_lbl.classes(add="italic")
                row.ts_lbl.set_text(f"closed · {row.ts}" if row.ts else "closed")
            else:
                row.ts_lbl.set_text(row.ts)

    ui.timer(0.25, _render)

    unsub_started = ui_subscribe(
        store, _on_channel_started, event_type="channel.started", run_id=run_id
    )
    unsub_closed = ui_subscribe(
        store, _on_channel_closed, event_type="channel.ended", run_id=run_id
    )

    def unsubscribe() -> None:
        unsub_started()
        unsub_closed()

    return container, unsubscribe
