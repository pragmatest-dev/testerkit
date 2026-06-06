"""Files browser — list every artifact written to FileStore."""

from __future__ import annotations

from typing import Any

from fastapi.responses import FileResponse
from nicegui import app, ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    format_file_size,
    page_header,
    page_layout,
    push_url_state,
    session_filter_banner,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_recent_files, resolve_file_uri

# Walk depth cap. The on-disk layout is unbounded as a glob; this
# limits memory + render time on huge projects. Past the cap, older
# artifacts need a date-window filter to reach.
_WALK_LIMIT = 1000

# What the table shows after filters are applied. Tuned for a single
# screen of rows on a typical viewport.
_PAGE_LIMIT = 200


def _row_for_artifact(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": entry["filename"],
        "mime": entry["mime"] or entry["extension"].lstrip("."),
        "size": format_file_size(entry["size_bytes"]),
        "created_at": format_datetime(entry["created_at"]),
        "uri": entry["uri"],
        "detail_url": f"/files/{entry['session_id']}/{entry['filename']}",
        "download_url": f"/files-static/{entry['session_id']}/{entry['filename']}?download=1",
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

    ``session_id`` is intentionally NOT here — it's URL-only and
    flows through the page-level parameter, never via a filter
    widget. Same shape as ``/events`` / ``/channels``.
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
    all_entries = list_recent_files(limit=_WALK_LIMIT)
    mime_options = _mime_options_from_entries(all_entries)
    initial_mime = mime if mime in mime_options else ""

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
            all_entries = list_recent_files(limit=_WALK_LIMIT)
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

            count_label.text = f"{len(filtered)} of {len(all_entries)} file(s)" + (
                f" (walked first {_WALK_LIMIT})" if len(all_entries) >= _WALK_LIMIT else ""
            )

            table_holder.clear()
            empty_state.clear()
            if not filtered:
                _show_empty_state(empty_state, has_data=bool(all_entries))
                return
            rows = [_row_for_artifact(e) for e in filtered]
            with table_holder:
                _build_table(rows)

        _apply_and_render()


def _build_table(rows: list[dict[str, Any]]) -> ui.table:
    columns = [
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


def _show_empty_state(slot: ui.column, *, has_data: bool) -> None:
    """Distinguish 'no files at all' from 'filters matched nothing'."""
    with slot, ui.card().classes("w-full"), ui.card_section():
        if has_data:
            ui.label("No files match the current filters.").classes("text-slate-500 italic")
            ui.label("Clear the filters above to see all artifacts.").classes(
                "text-xs text-slate-400"
            )
            return
        ui.label("No artifact files yet.").classes("text-slate-500 italic")
        ui.label(
            "Files appear once a test calls ``context.observe(name, value)`` with a "
            "blob value (PIL.Image / bytes / Pydantic model) or opens a "
            "``files.stream(name, format=...)`` sink."
        ).classes("text-xs text-slate-400")


@app.get("/files-static/{session_id}/{filename}")
def serve_file_artifact(session_id: str, filename: str, download: int = 0) -> FileResponse:
    """Serve an artifact file by its ``file://{session_id}/{filename}`` URI.

    Resolves through ``FileStore.resolve_uri`` so the date-partitioned
    on-disk layout stays an implementation detail. 404 when the URI
    doesn't match anything on disk.

    Pass ``?download=1`` to force ``Content-Disposition: attachment`` so
    the browser saves the file regardless of mime (otherwise the
    browser uses its own rules — inline for images/JSON/text, download
    for octet-stream).
    """
    path = resolve_file_uri(session_id, filename)
    if path is None or not path.exists():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"file://{session_id}/{filename}")
    if download:
        return FileResponse(path, filename=filename)
    return FileResponse(path)
