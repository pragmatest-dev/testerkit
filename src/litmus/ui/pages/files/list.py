"""Files browser — list every artifact written to FileStore."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi.responses import Response, StreamingResponse
from nicegui import app, ui

from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    format_file_size,
    page_header,
    page_layout,
    push_url_state,
    render_no_data_card,
    session_filter_banner,
)
from litmus.ui.shared.event_binding import ui_subscribe
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import files_dir_exists, list_recent_files

# Walk depth cap. The on-disk layout is unbounded as a glob; this
# limits memory + render time on huge projects. Past the cap, older
# artifacts need a date-window filter to reach.
_LIST_LIMIT = 1000

# What the table shows after filters are applied. Tuned for a single
# screen of rows on a typical viewport.
_PAGE_LIMIT = 200


def _row_for_artifact(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "live": "○ done",  # at-rest: a closed/one-shot artifact in the catalog
        "filename": entry["filename"],
        "mime": entry["mime"] or entry["extension"].lstrip("."),
        "size": format_file_size(entry["size_bytes"]),
        "created_at": format_datetime(entry["created_at"]),
        "uri": entry["uri"],
        # Routes carry the full key ({date}/{session_id}/{filename}) so the
        # detail/serve endpoints rebuild the exact URI — no date-less guess.
        "detail_url": f"/files/{entry['uri'].removeprefix('file://')}",
        "download_url": f"/files-static/{entry['uri'].removeprefix('file://')}?download=1",
    }


def _row_for_open_stream(stream_id: str, info: dict[str, Any]) -> dict[str, Any]:
    """A still-open stream: live, not yet in the catalog (no uri until close)."""
    return {
        "live": "● live",
        "filename": info.get("name") or "(stream)",
        "mime": info.get("format") or "stream",
        "size": "—",
        "created_at": format_datetime(info.get("started_at")),
        "uri": f"stream:{stream_id}",  # row key only — no link until close
        "detail_url": "",
        "download_url": "",
    }


def _apply_filters(
    entries: list[dict[str, Any]],
    *,
    session_id: str,
    mime: str,
    name: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Filter entries by session_id (URL-only), mime, filename substring, and date window.

    Empty strings are wildcards. Date comparisons use the ISO
    representation, which sorts correctly for the format
    ``format_datetime`` emits and ``datetime.isoformat()`` produces.
    """
    name_lower = name.lower()
    out: list[dict[str, Any]] = []
    for e in entries:
        if session_id and e["session_id"] != session_id:
            continue
        if mime and (e["mime"] or e["extension"].lstrip(".")) != mime:
            continue
        if name_lower and name_lower not in e["filename"].lower():
            continue
        if since or until:
            created_iso = e["created_at"].isoformat()
            if since and created_iso < since:
                continue
            if until and created_iso > until:
                continue
        out.append(e)
    return out


