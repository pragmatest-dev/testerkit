"""File detail page — metadata + mime-switched viewer + download."""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from nicegui import ui

from litmus.data.files import get_filestore
from litmus.data.files.models import FileArtifactMetadata
from litmus.ui.shared.components import (
    format_datetime,
    format_file_size,
    info_field,
    lookup_session_label,
    page_header,
    page_layout,
)
from litmus.ui.shared.layout import create_layout

# Hard cap on in-memory viewer payload size. Files larger than this
# render as a metadata card + Download button only; the browser never
# loads them. Tuned at 2 MB — bigger than any realistic JSON / JSONL
# log, small enough to keep the page responsive on a slow link.
_VIEWER_SIZE_CAP_BYTES = 2 * 1024 * 1024

# Hard cap on JSONL row count. A 2 MB JSONL with ~50-byte lines is
# ~40k rows — q-table can render it but the browser lags. The cap
# truncates with a "showing first N of M" note; the operator can
# Download to get the full file.
_JSONL_ROW_CAP = 5_000

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_VIDEO_EXTS = {".mp4", ".webm"}
_TEXT_EXTS = {".txt", ".log", ".md"}


@ui.page("/files/{date}/{session_id}/{filename}")
def file_detail_page(date: str, session_id: str, filename: str) -> None:
    """Show one FileStore artifact with metadata + inline viewer + download.

    The viewer dispatches on the on-disk file extension (cheapest
    reliable signal — the sidecar mime can be missing if the artifact
    was hand-placed). Falls back to a metadata-only card for binary
    types or files larger than the viewer cap.
    """
    create_layout("File")
    store = get_filestore()
    uri = f"file://{date}/{session_id}/{filename}"
    size = store.size(uri)

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            page_header(filename)
            ui.button("Back", icon="arrow_back", on_click=lambda: ui.navigate.to("/files")).props(
                "flat"
            )

        if size is None:
            with ui.card().classes("w-full p-6"):
                ui.label(f"File not found: file://{date}/{session_id}/{filename}").classes(
                    "text-slate-600"
                )
                ui.link("← Back to Files", "/files").classes("text-blue-600 hover:underline")
            return

        # Extension drives the viewer (cheapest reliable signal — the sidecar
        # mime can be missing). PurePosixPath does string-only suffix parsing,
        # never a filesystem touch.
        ext = PurePosixPath(filename).suffix.lower()
        meta = store.read_attributes(uri) or FileArtifactMetadata(
            mime="", extension=ext, size_bytes=size, attributes={}
        )
        download_url = f"/files-static/{session_id}/{filename}?download=1"
        view_url = f"/files-static/{session_id}/{filename}"

        _render_metadata_card(session_id, meta, store.modified_at(uri), download_url)
        _render_viewer(store, uri, ext, size, view_url)


def _render_metadata_card(
    session_id: str,
    meta: FileArtifactMetadata,
    modified: datetime | None,
    download_url: str,
) -> None:
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full justify-between items-start gap-4 p-4 flex-wrap"):
            info_field("MIME", meta.mime or "—")
            info_field("Extension", meta.extension or "—")
            info_field("Size", format_file_size(meta.size_bytes))
            if modified is not None:
                info_field("Modified", format_datetime(modified))
            # Resolve session_id to an operator-readable label
            # (``<dut_serial> · <YYYY-MM-DD HH:MM:SS>``) rather than
            # leaking the raw UUID. Same convention as the session
            # filter banner on /events / /channels / /files.
            # When the session is unknown (stale bookmark, deleted
            # session, typo), emphasize the field amber so the
            # asymmetry with a regular "(unknown)" string is visible,
            # matching ``session_filter_banner``'s found/not-found
            # state distinction.
            session_label, session_found = lookup_session_label(session_id)
            if session_found:
                info_field("Session", session_label)
            else:
                info_field(
                    "Session",
                    f'<span class="text-amber-700">{session_label} (session not found)</span>',
                )
            ui.button(
                "Download",
                icon="download",
                on_click=lambda: ui.navigate.to(download_url),
            ).props("color=primary")

        attributes = meta.attributes or {}
        if attributes:
            ui.separator()
            with ui.column().classes("w-full p-4 gap-2"):
                ui.label("Attributes").classes("text-sm font-medium text-slate-600 uppercase")
                with ui.column().classes("gap-1"):
                    for key, value in sorted(attributes.items()):
                        with ui.row().classes("gap-2 items-baseline"):
                            ui.label(f"{key}:").classes("text-sm text-slate-500 font-mono")
                            ui.label(str(value)).classes("text-sm font-mono")


