"""Channels browser — list every registered channel with descriptor + live preview."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_header,
    page_layout,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_channels_recent

# Sparkline cell geometry. Tight numbers — Quasar dense rows are
# ~30px tall; an 80×24 SVG fits without forcing the row to grow.
_SPARK_W = 80
_SPARK_H = 24
_SPARK_PAD = 2
# Live-update cadence. 2s is fast enough to feel "live" without
# hammering the daemon for every channel each second.
_REFRESH_SECONDS = 2.0
# Per-channel sample cap. Sparklines compress beautifully — 50
# points for an 80px-wide line is plenty of resolution.
_SAMPLES_PER_CHANNEL = 50


@ui.page("/channels")
def channels_page() -> None:
    """List all registered channels with click-through to detail.

    Each row shows a sparkline of recent samples and the latest value;
    the page polls ``/api/channels/_recent`` every two seconds so a
    live test session updates in place. Click a row to drill into the
    full chart at ``/channels/{id}``.

    The table is built once on first load; subsequent ticks mutate
    ``table.rows`` in place via ``table.update()`` so the cells
    re-render without tearing down the whole DOM (no flash).
    """
    create_layout("Channels")

    with page_layout():
        page_header("Channels")
        ui.label(
            "Streaming numeric / array signals captured during test runs — "
            "scope traces, PSU readback, sensor logs. Sparklines show the "
            "last 50 samples; values update live."
        ).classes("text-sm text-slate-500")

        count_label = ui.label("…").classes("text-sm text-slate-600")
        # Persistent containers — never cleared. We swap visibility
        # between the table and the empty-state card depending on the
        # registry being populated.
        table_holder = ui.column().classes("w-full flex-1 min-h-0 gap-0")
        empty_state = ui.column().classes("w-full")

        # ``state[table]`` is built lazily on the first tick that has
        # data, so the heavy ``data_table()`` setup only runs once;
        # later ticks mutate the existing row dicts in place. We
        # also remember the last-rendered HTML per cell and skip
        # ``table.update()`` entirely when nothing changed — that's
        # what was making the table flash every tick.
        state: dict[str, Any] = {"table": None, "fingerprint": ""}

        def refresh() -> None:
            try:
                payload = list_channels_recent(last_n=_SAMPLES_PER_CHANNEL)
            except (OSError, ValueError, RuntimeError):
                return
            channels = payload.get("channels") or {}
            count_label.text = f"{len(channels)} channel(s)"
            new_rows = [
                _row_for_channel(cid, descriptor) for cid, descriptor in sorted(channels.items())
            ]

            if not new_rows:
                if state["table"] is not None:
                    state["table"].rows.clear()
                    state["table"].update()
                    state["fingerprint"] = ""
                _show_empty_state(empty_state)
                return

            empty_state.clear()
            table = state["table"]
            if table is None:
                with table_holder:
                    state["table"] = _build_table(new_rows)
                state["fingerprint"] = _row_fingerprint(new_rows)
                return

            # Skip the round-trip entirely if nothing changed —
            # otherwise every tick would re-render the v-html cells
            # and flash. The fingerprint is just the joined cell
            # values; cheap to compute, robust to identical refreshes.
            new_fingerprint = _row_fingerprint(new_rows)
            if new_fingerprint == state["fingerprint"]:
                return
            state["fingerprint"] = new_fingerprint

            # Mutate the SAME row dicts in place where possible. q-table
            # keys rows by ``channel_id``, so updating a row's fields
            # without changing its identity preserves the cell DOM
            # nodes (Vue re-renders only the v-html spans whose value
            # actually changed).
            old_by_id = {r["channel_id"]: r for r in table.rows}
            preserved: list[dict[str, Any]] = []
            for new_row in new_rows:
                existing = old_by_id.get(new_row["channel_id"])
                if existing is None:
                    preserved.append(new_row)
                else:
                    existing.update(new_row)
                    preserved.append(existing)
            table.rows[:] = preserved
            table.update()

        refresh()

        # Subscribe to instrument lifecycle events so the table
        # refreshes when a channel writes a new sample. Replaces
        # the legacy ``ui.timer(2.0, refresh)`` polling pattern;
        # the in-place row mutation + fingerprint skip stay
        # exactly as they were.
        from litmus.data.data_dir import resolve_data_dir
        from litmus.data.event_store import EventStore
        from litmus.ui.shared.components import subscribe_with_refresh

        try:
            event_store = EventStore.get_shared(resolve_data_dir())
            subscribe_with_refresh(
                event_store,
                ["instrument.read", "instrument.set"],
                refresh,
            )
        except (OSError, RuntimeError):
            # Fall back to the polling timer if events are unavailable
            # (e.g. tests that don't run a daemon). Same cadence the
            # page used historically.
            ui.timer(_REFRESH_SECONDS, refresh)


def _row_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Cheap content hash to skip no-op refreshes."""
    return "|".join(f"{r['channel_id']}:{r['latest']}:{r['spark']}" for r in rows)