def _mime_options_from_entries(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Build the mime/extension dropdown values from observed entries."""
    mimes: dict[str, str] = {"": "(any)"}
    for e in entries:
        mime_or_ext = e["mime"] or e["extension"].lstrip(".")
        if mime_or_ext and mime_or_ext not in mimes:
            mimes[mime_or_ext] = mime_or_ext
    return mimes


class _Filters:
    """Lazy filter-value accessors so callbacks read the live widget state.

    The widget attributes are declared at class scope but populated
    on the INSTANCE after the widgets are constructed inside
    ``files_page``. An instance is not usable until each attribute
    has been assigned — the page guarantees this by construction
    (build the widgets, then call the getters). Do not type the
    attributes as ``ui.select | None`` to make this safer; the
    accessor methods would then need defensive ``None`` checks
    that obscure the actual access pattern.

    ``session_id`` is intentionally NOT a filter widget — it's
    URL-only and flows through the page-level parameter. Same
    shape as ``/events`` / ``/channels``.
    """

    mime_select: ui.select
    name_input: ui.input
    since_input: ui.input
    until_input: ui.input

    def mime(self) -> str:
        return (str(self.mime_select.value) if self.mime_select.value else "").strip()

    def name(self) -> str:
        return (self.name_input.value or "").strip()

    def since(self) -> str:
        return (self.since_input.value or "").strip()

    def until(self) -> str:
        return (self.until_input.value or "").strip()


@ui.page("/files")
def files_page(
    session_id: str = "",
    mime: str = "",
    name: str = "",
    since: str = "",
    until: str = "",
) -> None:
    """List artifact files captured by ``observe()`` / ``files.stream`` / ``files.write``.

    Filter state is mirrored into the URL via ``history.replaceState``
    so views are bookmarkable and shareable. Same pattern as
    ``/events`` / ``/channels`` / ``/metrics`` / ``/explore``.

    Session scoping is URL-only — set by deep-links from pages that
    already know the session (e.g. ``/results/{run_id}`` →
    ``/files?session_id=...``). The :func:`session_filter_banner` is
    the only affordance to clear it; UUIDs never appear in widgets.
    """
    create_layout("Files")

    # Walk once; filters apply in-memory against this snapshot. A
    # Refresh button re-walks the disk so newly-written artifacts
    # appear without a page reload.
    all_entries = list_recent_files(limit=_LIST_LIMIT)
    mime_options = _mime_options_from_entries(all_entries)
    initial_mime = mime if mime in mime_options else ""

    # Live status: open streams arrive as stream.started / stream.ended events
    # (an open stream has no catalog row until it closes). Track them in a
    # holder; the event callbacks only flip a dirty flag, and a ui.timer
    # re-renders when it's set (no per-tick rebuild → no flicker). Best-effort:
    # if the events daemon is down the table still lists at-rest artifacts.
    open_streams: dict[str, dict[str, Any]] = {}
    live_dirty = [False]
    try:
        _event_store = EventStore.get_shared(resolve_data_dir())

        def _on_stream_started(evt: dict) -> None:
            sid = evt.get("stream_id")
            if sid:
                open_streams[str(sid)] = {
                    "name": evt.get("name"),
                    "format": evt.get("format"),
                    "started_at": evt.get("occurred_at") or evt.get("received_at"),
                }
                live_dirty[0] = True

        def _on_stream_ended(evt: dict) -> None:
            sid = evt.get("stream_id")
            if sid and open_streams.pop(str(sid), None) is not None:
                live_dirty[0] = True  # now an at-rest catalog row on re-walk

        ui_subscribe(_event_store, _on_stream_started, event_type="stream.started")
        ui_subscribe(_event_store, _on_stream_ended, event_type="stream.ended")
    except (OSError, RuntimeError):
        pass

    with page_layout():
        page_header("Files")
        ui.label(
            "Artifact files captured by ``observe(value)`` for non-channel-shaped "
            "values (images, bytes, Pydantic models) and by ``files.stream(...)`` "
            "byte streams. Click the filename to open or download."
        ).classes("text-sm text-slate-500")

        # Session scoping is URL-only — same shape as /events,
        # /channels. The banner is the only affordance to clear.
        session_filter_banner(session_id, clear_path="/files")

        filters = _Filters()

        # Filter card renders FIRST (above table) per the operator-UI
        # consistency rule: filters must always appear above the data
        # they filter.
        with ui.card().classes("w-full").props('data-testid="files-filters"'):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.mime_select = ui.select(
                    mime_options,
                    value=initial_mime,
                    label="Type",
                    with_input=True,
                    on_change=lambda _: _apply_and_render(),
                ).classes("w-56")
                filters.name_input = ui.input(
                    "Filename contains", value=name, on_change=lambda _: _apply_and_render()
                ).classes("w-56")
                filters.since_input = ui.input(
                    "Since (ISO)", value=since, on_change=lambda _: _apply_and_render()
                ).classes("w-56")
                filters.until_input = ui.input(
                    "Until (ISO)", value=until, on_change=lambda _: _apply_and_render()
                ).classes("w-56")
                ui.button("Refresh", icon="refresh", on_click=lambda: refresh()).props(
                    "color=primary"
                )

        count_label = ui.label("…").classes("text-sm text-slate-600")
        table_holder = (
            ui.column().classes("w-full flex-1 min-h-0 gap-0").props('data-testid="files-table"')
        )
        empty_state = ui.column().classes("w-full")

        def refresh() -> None:
            # Re-walk in case new artifacts landed since the last render;
            # rebuild option dropdowns so newly-seen mimes become
            # selectable.
            nonlocal all_entries, mime_options
            all_entries = list_recent_files(limit=_LIST_LIMIT)
            mime_options = _mime_options_from_entries(all_entries)
            filters.mime_select.options = mime_options
            filters.mime_select.update()
            _apply_and_render()

        def _apply_and_render() -> None:
            push_url_state(
                "/files",
                {
                    # session_id is URL-only — preserved across refresh
                    # via the page-level param, not the filter widgets.
                    "session_id": session_id,
                    "mime": filters.mime(),
                    "name": filters.name(),
                    "since": filters.since(),
                    "until": filters.until(),
                },
            )
            filtered = _apply_filters(
                all_entries,
                session_id=session_id,
                mime=filters.mime(),
                name=filters.name(),
                since=filters.since(),
                until=filters.until(),
            )[:_PAGE_LIMIT]

            # Live (open) streams ride at the top — they have no catalog row
            # yet. Closed/one-shot artifacts come from the filtered catalog.
            live_rows = [_row_for_open_stream(sid, info) for sid, info in open_streams.items()]
            catalog_rows = [_row_for_artifact(e) for e in filtered]
            rows = live_rows + catalog_rows

            count_label.text = (
                f"{len(catalog_rows)} of {len(all_entries)} file(s)"
                + (f" (first {_LIST_LIMIT})" if len(all_entries) >= _LIST_LIMIT else "")
                + (f" · {len(live_rows)} live" if live_rows else "")
            )

            table_holder.clear()
            empty_state.clear()
            if not rows:
                _show_empty_state(
                    empty_state,
                    has_data=bool(all_entries),
                    dir_exists=files_dir_exists(),
                )
                return
            with table_holder:
                _build_table(rows)

        _apply_and_render()

        def _live_tick() -> None:
            # Re-render only when a stream opened/closed — re-walks the catalog
            # so a just-closed stream shows up as its at-rest row.
            if live_dirty[0]:
                live_dirty[0] = False
                refresh()

        ui.timer(0.5, _live_tick)


def _build_table(rows: list[dict[str, Any]]) -> ui.table:
    columns = [
        {"name": "live", "label": "Live", "field": "live", "align": "left"},
        {"name": "filename", "label": "Filename", "field": "filename", "align": "left"},
        {"name": "mime", "label": "Type", "field": "mime", "align": "left"},
        {"name": "size", "label": "Size", "field": "size", "align": "right"},
        {"name": "created_at", "label": "Created", "field": "created_at", "align": "left"},
        {"name": "actions", "label": "", "field": "actions", "align": "right"},
    ]
    table = data_table(
        columns=columns,
        rows=rows,
        row_key="uri",
        time_columns=["created_at"],
    )
    # Live cell: green ● live for an open stream, gray ○ done for at-rest.
    table.add_slot(
        "body-cell-live",
        '<q-td :props="props">'
        "<span :class=\"props.value === '● live' "
        "? 'text-emerald-600 font-semibold text-xs' : 'text-slate-400 text-xs'\">"
        "{{ props.value }}</span>"
        "</q-td>",
    )
    # Filename cell links to the detail page (metadata + inline viewer).
    table.add_slot(
        "body-cell-filename",
        '<q-td :props="props">'
        '<a :href="props.row.detail_url" '
        'class="text-blue-600 hover:underline font-mono text-xs">'
        "{{ props.value }}</a>"
        "</q-td>",
    )
    # Actions cell offers a direct download link bypassing the viewer.
    table.add_slot(
        "body-cell-actions",
        '<q-td :props="props">'
        '<a :href="props.row.download_url" '
        'class="text-blue-600 hover:underline text-xs">Download</a>'
        "</q-td>",
    )
    return table


def _show_empty_state(slot: ui.column, *, has_data: bool, dir_exists: bool) -> None:
    """Distinguish three empty causes: filtered-empty, no-data, no-dir.

    ``dir_exists=False`` signals a missing FileStore directory — either
    a fresh project that has never written a file, or a data wipe.
    Different copy from "no files yet (directory present)" so an
    operator looking at a long-running project notices the difference.
    """
    if has_data:
        render_no_data_card(
            slot,
            title="No files match the current filters.",
            reason="Clear the filters above to see all artifacts.",
            icon="folder_off",
        )
        return
    if not dir_exists:
        render_no_data_card(
            slot,
            title="FileStore directory not found.",
            reason=(
                "Either the project has never written a FileStore artifact, or the "
                "``files/`` directory under the project's data_dir has been removed. "
                "If this is a long-running project, check whether the data directory "
                "moved or was wiped. The directory is created on the first "
                "``context.observe(name, value)`` or ``files.write(...)`` call."
            ),
            icon="folder_off",
            emphasis="warning",
        )
        return
    render_no_data_card(
        slot,
        title="No artifact files yet.",
        reason=(
            "Files appear once a test calls ``context.observe(name, value)`` with a "
            "blob value (PIL.Image / bytes / Pydantic model) or opens a "
            "``files.stream(name, format=...)`` sink."
        ),
        icon="folder",
    )


@app.get("/files-static/{date}/{session_id}/{filename}")
def serve_file_artifact(date: str, session_id: str, filename: str, download: int = 0) -> Response:
    """Serve an artifact by its ``file://{date}/{session_id}/{filename}`` URI.

    Streams the bytes through the FileStore (blob backend) — the on-disk /
    object-store layout stays an implementation detail and the body is
    never buffered whole. 404 when the URI resolves to nothing.

    Pass ``?download=1`` to force ``Content-Disposition: attachment`` so
    the browser saves the file regardless of mime (otherwise the
    browser uses its own rules — inline for images/JSON/text, download
    for octet-stream).
    """
    from fastapi import HTTPException

    from litmus.api._mime import sniff_mime
    from litmus.data.files import get_filestore

    uri = f"file://{date}/{session_id}/{filename}"
    store = get_filestore()
    size = store.size(uri)
    handle = store.open_input(uri)
    if size is None or handle is None:
        raise HTTPException(status_code=404, detail=uri)
    media_type = sniff_mime(store.read_range(uri, offset=0, length=64) or b"")

    def _body() -> Iterator[bytes]:
        try:
            while chunk := handle.read(1 << 20):
                yield chunk
        finally:
            handle.close()

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(_body(), media_type=media_type, headers=headers)
