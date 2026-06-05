"""Channels browser — list every registered channel with descriptor + live preview."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_header,
    page_layout,
    push_url_state,
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
def channels_page(
    name: str = "",
    data_type: str = "",
    instrument: str = "",
    since: str = "",
    until: str = "",
) -> None:
    """List all registered channels with click-through to detail.

    Each row shows a sparkline of recent samples and the latest value;
    the page polls ``/api/channels/_recent`` every two seconds so a
    live test session updates in place. Click a row to drill into the
    full chart at ``/channels/{id}``.

    Filter state is mirrored into the URL via ``history.replaceState``
    so views are bookmarkable and shareable. Same pattern as
    ``/events`` / ``/files``.

    The table is built once on first load; subsequent ticks mutate
    ``table.rows`` in place via ``table.update()`` so the cells
    re-render without tearing down the whole DOM (no flash).
    """
    create_layout("Channels")

    filters = _Filters()

    with page_layout():
        page_header("Channels")
        ui.label(
            "Streaming numeric / array signals captured during test runs — "
            "scope traces, PSU readback, sensor logs. Sparklines show the "
            "last 50 samples; values update live."
        ).classes("text-sm text-slate-500")

        # Filter card renders FIRST (above table) per the operator-UI
        # consistency rule. ``Type`` options are seeded with "(any)";
        # the refresh callback rebuilds the dropdown from observed
        # values once data has been walked.
        with ui.card().classes("w-full").props('data-testid="channels-filters"'):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.name_input = ui.input(
                    "Channel ID contains",
                    value=name,
                    on_change=lambda _: _refresh_render(),
                ).classes("w-72")
                filters.type_select = ui.select(
                    {"": "(any)"},
                    value="",
                    label="Type",
                    with_input=True,
                    on_change=lambda _: _refresh_render(),
                ).classes("w-56")
                filters.instrument_select = ui.select(
                    {"": "(any)"},
                    value="",
                    label="Instrument",
                    with_input=True,
                    on_change=lambda _: _refresh_render(),
                ).classes("w-48")
                filters.since_input = ui.input(
                    "Since (ISO)",
                    value=since,
                    on_change=lambda _: _refresh_render(),
                ).classes("w-56")
                filters.until_input = ui.input(
                    "Until (ISO)",
                    value=until,
                    on_change=lambda _: _refresh_render(),
                ).classes("w-56")
                ui.button("Refresh", icon="refresh", on_click=lambda: refresh()).props(
                    "color=primary"
                )

        count_label = ui.label("…").classes("text-sm text-slate-600")
        # Persistent containers — never cleared. We swap visibility
        # between the table and the empty-state card depending on the
        # registry being populated. data-testid attributes are stable
        # selectors for scripts/regenerate-ui-screenshots.py.
        table_holder = (
            ui.column().classes("w-full flex-1 min-h-0 gap-0").props('data-testid="channels-table"')
        )
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

            # Re-seed the Type + Instrument dropdowns from observed
            # descriptor values so newly-seen values become selectable.
            # The Instrument dropdown carries a "(none)" sentinel for
            # channels with no instrument_role (derived / computed /
            # user-streamed channels). Preserve current selections when
            # still valid.
            type_options = {"": "(any)"}
            instrument_options: dict[str, str] = {"": "(any)"}
            has_unassigned = False
            for descriptor in channels.values():
                dt = descriptor.get("data_type") or ""
                if dt and dt not in type_options:
                    type_options[dt] = dt
                role = descriptor.get("instrument_role") or ""
                if role:
                    instrument_options.setdefault(role, role)
                else:
                    has_unassigned = True
            if has_unassigned:
                instrument_options[_INSTRUMENT_NONE_SENTINEL] = "(none)"

            current_type = filters.type_select.value or ""
            filters.type_select.options = type_options
            if current_type not in type_options:
                filters.type_select.value = ""
            filters.type_select.update()

            current_inst = filters.instrument_select.value or ""
            filters.instrument_select.options = instrument_options
            if current_inst not in instrument_options:
                filters.instrument_select.value = ""
            filters.instrument_select.update()

            state["all_rows"] = [
                _row_for_channel(cid, descriptor) for cid, descriptor in sorted(channels.items())
            ]
            state["total"] = len(channels)
            _render_filtered()

        def _render_filtered() -> None:
            """Apply current filter state to ``state['all_rows']`` and render."""
            push_url_state(
                "/channels",
                {
                    "name": filters.name(),
                    "data_type": filters.data_type(),
                    "instrument": filters.instrument(),
                    "since": filters.since(),
                    "until": filters.until(),
                },
            )
            all_rows: list[dict[str, Any]] = state.get("all_rows") or []
            total = state.get("total", len(all_rows))
            filtered = _apply_filters(
                all_rows,
                name=filters.name(),
                data_type=filters.data_type(),
                instrument=filters.instrument(),
                since=filters.since(),
                until=filters.until(),
            )
            count_label.text = (
                f"{len(filtered)} of {total} channel(s)"
                if len(filtered) != total
                else f"{total} channel(s)"
            )

            if not filtered:
                if state["table"] is not None:
                    state["table"].rows.clear()
                    state["table"].update()
                    state["fingerprint"] = ""
                _show_empty_state(empty_state, has_data=bool(all_rows))
                return

            empty_state.clear()
            table = state["table"]
            if table is None:
                with table_holder:
                    state["table"] = _build_table(filtered)
                state["fingerprint"] = _row_fingerprint(filtered)
                return

            # Skip the round-trip entirely if nothing changed —
            # otherwise every tick would re-render the v-html cells
            # and flash. Fingerprint includes filter outputs so a
            # filter change forces a re-render even at constant data.
            new_fingerprint = _row_fingerprint(filtered)
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
            for new_row in filtered:
                existing = old_by_id.get(new_row["channel_id"])
                if existing is None:
                    preserved.append(new_row)
                else:
                    existing.update(new_row)
                    preserved.append(existing)
            table.rows[:] = preserved
            table.update()

        def _refresh_render() -> None:
            """Re-render against the in-memory snapshot when a filter changes."""
            _render_filtered()

        # Apply URL-driven initial filter values before the first walk
        # so the count + table reflect the deep-linked state.
        if data_type:
            filters.type_select.options = {"": "(any)", data_type: data_type}
            filters.type_select.value = data_type
            filters.type_select.update()
        if instrument:
            label = "(none)" if instrument == _INSTRUMENT_NONE_SENTINEL else instrument
            filters.instrument_select.options = {"": "(any)", instrument: label}
            filters.instrument_select.value = instrument
            filters.instrument_select.update()
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


class _Filters:
    """Lazy filter-value accessors so callbacks read the live widget state.

    Same shape as the ``_Filters`` in ``/events`` and ``/files`` — class
    attributes set after the widgets are constructed; getters return the
    stripped value (empty string = "(any)" / no filter).
    """

    name_input: ui.input
    type_select: ui.select
    instrument_select: ui.select
    since_input: ui.input
    until_input: ui.input

    def name(self) -> str:
        return (self.name_input.value or "").strip()

    def data_type(self) -> str:
        return (str(self.type_select.value) if self.type_select.value else "").strip()

    def instrument(self) -> str:
        return (str(self.instrument_select.value) if self.instrument_select.value else "").strip()

    def since(self) -> str:
        return (self.since_input.value or "").strip()

    def until(self) -> str:
        return (self.until_input.value or "").strip()


# Sentinel for the Instrument dropdown's "no instrument_role set" entry.
# Channel descriptors carry ``instrument_role: str = ""`` so the empty
# string is genuinely "unassigned" — distinct from "(any)" which means
# "don't filter on this dimension."
_INSTRUMENT_NONE_SENTINEL = "__none__"


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    name: str,
    data_type: str,
    instrument: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Apply the channel-list filters to ``rows``. Empty strings = wildcards."""
    name_lower = name.lower()
    out: list[dict[str, Any]] = []
    for r in rows:
        if name_lower and name_lower not in r["channel_id"].lower():
            continue
        if data_type and r["data_type"] != data_type:
            continue
        if instrument:
            row_instr = r["instrument_role"]
            if instrument == _INSTRUMENT_NONE_SENTINEL:
                if row_instr:
                    continue
            elif row_instr != instrument:
                continue
        if since or until:
            last = r["last_updated"] or ""
            if since and last < since:
                continue
            if until and last > until:
                continue
        out.append(r)
    return out


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


def _show_empty_state(slot: ui.column, *, has_data: bool = False) -> None:
    """Render the empty-state card. Distinguishes "no data" from "filtered out"."""
    slot.clear()
    with slot, ui.card().classes("w-full"), ui.card_section():
        if has_data:
            ui.label("No channels match the current filters.").classes("text-slate-500 italic")
            ui.label("Clear the filters above to see all channels.").classes(
                "text-xs text-slate-400"
            )
            return
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