def _render_viewer(store: Any, uri: str, ext: str, size: int, view_url: str) -> None:
    """Pick a viewer based on file extension. Caps oversized files.

    Image/video stream through the ``view_url`` HTTP route (the browser
    fetches them). Content viewers (json/csv/npz/…) parse the whole
    artifact in-process, so they pull the bytes through the store — never
    the filesystem, so a remote backend serves them the same way.
    """
    if size > _VIEWER_SIZE_CAP_BYTES:
        with ui.card().classes("w-full p-6"):
            ui.label(
                f"File is {format_file_size(size)} — too large for in-page viewer "
                f"(cap {format_file_size(_VIEWER_SIZE_CAP_BYTES)}). Use Download."
            ).classes("text-slate-600")
        return

    if ext in _IMAGE_EXTS:
        with ui.card().classes("w-full p-4 flex justify-center"):
            ui.image(view_url).classes("max-w-full max-h-[70vh] object-contain")
        return

    if ext in _VIDEO_EXTS:
        with ui.card().classes("w-full p-4"):
            ui.video(view_url, controls=True).classes("w-full")
        return

    content_viewers = {
        ".json": _render_json_viewer,
        ".jsonl": _render_jsonl_viewer,
        ".ndjson": _render_jsonl_viewer,
        ".csv": _render_csv_viewer,
        ".npz": _render_npz_waveform_viewer,
        ".npy": _render_npy_viewer,
    }
    viewer = content_viewers.get(ext) or (_render_text_viewer if ext in _TEXT_EXTS else None)
    if viewer is None:
        with ui.card().classes("w-full p-6"):
            ui.label(
                f"No inline viewer for {ext or 'unknown'} files — use Download to open externally."
            ).classes("text-slate-600")
        return

    data = store.read(uri)
    if data is None:
        with ui.card().classes("w-full p-4"):
            ui.label("Could not read file contents.").classes("text-red-600")
        return
    viewer(data)


def _render_json_viewer(data: bytes) -> None:
    try:
        parsed = json.loads(data.decode("utf-8"))
        pretty = json.dumps(parsed, indent=2, default=str)
    except (UnicodeDecodeError, ValueError) as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not parse JSON: {exc}").classes("text-red-600")
        return
    with ui.card().classes("w-full p-4"):
        ui.label("JSON").classes("text-sm font-medium text-slate-600 uppercase mb-2")
        ui.html(
            f'<pre class="text-xs whitespace-pre-wrap font-mono" '
            f'style="max-height:60vh;overflow:auto">{html.escape(pretty)}</pre>',
            sanitize=False,
        )


def _render_jsonl_viewer(data: bytes) -> None:
    """Render each JSONL line as a row in a table. Columns = union of keys.

    Streams line-by-line and stops at ``_JSONL_ROW_CAP`` rows so a
    JSONL file with tens of thousands of small lines doesn't lag the
    browser. Truncation is reported in the header so operators see
    "showing first N of M" rather than silently missing rows. The
    full file is always available via the Download button.

    Three counters are tracked separately so the header copy doesn't
    overlap:

    * ``rows`` (= ``len(rows)``) — successful parses actually rendered.
    * ``total_parsed`` — successful parses observed across the whole
      file (may exceed ``len(rows)`` when truncation kicks in).
    * ``parse_errors`` — full-file count of lines that failed
      ``json.loads`` (counted before the row cap so the operator
      sees the true count, not just the count of errors before
      truncation).
    """
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    total_parsed = 0
    truncated = False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not read file: {exc}").classes("text-red-600")
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            parse_errors += 1
            continue
        total_parsed += 1
        if len(rows) >= _JSONL_ROW_CAP:
            truncated = True
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            rows.append({"value": obj})

    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)

    with ui.card().classes("w-full p-4"):
        # Header counts are non-overlapping: ``rows shown`` and
        # ``total_parsed`` are the same category (successful parses),
        # ``parse_errors`` is its own category. Truncation only
        # affects how many successful parses landed in the table.
        if truncated:
            header = (
                f"JSONL · showing first {_JSONL_ROW_CAP} of "
                f"{total_parsed} successful entries — Download for full file"
            )
        else:
            header = f"JSONL · {len(rows)} entries"
        if parse_errors:
            header += f" ({parse_errors} unparseable, file-wide)"
        ui.label(header).classes("text-sm font-medium text-slate-600 uppercase mb-2")
        if not rows:
            ui.label("(no entries)").classes("text-slate-500 italic")
            return
        columns = [{"name": k, "label": k, "field": k, "align": "left"} for k in keys]
        # Stringify nested values so q-table doesn't choke on dicts/lists.
        rendered_rows = [
            {k: _format_jsonl_cell(row.get(k)) for k in keys} | {"_idx": i}
            for i, row in enumerate(rows)
        ]
        ui.table(columns=columns, rows=rendered_rows, row_key="_idx").classes("w-full")