def _build_table(rows: list[dict[str, Any]]) -> ui.table:
    """Construct the channels table once. Later ticks mutate ``.rows``."""
    columns = [
        {"name": "channel_id", "label": "Channel ID", "field": "channel_id", "align": "left"},
        {"name": "latest", "label": "Latest", "field": "latest", "align": "right"},
        {"name": "spark", "label": "History", "field": "spark", "align": "left"},
        {"name": "data_type", "label": "Type", "field": "data_type", "align": "left"},
        {
            "name": "instrument_role",
            "label": "Instrument",
            "field": "instrument_role",
            "align": "left",
        },
        {
            "name": "last_updated",
            "label": "Last updated",
            "field": "last_updated",
            "align": "left",
        },
    ]
    table = data_table(
        columns=columns,
        rows=rows,
        row_key="channel_id",
        on_row_click=lambda r: ui.navigate.to(f"/channels/{r['channel_id']}"),
        time_columns=["last_updated"],
    )
    # ``latest`` and ``spark`` cells carry HTML strings (number with
    # units, inline SVG) that q-table renders as text by default.
    table.add_slot(
        "body-cell-latest",
        '<q-td :props="props"><span v-html="props.value"></span></q-td>',
    )
    table.add_slot(
        "body-cell-spark",
        '<q-td :props="props"><span v-html="props.value"></span></q-td>',
    )
    return table


def _show_empty_state(slot: ui.column) -> None:
    """Render the no-channels card. Idempotent — replaces existing content."""
    slot.clear()
    with slot, ui.card().classes("w-full"), ui.card_section():
        ui.label("No channels recorded yet.").classes("text-slate-500 italic")
        ui.label(
            "Channels appear once a test writes to ChannelStore "
            "(e.g. ``context.observe('scope', ndarray)`` or "
            "instrument observers)."
        ).classes("text-xs text-slate-400")


def _row_for_channel(channel_id: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    units = descriptor.get("units") or ""
    recent = descriptor.get("recent") or {}
    samples = recent.get("samples") or []
    latest_value = recent.get("latest")
    last_updated = recent.get("last_updated") or descriptor.get("first_seen")

    return {
        "channel_id": channel_id,
        "data_type": descriptor.get("data_type") or "",
        "instrument_role": descriptor.get("instrument_role") or "",
        "units": units,
        "last_updated": format_datetime(last_updated),
        "latest": _format_latest(latest_value, units),
        "spark": _sparkline_svg(samples),
    }


def _format_latest(value: Any, units: str) -> str:
    """Render the most recent sample as a small inline value+units."""
    if value is None:
        return '<span class="text-slate-400">—</span>'
    if isinstance(value, (int, float)):
        rendered = f"{value:.6g}"
    else:
        rendered = str(value)
    if units:
        return (
            f'<span class="font-mono">{rendered}</span>'
            f' <span class="text-xs text-slate-500">{units}</span>'
        )
    return f'<span class="font-mono">{rendered}</span>'


def _sparkline_svg(samples: list[Any]) -> str:
    """Build a tight inline SVG polyline for the recent sample series.

    Empty / single-sample series render as an em-dash so the cell is
    not visually empty; 2+ samples render as a 1-pixel polyline with
    no fill. Coordinate space is reset per row so the line always
    fills the cell — magnitude is reflected in the latest value, not
    the sparkline shape.
    """
    numeric: list[float] = []
    for entry in samples:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            v = entry[1]
        else:
            v = entry
        if isinstance(v, (int, float)):
            numeric.append(float(v))

    if len(numeric) < 2:
        return '<span class="text-slate-400">—</span>'

    lo = min(numeric)
    hi = max(numeric)
    span = hi - lo or 1.0
    n = len(numeric)
    inner_w = _SPARK_W - 2 * _SPARK_PAD
    inner_h = _SPARK_H - 2 * _SPARK_PAD

    points: list[str] = []
    for i, val in enumerate(numeric):
        x = _SPARK_PAD + (i / (n - 1)) * inner_w
        # SVG y grows downward; invert so peaks render at the top.
        y = _SPARK_PAD + inner_h - ((val - lo) / span) * inner_h
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)
    last_x, last_y = points[-1].split(",")
    return (
        f'<svg width="{_SPARK_W}" height="{_SPARK_H}" '
        f'viewBox="0 0 {_SPARK_W} {_SPARK_H}" '
        'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle">'
        f'<polyline points="{polyline}" fill="none" stroke="#3b82f6" '
        'stroke-width="1.25" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="1.75" fill="#3b82f6"/>'
        "</svg>"
    )
