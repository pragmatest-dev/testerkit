"""Live FileStore streams panel.

Subscribes to :class:`~litmus.data.events.FileStarted` and
:class:`~litmus.data.events.FileEnded` events from the EventStore
and renders a row per stream: name / format / status (OPEN → DONE) /
size / link to the artifact viewer once the stream closes.

This is the **discovery** view per the lifecycle-only File event
model (see ``data-stores.md`` §6). Sample data — i.e. the bytes
themselves — flows on a separate transport (the file on disk;
consumers range-read or format-decode it). The event log tells
operators *what's open* and *what closed when*; clicking through
to the artifact viewer hands them the bytes.

Push-based: no polling — :func:`ui_subscribe` delivers each File*
event on the NiceGUI thread the moment it lands.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from uuid import UUID

from nicegui import ui

from litmus.data.event_store import EventStore
from litmus.ui.shared.event_binding import ui_subscribe
from litmus.ui.shared.timestamps import format_time_short

_STATUS_OPEN_CLS = "px-2 py-0.5 rounded text-xs font-semibold bg-cyan-100 text-cyan-800"
_STATUS_DONE_CLS = "px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800"


def create_file_streams_panel(
    store: EventStore,
    *,
    run_id: UUID | None = None,
) -> tuple[ui.column, Callable[[], None]]:
    """Live streams table — scoped to ``run_id`` when supplied.

    A row appears when ``FileStarted`` lands; its status flips from
    OPEN → DONE and the size + artifact link populate when the
    matching ``FileEnded`` (by ``file_id``) arrives.

    ``run_id`` filters the subscription to events whose ``run_id``
    matches — used by ``/live/{run_id}`` so operators only see streams
    from the run they're looking at. ``None`` shows everything (for
    diagnostic / aggregate views).

    Returns ``(container, unsubscribe)`` so the caller can stop
    updates on page teardown.
    """
    # file_id → (status_label, size_label, link_container)
    stream_rows: dict[str, tuple[ui.label, ui.label, ui.row]] = {}

    container = ui.column().classes("w-full gap-2")

    with container:
        placeholder = ui.label("No streams yet").classes("text-sm text-slate-400 italic")
        header = ui.row().classes("w-full px-3 py-1 border-b border-slate-200 hidden")
        with header:
            ui.label("Name").classes("w-1/4 text-xs font-semibold text-slate-500")
            ui.label("Format").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Status").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Size").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Opened").classes("w-1/6 text-xs font-semibold text-slate-500")
            ui.label("Artifact").classes("w-auto text-xs font-semibold text-slate-500")
        rows_container = ui.column().classes("w-full gap-0")

    placeholder_removed = False

    def _on_started(evt: dict) -> None:
        nonlocal placeholder_removed

        sid = evt.get("file_id")
        if not sid:
            return
        if sid in stream_rows:
            return  # idempotent — already shown

        if not placeholder_removed:
            placeholder_removed = True
            placeholder.delete()
            header.classes(remove="hidden")

        name = evt.get("name") or "(unnamed)"
        fmt = evt.get("format") or "?"
        opened_at = format_time_short(evt.get("occurred_at") or evt.get("received_at") or "")

        with rows_container:
            with ui.row().classes("w-full px-3 py-1.5 border-b border-slate-100 items-center"):
                ui.label(name).classes("w-1/4 text-sm font-mono text-slate-700")
                ui.label(fmt).classes("w-1/6 text-xs text-slate-500 font-mono")
                status_lbl = ui.label("OPEN").classes(_STATUS_OPEN_CLS)
                # wrap status so we can swap classes via remove/add later
                status_wrap = ui.row().classes("w-1/6 items-center")
                status_wrap.move(status_lbl)  # no-op container for layout consistency
                size_lbl = ui.label("—").classes("w-1/6 text-xs font-mono text-slate-500")
                ui.label(opened_at).classes("w-1/6 text-xs text-slate-400")
                link_container = ui.row().classes("w-auto items-center")
        stream_rows[sid] = (status_lbl, size_lbl, link_container)

    def _on_ended(evt: dict) -> None:
        sid = evt.get("file_id")
        if not sid or sid not in stream_rows:
            return
        status_lbl, size_lbl, link_container = stream_rows[sid]

        status_lbl.set_text("DONE")
        status_lbl.classes(remove=_STATUS_OPEN_CLS)
        status_lbl.classes(add=_STATUS_DONE_CLS)

        size = evt.get("size_bytes")
        if isinstance(size, int):
            size_lbl.set_text(_format_size(size))

        uri = evt.get("uri")
        if uri:
            with link_container:
                # /api/files resolves file:// URIs via FileStore (the run
                # may not be materialized yet, so /api/runs/{id}/ref isn't
                # available for live streams).
                encoded = urllib.parse.quote(uri, safe="")
                ui.link("open →", f"/api/files?uri={encoded}").classes(
                    "text-xs text-blue-600 hover:underline"
                )

    unsub_started = ui_subscribe(store, _on_started, event_type="file.started", run_id=run_id)
    unsub_ended = ui_subscribe(store, _on_ended, event_type="file.ended", run_id=run_id)

    def unsubscribe() -> None:
        unsub_started()
        unsub_ended()

    return container, unsubscribe


def _format_size(n: int) -> str:
    """Human-friendly byte size."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"