def _format_jsonl_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _render_csv_viewer(data: bytes) -> None:
    """Parse CSV and render as a q-table. First row is treated as header."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not read CSV: {exc}").classes("text-red-600")
        return

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        with ui.card().classes("w-full p-4"):
            ui.label("(empty CSV)").classes("text-slate-500 italic")
        return

    rows: list[dict[str, str]] = []
    for i, raw in enumerate(reader):
        # Pad short rows so q-table never sees missing keys; truncate long
        # rows to header width to match what a spreadsheet shows.
        padded = (list(raw) + [""] * len(header))[: len(header)]
        rows.append({col: padded[j] for j, col in enumerate(header)} | {"_idx": str(i)})

    columns = [{"name": col, "label": col, "field": col, "align": "left"} for col in header]
    with ui.card().classes("w-full p-4"):
        ui.label(f"CSV · {len(rows)} rows · {len(header)} columns").classes(
            "text-sm font-medium text-slate-600 uppercase mb-2"
        )
        if not rows:
            ui.label("(no rows)").classes("text-slate-500 italic")
            return
        ui.table(columns=columns, rows=rows, row_key="_idx").classes("w-full")


def _render_npz_waveform_viewer(data: bytes) -> None:
    """Load .npz arrays and plot the first numeric array as a line chart.

    Matches the FileStore Waveform serializer which writes ``y`` (the
    sample array) and optional ``x`` / ``t0`` / ``dt`` / ``attributes``
    via ``np.savez``. Falls back to a stats card when no numeric array
    is found.
    """
    # Defer ~150ms numpy import to first .npz file open. This page
    # module is imported at NiceGUI startup; a top-level numpy import
    # would slow every operator-UI cold start, even when the user
    # never opens an .npz file.
    import numpy as np  # noqa: PLC0415

    try:
        with np.load(io.BytesIO(data), allow_pickle=True) as archive:
            arrays = {k: archive[k] for k in archive.files}
    except (OSError, ValueError) as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not load .npz: {exc}").classes("text-red-600")
        return

    y_arr = arrays.get("y") if "y" in arrays else next(iter(arrays.values()), None)
    if y_arr is None or not hasattr(y_arr, "shape") or y_arr.size == 0:
        with ui.card().classes("w-full p-4"):
            ui.label("No plottable array found in .npz.").classes("text-slate-600")
        return

    y_list = y_arr.flatten().tolist()
    # If the .npz includes ``x``, use it as the x-axis; otherwise sample-index.
    x_arr = arrays.get("x")
    if x_arr is not None and hasattr(x_arr, "size") and x_arr.size == len(y_list):
        x_axis = [f"{v:.4g}" for v in x_arr.flatten().tolist()]
    else:
        x_axis = [str(i) for i in range(len(y_list))]

    with ui.card().classes("w-full p-4"):
        ui.label(f"Waveform · {len(y_list)} samples").classes(
            "text-sm font-medium text-slate-600 uppercase mb-2"
        )
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": x_axis},
                "yAxis": {"type": "value"},
                "series": [
                    {
                        "type": "line",
                        "data": y_list,
                        "showSymbol": False,
                        "smooth": False,
                    }
                ],
                "grid": {"left": 60, "right": 30, "top": 30, "bottom": 50},
            }
        ).classes("w-full h-96")


def _render_npy_viewer(data: bytes) -> None:
    """Show ndarray stats + first 100 flat values inline."""
    # Defer ~150ms numpy import to first .npy file open. Same
    # module-load avoidance as _render_npz_waveform_viewer.
    import numpy as np  # noqa: PLC0415

    try:
        arr = np.load(io.BytesIO(data), allow_pickle=False)
    except (OSError, ValueError) as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not load .npy: {exc}").classes("text-red-600")
        return

    with ui.card().classes("w-full p-4"):
        ui.label("NumPy ndarray").classes("text-sm font-medium text-slate-600 uppercase mb-2")
        with ui.row().classes("gap-6 flex-wrap mb-2"):
            info_field("Shape", str(arr.shape))
            info_field("Dtype", str(arr.dtype))
            info_field("Size", str(arr.size))
        flat = arr.flatten()[:100]
        preview = ", ".join(f"{v:.6g}" if hasattr(v, "__float__") else str(v) for v in flat)
        suffix = " …" if arr.size > 100 else ""
        ui.html(
            f'<pre class="text-xs whitespace-pre-wrap font-mono" '
            f'style="max-height:40vh;overflow:auto">{html.escape(preview + suffix)}</pre>',
            sanitize=False,
        )


def _render_text_viewer(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Could not read as text: {exc}").classes("text-red-600")
        return
    with ui.card().classes("w-full p-4"):
        ui.html(
            f'<pre class="text-xs whitespace-pre-wrap font-mono" '
            f'style="max-height:60vh;overflow:auto">{html.escape(text)}</pre>',
            sanitize=False,
        )
