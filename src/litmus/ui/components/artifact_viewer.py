"""View buttons + dialogs for measurement-output ref artifacts.

Walks each measurement's ``out_*`` columns; for any value that is a
``file://_ref/...`` or ``channel://...`` URI, renders a "View ..."
button. Clicking opens a NiceGUI dialog with an extension-driven
viewer:

* ``.npz`` (Waveform) — Python-side fetch of the JSON payload,
  rendered as an ECharts line plot.
* image / video / PDF / SVG / HTML / text / JSON — embed the API
  URL directly in ``<img>`` / ``<video>`` / ``<iframe>``. The
  ``/api/runs/{id}/ref`` endpoint serves the right ``Content-Type``
  (image/png, video/mp4, application/pdf, text/plain, …) so the
  browser renders inline without us doing any conversion.
* anything else — a download link.

The endpoint already deduplicates the heavy lifting; this module is
intentionally thin glue.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from nicegui import ui

from litmus.ui.shared.services import load_artifact_ref

# Map from URI extension to ("button label", "viewer kind").
#
# `.bin` is the catch-all the write path uses for any ``bytes`` payload
# whose original MIME type is unknown (operator screenshots, binary
# captures, …). The endpoint sniffs magic bytes server-side and serves
# the right ``Content-Type``; the UI just iframes the URL and lets the
# browser pick a renderer (``image/*`` → image, ``video/*`` → video,
# ``application/pdf`` → PDF reader, ``text/*`` → text). Same trick for
# explicit known extensions where the format is unambiguous.
_VIEWER_BY_EXT: dict[str, tuple[str, str]] = {
    ".npz": ("View Waveform", "waveform"),
    ".png": ("View Image", "image"),
    ".jpg": ("View Image", "image"),
    ".jpeg": ("View Image", "image"),
    ".gif": ("View Image", "image"),
    ".webp": ("View Image", "image"),
    ".svg": ("View Image", "image"),
    ".mp4": ("View Video", "video"),
    ".webm": ("View Video", "video"),
    ".pdf": ("View PDF", "pdf"),
    ".txt": ("View Text", "text"),
    ".log": ("View Text", "text"),
    ".html": ("View HTML", "html"),
    ".htm": ("View HTML", "html"),
    ".json": ("View Data", "json"),
    # ``.bin`` covers raw bytes that the server sniffs into the right
    # Content-Type at request time. We don't know the precise type until
    # the user clicks, so use a generic label and a universal iframe.
    ".bin": ("View Artifact", "iframe"),
}


def list_artifacts(measurement: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``[(output_key, uri)]`` for every ref-typed ``out_*`` column."""
    refs: list[tuple[str, str]] = []
    for key, value in measurement.items():
        if not key.startswith("out_") or not isinstance(value, str):
            continue
        # Item 1d: ``file://`` URIs come in two shapes — legacy
        # ``file://_ref/{filename}`` (per-parquet sidecar) and
        # FileStore-canonical ``file://{date}/{session_id}/{filename}``.
        # Both are file references; ``channel://`` is the live-channel
        # reference. Anything else is inline data.
        if value.startswith(("file://", "channel://")):
            refs.append((key.removeprefix("out_"), value))
    return refs


def _viewer_for_uri(uri: str) -> tuple[str, str]:
    """Return ``(label, kind)`` for *uri* based on its filename extension."""
    lowered = uri.lower().split("?", 1)[0]
    for ext, label_kind in _VIEWER_BY_EXT.items():
        if lowered.endswith(ext):
            return label_kind
    return "Download", "download"


def _api_url(run_id: str, uri: str) -> str:
    return f"/api/runs/{run_id}/ref?uri={urllib.parse.quote(uri, safe='')}"


def render_artifact_buttons(run_id: str, measurement: dict[str, Any]) -> None:
    """Render one View / Download button per artifact on a measurement row.

    Caller decides where in the layout to drop these (typically a row
    under the measurements table or inline alongside the value).
    """
    artifacts = list_artifacts(measurement)
    if not artifacts:
        return

    step = measurement.get("step_name", "")
    name = measurement.get("measurement_name", "")
    label_prefix = f"{step}.{name}" if step else name

    with ui.row().classes("items-center gap-2 flex-wrap"):
        ui.label(f"{label_prefix}:").classes("text-sm font-medium text-slate-700")
        for output_key, uri in artifacts:
            label, kind = _viewer_for_uri(uri)
            api_url = _api_url(run_id, uri)
            button_text = f"{label} ({output_key})"
            if kind == "download":
                ui.link(button_text, api_url, new_tab=True).classes(
                    "text-blue-600 hover:underline text-sm"
                )
                continue
            ui.button(
                button_text,
                on_click=_make_open_dialog(run_id, output_key, uri, api_url, kind),
            ).props("dense flat color=primary")


