"""Files browser — list every artifact written to FileStore."""

from __future__ import annotations

from typing import Any

from fastapi.responses import FileResponse
from nicegui import app, ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_header,
    page_layout,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_recent_files, resolve_file_uri

_RECENT_LIMIT = 200


def _format_size(size_bytes: int) -> str:
    """Render byte count as KB / MB for the table cell."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _row_for_artifact(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": entry["filename"],
        "session_id": entry["session_id"][:12],
        "session_full": entry["session_id"],
        "mime": entry["mime"] or entry["extension"].lstrip("."),
        "size": _format_size(entry["size_bytes"]),
        "created_at": format_datetime(entry["created_at"]),
        "uri": entry["uri"],
        "detail_url": f"/files/{entry['session_id']}/{entry['filename']}",
        "download_url": f"/files-static/{entry['session_id']}/{entry['filename']}?download=1",
    }


@ui.page("/files")
def files_page() -> None:
    """List artifact files written by ``observe()`` / ``files.stream`` / ``files.write``.

    Each row shows filename, session, mime/extension, size, and a
    download link. Rows are sorted newest-first. The table caps at
    200 entries — the data path is a directory walk, fast for typical
    project sizes but unbounded as a glob.
    """
    create_layout("Files")

    with page_layout():
        page_header("Files")
        ui.label(
            "Artifact files captured by ``observe(value)`` for non-channel-shaped "
            "values (images, bytes, Pydantic models) and by ``files.stream(...)`` "
            "byte streams. Click the filename to open or download."
        ).classes("text-sm text-slate-500")

        count_label = ui.label("…").classes("text-sm text-slate-600")

        table_holder = (
            ui.column().classes("w-full flex-1 min-h-0 gap-0").props('data-testid="files-table"')
        )
        empty_state = ui.column().classes("w-full")

        def refresh() -> None:
            entries = list_recent_files(limit=_RECENT_LIMIT)
            count_label.text = f"{len(entries)} file(s) — showing most recent {_RECENT_LIMIT}"
            table_holder.clear()
            empty_state.clear()
            if not entries:
                _show_empty_state(empty_state)
                return
            rows = [_row_for_artifact(e) for e in entries]
            with table_holder:
                _build_table(rows)

        refresh()

        ui.button("Refresh", icon="refresh", on_click=refresh).classes("self-end")


def _build_table(rows: list[dict[str, Any]]) -> ui.table:
    columns = [
        {"name": "filename", "label": "Filename", "field": "filename", "align": "left"},
        {"name": "session_id", "label": "Session", "field": "session_id", "align": "left"},
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


def _show_empty_state(slot: ui.column) -> None:
    with slot, ui.card().classes("w-full"), ui.card_section():
        ui.label("No artifact files yet.").classes("text-slate-500 italic")
        ui.label(
            "Files appear once a test calls ``context.observe(name, value)`` with a "
            "blob value (PIL.Image / bytes / Pydantic model) or opens a "
            "``files.stream(name, format=...)`` sink."
        ).classes("text-xs text-slate-400")


@app.get("/files-static/{session_id}/{filename}")
def serve_file_artifact(session_id: str, filename: str, download: int = 0) -> FileResponse:
    """Serve an artifact file by its ``file://{session_id}/{filename}`` URI.

    Resolves through ``FileStore._resolve_uri`` so the date-partitioned
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