def _make_open_dialog(run_id: str, output_key: str, uri: str, api_url: str, kind: str):
    """Closure factory binding loop-iteration values to the click handler."""
    return lambda: _open_dialog(run_id, output_key, uri, api_url, kind)


def _open_dialog(run_id: str, output_key: str, uri: str, api_url: str, kind: str) -> None:
    """Pop a dialog containing the appropriate viewer for *kind*."""
    with ui.dialog() as dialog, ui.card().classes("p-4 w-[min(900px,90vw)]"):
        ui.label(output_key).classes("text-lg font-semibold mb-2")
        _render_viewer_body(run_id, uri, api_url, kind)
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _render_viewer_body(run_id: str, uri: str, api_url: str, kind: str) -> None:
    """Pick the viewer body for *kind*.

    Browser-rendered viewers (image / video / pdf / iframe) embed the
    HTTP API URL directly — the browser fetches and renders. JSON-shaped
    payloads (waveform / text / dict) skip the HTTP round-trip and call
    ``load_ref`` directly: the UI runs in the same Python process as
    the API, so we don't need to go through the network.
    """
    if kind == "waveform":
        _render_waveform(run_id, uri)
    elif kind == "image":
        ui.image(api_url).classes("w-full")
    elif kind == "video":
        ui.video(api_url, controls=True).classes("w-full")
    elif kind == "pdf":
        _iframe(api_url, sandbox=False, border=False)
    elif kind == "html":
        _iframe(api_url, sandbox=True, border=True)
    elif kind == "text":
        _render_text_or_json(run_id, uri, pretty=False)
    elif kind == "json":
        _render_text_or_json(run_id, uri, pretty=True)
    elif kind == "iframe":
        # Universal: server's magic-byte sniff sets Content-Type; the
        # browser picks a renderer (image / video / pdf / text / download).
        # No sandbox — Chrome blocks the PDF plugin in sandboxed iframes,
        # and the content comes from the user's own results dir (same-origin,
        # no untrusted scripts).
        _iframe(api_url, sandbox=False, border=True)
    else:
        ui.link("Download", api_url, new_tab=True)


def _iframe(api_url: str, *, sandbox: bool, border: bool) -> None:
    sandbox_attr = "sandbox " if sandbox else ""
    border_style = "1px solid #ccc" if border else "0"
    ui.html(
        f'<iframe src="{api_url}" {sandbox_attr}'
        f'style="width:100%;height:70vh;border:{border_style}"></iframe>',
        sanitize=False,
    )


def _render_waveform(run_id: str, uri: str) -> None:
    """Materialize the Waveform in-process and render an ECharts line plot."""
    try:
        wfm = load_artifact_ref(run_id, uri)
    except (FileNotFoundError, OSError, ValueError) as exc:
        ui.label(f"Failed to load waveform: {exc}").classes("text-red-600")
        return

    if not hasattr(wfm, "Y"):
        ui.label(f"Expected a Waveform, got {type(wfm).__name__}").classes("text-red-600")
        return

    Y = list(wfm.Y)
    t0 = float(getattr(wfm, "t0", 0.0))
    dt = float(getattr(wfm, "dt", 1.0)) or 1.0
    x_axis = [t0 + i * dt for i in range(len(Y))]
    attrs = getattr(wfm, "attrs", {}) or {}
    units = attrs.get("units")
    y_label = f"value ({units})" if units else "value"

    ui.echart(
        {
            "tooltip": {"trigger": "axis"},
            "xAxis": {
                "type": "category",
                "data": [f"{v:.4g}" for v in x_axis],
                "name": "time (s)",
                "nameLocation": "middle",
                "nameGap": 30,
            },
            "yAxis": {"type": "value", "name": y_label},
            "series": [
                {
                    "type": "line",
                    "data": Y,
                    "showSymbol": False,
                    "smooth": False,
                }
            ],
            "grid": {"left": 60, "right": 30, "top": 30, "bottom": 50},
        }
    ).classes("w-full h-96")


def _render_text_or_json(run_id: str, uri: str, *, pretty: bool) -> None:
    """Materialize text/JSON in-process and embed in a ``<pre>`` block."""
    try:
        payload = load_artifact_ref(run_id, uri)
    except (FileNotFoundError, OSError, ValueError) as exc:
        ui.label(f"Failed to load: {exc}").classes("text-red-600")
        return

    if pretty and isinstance(payload, dict):
        content = json.dumps(payload, indent=2, default=str)
    elif isinstance(payload, bytes):
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            content = repr(payload)
    elif isinstance(payload, dict):
        content = json.dumps(payload, default=str)
    else:
        content = str(payload)

    # NiceGUI's ui.html escapes nothing; build a safe pre block manually.
    import html as _html

    ui.html(
        f'<pre class="text-xs whitespace-pre-wrap break-all" '
        f'style="max-height:60vh;overflow:auto">{_html.escape(content)}</pre>',
        sanitize=False,
    )
