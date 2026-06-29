"""Channel detail page — descriptor + filtered chart / data tabs."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import run, ui

from litmus.data.channels.models import ChannelSample
from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    LiveBadge,
    UtcDateHandle,
    data_table,
    format_datetime,
    info_field,
    lookup_session_label,
    push_url_state,
    session_filter_banner,
    utc_date_input,
)
from litmus.ui.shared.event_binding import ui_channel_data, ui_subscribe
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_channels, query_channel
from litmus.ui.shared.timestamps import format_time_short

logger = logging.getLogger(__name__)


@ui.page("/channels/{channel_id}")
def channel_detail_page(
    channel_id: str,
    session_id: str = "",
    since: str = "",
    until: str = "",
    x_mode: str = "time",
) -> None:
    """Single-channel browser with a chart and a raw-data tab.

    Filter state is mirrored into the URL via ``history.replaceState``
    so a deep link reopens the same view. Same pattern as
    ``/metrics`` and ``/explore``.
    """
    create_layout(f"Channel {channel_id}")

    descriptor = list_channels().get("channels", {}).get(channel_id)

    with ui.column().classes("w-full p-6 gap-4"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.label(channel_id).classes("text-2xl font-semibold")
                # Page-scoped so it survives chart rebuilds on every filter
                # change. Driven by lifecycle events + sample activity below.
                live_badge = LiveBadge()
            ui.button(
                "Back",
                icon="arrow_back",
                on_click=lambda: ui.navigate.to("/channels"),
            ).props("flat")

        if descriptor is None:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label(f"Channel {channel_id!r} is not registered.").classes("text-slate-600")
                ui.link("← Back to Channels", "/channels").classes("text-blue-600 hover:underline")
            return

        _render_descriptor_card(descriptor)

        # Session scoping is URL-only — no widget. The param is set
        # by deep-links from pages that already know the session
        # (e.g. /results/{run_id} → /channels/{id}?session_id=...).
        # The banner is the only affordance to clear; there is no
        # add/change picker. UUIDs never appear in the UI.
        session_filter_banner(session_id, clear_path=f"/channels/{channel_id}")

        filters = _Filters()

        # Forward-declared so refresh() can guard against early fires.
        # bind_value on date pickers propagates the initial input value
        # synchronously during construction, which triggers on_change
        # before chart_card / data_card are built below.
        chart_card: ui.card | None = None
        data_card: ui.card | None = None
        # X-axis mode for scalar plots: "time" (received timestamp) or
        # "offset" (per-session sample offset, so multiple sessions overlay
        # for shape comparison). Mutable cell — the toggle updates it.
        x_mode_state = ["offset" if x_mode == "offset" else "time"]
        # ── Live infrastructure: one holder, one renderer ───────────────
        # The live-UI rule (docs/_internal/explorations/live-ui-pattern.md):
        # the subscription callbacks below write only plain Python (the
        # holder) and the thread-safe badge setters; the ui.timer is the
        # sole code that mutates the chart, on the UI loop. refresh()
        # rebuilds the chart on every filter change, so the timer targets
        # whichever chart is current via chart_ref, and ``drawn_through``
        # (set to query time by refresh) keeps the live tail from redrawing
        # samples already in the history slice.
        live_samples: deque[ChannelSample] = deque(maxlen=2000)
        chart_ref: list[ui.echart | None] = [None]
        drawn_through: list[datetime | None] = [None]

        def _on_live(sample: ChannelSample) -> None:
            live_samples.append(sample)
            live_badge.ping()

        ui_channel_data(channel_id).subscribe(_on_live)

        # Lifecycle → badge, on a separate path from samples (so a started
        # channel reads live even if the sample relay is quiet). Best-effort:
        # if the events daemon is down the badge falls back to activity alone.
        try:
            event_store = EventStore.get_shared(resolve_data_dir())

            # channel.started/closed replay as two independent streams, so an
            # older session's close must not mark a channel a newer session
            # has reopened. Compare the latest start vs latest close timestamp
            # (ISO strings sort chronologically) to decide the current state.
            last_started = [""]
            last_closed = [""]

            def _apply_lifecycle() -> None:
                if not last_started[0]:
                    return
                if last_closed[0] >= last_started[0]:
                    live_badge.mark_closed()
                else:
                    live_badge.mark_started()

            def _on_started(evt: dict) -> None:
                if evt.get("channel_id") != channel_id:
                    return
                ts = str(evt.get("received_at") or evt.get("occurred_at") or "")
                last_started[0] = max(last_started[0], ts)
                _apply_lifecycle()

            def _on_closed(evt: dict) -> None:
                if evt.get("channel_id") != channel_id:
                    return
                ts = str(evt.get("received_at") or evt.get("occurred_at") or "")
                last_closed[0] = max(last_closed[0], ts)
                _apply_lifecycle()

            ui_subscribe(event_store, _on_started, event_type="channel.started")
            ui_subscribe(event_store, _on_closed, event_type="channel.ended")
        except (OSError, RuntimeError) as exc:
            logger.debug("Channel lifecycle badge updates unavailable: %s", exc)

        def _redraw_live() -> None:
            chart = chart_ref[0]
            if chart is None or not live_samples:
                return
            cutoff = drawn_through[0]
            fresh = [s for s in live_samples if cutoff is None or s.received_at > cutoff]
            if not fresh:
                return
            drawn_through[0] = fresh[-1].received_at
            _append_live_samples(chart, fresh)

        ui.timer(0.25, _redraw_live)

        async def refresh() -> None:
            if chart_card is None or data_card is None:
                return
            push_url_state(
                f"/channels/{channel_id}",
                {
                    # session_id is URL-only — preserved across refresh
                    # via the page-level param, not the filter widgets.
                    "session_id": session_id,
                    "since": filters.since(),
                    "until": filters.until(),
                    "x_mode": x_mode_state[0],
                },
            )
            # io_bound: the channel query is a blocking gRPC call — run it
            # off the event loop so filter changes never freeze the page.
            payload = await run.io_bound(
                query_channel,
                channel_id,
                session_id=session_id or None,
                since=filters.since() or None,
                until=filters.until() or None,
                max_points=1000,  # LTTB decimation for chart-friendly response
            )
            data = payload.get("data") or []
            chart_ref[0] = _render_chart(
                chart_card,
                channel_id,
                data,
                descriptor,
                x_mode=x_mode_state[0],
            )
            # History now covers up to ~now; the live tail draws only newer.
            drawn_through[0] = datetime.now(UTC)
            _render_data_table(data_card, data)

        # Filters first (above chart + data) so the page reads top-down.
        with ui.card().classes("w-full"):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.since_handle = utc_date_input(
                    "Since",
                    value=since or None,
                    on_change=lambda _: refresh(),
                    classes="w-44",
                )
                filters.until_handle = utc_date_input(
                    "Until",
                    value=until or None,
                    on_change=lambda _: refresh(),
                    classes="w-44",
                )

                ui.button(
                    "Clear", icon="clear", on_click=lambda: _clear_filters(filters, refresh)
                ).props("flat dense")

                # X-axis mode: Time, or per-session Offset (sessions overlay
                # for shape comparison). Applies to scalar and array channels
                # alike — an array session's captures are appended in offset
                # order, so it overlays just like a scalar.
                async def _on_x_mode(e: Any) -> None:
                    x_mode_state[0] = "offset" if e.value == "offset" else "time"
                    await refresh()

                ui.toggle(
                    {"time": "Time", "offset": "Offset"},
                    value=x_mode_state[0],
                    on_change=_on_x_mode,
                ).props("dense").classes("ml-auto").tooltip(
                    "X-axis: received time, or per-session sample offset "
                    "(multiple sessions overlay for shape comparison)"
                )

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        chart_card = ui.card().classes("w-full").props('data-testid="channel-chart"')
        data_card = ui.card().classes("w-full").props('data-testid="channel-data"')

        # Schedule the first (async) render on the loop — the page function
        # itself is sync, so it can't await refresh() directly.
        ui.timer(0, refresh, once=True)


async def _clear_filters(filters: _Filters, refresh: Callable[[], Awaitable[None]]) -> None:
    """Reset the date-window filters to defaults and re-render.

    The ``?session_id=`` URL param is intentionally NOT cleared here —
    its only affordance is the session banner's Clear button (which
    navigates to a URL without the param).
    """
    filters.since_handle.clear()
    filters.until_handle.clear()
    await refresh()


def _render_descriptor_card(descriptor: dict[str, Any]) -> None:
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.grid(columns=4).classes("gap-4"):
                info_field("Data type", descriptor.get("value_type") or "")
                info_field("Instrument role", descriptor.get("instrument_role") or "")
                info_field("Resource", descriptor.get("resource") or "")
                info_field("Units", descriptor.get("unit") or "")
                info_field("First seen", format_datetime(descriptor.get("first_seen")))
                attrs = descriptor.get("attributes") or {}
                if attrs:
                    info_field("Attributes", ", ".join(f"{k}={v}" for k, v in attrs.items()))


def _render_chart(
    card: ui.card,
    channel_id: str,
    rows: list[dict[str, Any]],
    descriptor: dict[str, Any],
    *,
    x_mode: str = "time",
) -> ui.echart | None:
    """ECharts line plot of scalar values; first-sample plot for arrays.

    Rebuilds the chart card from the (already filtered + decimated)
    ``rows``. Returns the chart object so the page's live timer can append
    the live tail to it (``None`` when there's no history yet — the timer
    skips drawing until a refresh builds a chart). The live tail itself is
    wired once at the page level, not here.

    Scalar channels plot one (received_at, value) point per row; array
    channels (Waveform-shaped writes) plot the most recent capture as a
    single trace.
    """
    card.clear()
    with card:
        with ui.card_section():
            ui.label("Chart").classes("font-semibold")
            ui.label("LTTB-decimated to ≤1,000 points; faithful peaks/valleys.").classes(
                "text-xs text-slate-500"
            )

        if not rows:
            with ui.card_section():
                ui.label("No samples for the current filters.").classes("text-slate-500 italic")
            return None

        x_values = [_axis_tick(r.get("received_at")) for r in rows]
        unit = descriptor.get("unit") or ""
        y_label = f"value ({unit})" if unit else "value"

        # Array-of-items capture: rows carry a ``value`` list (e.g.
        # scope.waveform's per-trigger waveform). Plot the most recent
        # capture as a single series. We detect array rows from the
        # row shape rather than the registry's declared ``value_type``,
        # since channels written by older sessions can come back
        # classified as ``"struct"`` while still being array-shaped.
        # Note: scalar rows also have a ``value`` column post-C3a-pre,
        # but it's a scalar — the ``isinstance(..., list)`` check
        # disambiguates.
        last_row = rows[-1]
        last_values = last_row.get("value")
        y_axis_scale = False
        if isinstance(last_values, list) and last_values:
            interval = last_row.get("sample_interval") or 0.0
            if interval:
                # Sample interval recorded → label X as time using the
                # most readable unit for the capture window.
                x_values, x_axis_label = _format_time_axis(interval, len(last_values))
            else:
                x_values = [str(i) for i in range(len(last_values))]
                x_axis_label = "sample"
            y_data: list[Any] = list(last_values)
            # Waveform values can occupy a tiny fraction of their
            # absolute range (a 24mV ripple on a 3.3V rail). Force
            # ECharts to ignore the 0 baseline so the variation is
            # actually visible.
            y_axis_scale = True
        else:
            y_data = [_extract_scalar(r) for r in rows]
            x_axis_label = "received"

        # Offset mode overlays one trace per session on a value axis, for
        # scalar and array channels alike (an array session's captures are
        # appended). Show the legend whenever >1 session is present so each
        # colored trace is identifiable. Time mode keeps the scalar-only
        # multi-session legend; array time mode (eye-diagram) stays legend-off.
        if x_mode == "offset":
            show_legend = len({str(r.get("session_id") or "") for r in rows}) > 1
            x_axis: dict[str, Any] = {
                "type": "value",
                "name": "offset",
                "nameLocation": "middle",
                "nameGap": 30,
                "minInterval": 1,  # offsets are integers — no fractional ticks
            }
        else:
            scalar_session_ids = {
                str(r.get("session_id") or "") for r in rows if not isinstance(r.get("value"), list)
            }
            show_legend = (not isinstance(last_values, list) or not last_values) and len(
                scalar_session_ids
            ) > 1
            x_axis = {
                "type": "category",
                "data": x_values,
                "name": x_axis_label,
                "nameLocation": "middle",
                "nameGap": 30,
            }

        # Legend lives UNDER the plot (below the zoom slider) so it never
        # collides with the top-right toolbox; the grid bottom grows for it.
        grid_bottom = 112 if show_legend else 70
        slider_bottom = 52 if show_legend else 18

        chart = ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": (
                    {"show": True, "type": "scroll", "bottom": 8}
                    if show_legend
                    else {"show": False}
                ),
                "title": {
                    "text": channel_id,
                    "textStyle": {"fontSize": 14, "fontWeight": "normal"},
                    "left": "center",
                    "top": 8,
                },
                # Built-in toolbox: drag-to-zoom into any region, one-
                # click restore (fit to full data range), save image.
                "toolbox": {
                    "right": 16,
                    "top": 8,
                    "itemSize": 14,
                    "feature": {
                        "dataZoom": {
                            "title": {"zoom": "Zoom", "back": "Restore zoom"},
                            "yAxisIndex": "all",
                        },
                        "restore": {"title": "Fit to data"},
                        "saveAsImage": {"title": "Save", "name": channel_id},
                    },
                },
                # Bottom slider too — useful for long scalar series.
                # ``filterMode: 'none'`` keeps Y autoscale tied to the
                # full range, not the visible window, so zooming in
                # doesn't squash the line into a flat band.
                "dataZoom": [
                    {"type": "inside", "filterMode": "none"},
                    {"type": "slider", "filterMode": "none", "height": 16, "bottom": slider_bottom},
                ],
                "xAxis": x_axis,
                "yAxis": {"type": "value", "name": y_label, "scale": y_axis_scale},
                "series": _build_chart_series(rows, last_values, y_data, x_mode),
                "grid": {"left": 60, "right": 30, "top": 40, "bottom": grid_bottom},
            }
        ).classes("w-full h-96 px-4 pb-4")
        # ECharts inside a Quasar card_section sometimes initializes with
        # width=0 because the parent's flex layout hasn't sized yet. Force
        # a resize after the page is interactive.
        chart_id = chart.id
        ui.timer(
            0.1,
            lambda: ui.run_javascript(
                f"const el = getElement({chart_id}); if (el && el.chart) el.chart.resize();"
            ),
            once=True,
        )

    return chart


def _append_live_samples(chart: ui.echart, samples: list[ChannelSample]) -> None:
    """Append live ``samples`` to ``chart`` (scalar) or replace its trace (array).

    The sole chart-mutating path for live data — called only from the
    page's ``ui.timer`` (on the UI loop), never from a delivery thread.

    Scalar channels append one (received_at, value) point per sample to the
    trailing series. Array channels (Waveform-shaped writes) replace the
    trace with the most recent capture. Multi-session scalar views (more
    than one series) skip the live tail: ChannelSample carries no session_id
    on the live path, so a point can't be routed to the right per-session
    series — the operator gets the new sample on the next Refresh instead.
    """
    opts = chart.options
    series = opts.get("series") or []
    touched = False
    for sample in samples:
        value = sample.value
        if isinstance(value, list) and value:
            interval = sample.sample_interval or 0.0
            if interval:
                x_axis_data, _ = _format_time_axis(interval, len(value))
            else:
                x_axis_data = [str(i) for i in range(len(value))]
            opts["xAxis"]["data"] = x_axis_data
            opts["series"] = [
                {
                    "name": "live",
                    "type": "line",
                    "data": list(value),
                    "showSymbol": False,
                    "smooth": False,
                    "lineStyle": {"width": 1.5, "color": "#2563eb"},
                }
            ]
            series = opts["series"]
            touched = True
        else:
            if not series or len(series) > 1:
                continue
            primary = series[0]
            data_list = list(primary.get("data") or [])
            data_list.append(value)
            primary["data"] = data_list
            x_list = list((opts.get("xAxis") or {}).get("data") or [])
            x_list.append(format_time_short(sample.received_at.isoformat()))
            opts["xAxis"]["data"] = x_list
            touched = True
    if touched:
        chart.update()


def _render_data_table(card: ui.card, rows: list[dict[str, Any]]) -> None:
    """Raw rows table — received / value / source / session."""
    card.clear()
    with card:
        with ui.card_section():
            ui.label("Data").classes("font-semibold")
            ui.label(f"{len(rows)} sample(s) — most recent first.").classes(
                "text-xs text-slate-500"
            )

        if not rows:
            return

        columns = [
            {
                "name": "received_at",
                "label": "Received",
                "field": "received_at",
                "align": "left",
            },
            {
                "name": "value",
                "label": "Value",
                "field": "value",
                "align": "right",
            },
            {
                "name": "source",
                "label": "Source",
                "field": "source",
                "align": "left",
            },
            {
                "name": "session",
                "label": "Session",
                "field": "session",
                "align": "left",
            },
        ]

        # Session column shows the operator-readable label (UUT serial
        # + start time) rather than the raw UUID prefix. Matches the
        # no-synthetic-IDs-in-operator-UI rule and the chart legend
        # rendered by item 18b.
        def _session_cell(r: dict[str, Any]) -> str:
            sid = str(r.get("session_id") or "")
            if not sid:
                return ""
            label, _found = lookup_session_label(sid)
            return label

        table_rows = [
            {
                "id": str(idx),
                "received_at": format_datetime(r.get("received_at")),
                "value": _value_summary(r),
                "source": r.get("source_method") or "",
                "session": _session_cell(r),
            }
            for idx, r in enumerate(reversed(rows))
        ]
        data_table(
            columns=columns,
            rows=table_rows,
            row_key="id",
            time_columns=["received_at"],
        )


class _Filters:
    """Lazy-read filter values from the UTC date handles.

    ``session_id`` is intentionally NOT here — it's URL-only and
    flows through the page-level ``session`` parameter, never via
    a filter widget (see :func:`session_filter_banner`).

    ``.since_handle`` / ``.until_handle`` are :class:`UtcDateHandle` instances
    whose ``.value`` is always UTC or ``None`` — the JS layer converts before
    Python sees any value.
    """

    since_handle: UtcDateHandle
    until_handle: UtcDateHandle

    def since(self) -> str:
        return (self.since_handle.value or "").strip()

    def until(self) -> str:
        return (self.until_handle.value or "").strip()


def _extract_scalar(row: dict[str, Any]) -> Any:
    """Pick the numeric value from a scalar-channel row."""
    for key in ("value", "y", "reading"):
        if key in row and row[key] is not None:
            return row[key]
    # Fallback: first non-metadata field
    for k, v in row.items():
        if k not in ("received_at", "sampled_at", "source_method", "session_id") and v is not None:
            return v
    return None


def _value_summary(row: dict[str, Any]) -> str:
    """Render the row's value compactly (full numbers; truncate arrays).

    Post-C3a-pre: both scalar and array rows have a ``value`` column;
    array rows' ``value`` is a list. Disambiguate by type.
    """
    payload = row.get("value")
    if isinstance(payload, list):
        head = ", ".join(f"{v:.4g}" if isinstance(v, (int, float)) else str(v) for v in payload[:4])
        more = "" if len(payload) <= 4 else f" … +{len(payload) - 4} more"
        return f"[{head}{more}]"
    val = _extract_scalar(row)
    if isinstance(val, (int, float)):
        return f"{val:.6g}"
    return "" if val is None else str(val)


def _format_time_axis(interval: float, n: int) -> tuple[list[str], str]:
    """Build X-axis tick labels + axis title for a sample-interval capture.

    Picks the most readable unit for the capture window so an
    operator reading the chart sees ``200`` µs instead of
    ``0.0002`` s.
    """
    span = interval * (n - 1) if n > 1 else interval
    if span >= 1:
        unit, scale = "s", 1.0
    elif span >= 1e-3:
        unit, scale = "ms", 1e3
    elif span >= 1e-6:
        unit, scale = "µs", 1e6
    else:
        unit, scale = "ns", 1e9
    return (
        [f"{i * interval * scale:.4g}" for i in range(n)],
        f"time ({unit})",
    )


def _build_chart_series(
    rows: list[dict[str, Any]],
    last_values: list[Any] | None,
    y_data: list[Any],
    x_mode: str = "time",
) -> list[dict[str, Any]]:
    """Build the ECharts ``series`` array.

    For waveform channels with multiple captures, every capture gets
    its own translucent line so the operator sees an eye-diagram-
    style overlay (newer captures land on top of older). The latest
    capture stays opaque so it's identifiable. Capped at 50 captures
    to keep the render cheap.

    For scalar channels with more than one session represented in
    the rows, build one series per session so each session's points
    get a distinct color and a legend entry (operator-readable
    label via :func:`lookup_session_label`). A scalar channel with
    only one session keeps the existing single-line shape.
    """
    # Offset overlay treats array channels like scalars: append each
    # session's captures into one trace (see _build_offset_array_series).
    if x_mode == "offset" and isinstance(last_values, list) and last_values:
        return _build_offset_array_series(rows)

    # Scalar channel (or empty array): plot the scalar series from y_data.
    # ``last_values`` for scalar channels is a single float — guard with
    # isinstance before treating it as iterable.
    if not isinstance(last_values, list) or not last_values:
        return _build_scalar_series(rows, y_data, x_mode)

    waveform_rows = [r for r in rows if isinstance(r.get("value"), list) and r.get("value")]
    if len(waveform_rows) <= 1:
        return [
            {
                "type": "line",
                "data": list(last_values),
                "showSymbol": False,
                "smooth": False,
            }
        ]

    return _build_waveform_series(waveform_rows)


def _build_scalar_series(
    rows: list[dict[str, Any]],
    y_data: list[Any],
    x_mode: str = "time",
) -> list[dict[str, Any]]:
    """Build the scalar-channel series array.

    Groups by ``session_id`` so multiple runs' points get distinct
    colors. When only one session is present (the common case with a
    session-scoped deep-link, or a channel that has only ever been
    written from one session), returns a single unnamed series matching
    the prior behaviour. When two or more sessions are present, returns
    one named series per session (operator-readable label) so the
    legend identifies which color is which run.

    ``x_mode`` controls the X placement. ``"time"`` plots each point at
    its ``received_at`` against a shared time axis (sessions sit
    side-by-side along the timeline). ``"offset"`` plots each session's
    values against their per-session ``offset`` as ``[offset, value]``
    pairs on a value axis, so sessions overlay aligned at offset 0.
    """
    session_ids = {str(r.get("session_id") or "") for r in rows}
    if len(session_ids) <= 1:
        data = (
            [[r.get("sample_offset"), _extract_scalar(r)] for r in rows]
            if x_mode == "offset"
            else y_data
        )
        return [
            {
                "type": "line",
                "data": data,
                "showSymbol": False,
                "smooth": False,
            }
        ]

    # Multi-session: bucket rows by session_id, one series per bucket.
    # Order preserved by the row sequence (server-side sorted by
    # received_at).
    by_session: dict[str, list[Any]] = {}
    for r in rows:
        sid = str(r.get("session_id") or "")
        value = _extract_scalar(r)
        if value is None:
            continue
        # time: [tick, value] pairs on the shared category axis (a tick
        # missing from a session renders as a gap, not a zero). offset:
        # [offset, value] pairs on the value axis, sessions aligned at 0.
        if x_mode == "offset":
            point = [r.get("sample_offset"), value]
        else:
            point = [_axis_tick(r.get("received_at")), value]
        by_session.setdefault(sid, []).append(point)

    series: list[dict[str, Any]] = []
    for sid, points in by_session.items():
        label, _found = lookup_session_label(sid) if sid else ("(no session)", True)
        series.append(
            {
                "type": "line",
                "name": label,
                "data": points,
                "showSymbol": False,
                "smooth": False,
            }
        )
    return series


def _build_offset_array_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Offset overlay for array channels: append each session's captures.

    A session's captures (each an array value at a successive offset) are
    concatenated in offset order into one trace, plotted as ``[position,
    value]`` pairs against a running sample position. One series per session,
    overlaid and aligned at 0 — the array analog of the scalar offset overlay.
    """
    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if isinstance(r.get("value"), list) and r.get("value"):
            by_session.setdefault(str(r.get("session_id") or ""), []).append(r)

    series: list[dict[str, Any]] = []
    for sid, srows in by_session.items():
        srows.sort(key=lambda r: r.get("sample_offset") or 0)
        points: list[list[Any]] = []
        pos = 0
        for r in srows:
            for v in r["value"]:
                points.append([pos, v])
                pos += 1
        label, _found = lookup_session_label(sid) if sid else ("(no session)", True)
        series.append(
            {
                "type": "line",
                "name": label,
                "data": points,
                "showSymbol": False,
                "smooth": False,
            }
        )
    return series


# Captures shown as full per-trace lines fading from bold blue (newest)
# to faint gray (Nth oldest). Older captures collapse into a single
# min/max envelope so 100s of historic captures don't make the chart
# unreadable.
_RECENT_WINDOW = 10


def _build_waveform_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-capture lines (recent) + min/max envelope (older)."""
    # ``r.get("value")`` may be a scalar (for scalar channels) or a list
    # (for array channels) — guard with isinstance to skip non-list rows.
    captures = [list(r.get("value") or []) for r in rows if isinstance(r.get("value"), list)]
    captures = [c for c in captures if c]
    if not captures:
        return []
    if len(captures) == 1:
        return [
            {
                "type": "line",
                "name": "Latest",
                "data": captures[0],
                "showSymbol": False,
                "smooth": False,
                "lineStyle": {"width": 1.5, "opacity": 1.0, "color": "#3b82f6"},
                "tooltip": {"show": True},
            }
        ]

    recent = captures[-_RECENT_WINDOW:]
    old = captures[:-_RECENT_WINDOW]

    series: list[dict[str, Any]] = []
    if old:
        series.extend(_envelope_series(old))
    series.extend(_recent_gradient_series(recent))
    return series


def _envelope_series(old: list[list[float]]) -> list[dict[str, Any]]:
    """Min/max band across all ``old`` captures, rendered as one filled area.

    ECharts trick: stack two series, the lower being the floor and
    the second being the *delta* (max − min) with ``areaStyle``. The
    visible area is exactly the band between min and max. Both
    series have invisible lines so only the fill shows.
    """
    width = min(len(c) for c in old)
    if width == 0:
        return []
    lo: list[float] = []
    hi: list[float] = []
    for i in range(width):
        col = [c[i] for c in old]
        lo.append(min(col))
        hi.append(max(col))
    delta = [h - lo_i for h, lo_i in zip(hi, lo, strict=True)]
    invisible = {"width": 0, "opacity": 0}
    return [
        {
            "type": "line",
            "name": "history floor",
            "data": lo,
            "stack": "envelope",
            "showSymbol": False,
            "lineStyle": invisible,
            "tooltip": {"show": False},
            "silent": True,
            "z": 0,
        },
        {
            "type": "line",
            "name": "history range",
            "data": delta,
            "stack": "envelope",
            "showSymbol": False,
            "lineStyle": invisible,
            "areaStyle": {"color": "#94a3b8", "opacity": 0.18},
            "tooltip": {"show": False},
            "silent": True,
            "z": 0,
        },
    ]


def _recent_gradient_series(recent: list[list[float]]) -> list[dict[str, Any]]:
    """Per-capture lines for the last K captures, fading by age.

    All recent traces stay in the blue family — gray is reserved for
    the historical-range band underneath. The newest is fully
    saturated; each older step desaturates toward a duller blue
    while keeping a hint of the brand color so the eye reads the
    progression as "ages of the same signal," not "different
    signal." Only the latest contributes to the tooltip.
    """
    # Family of blues, saturated → muted. Hand-picked so each step
    # is visibly distinct without bleeding into gray. ``recent[-1]``
    # uses the saturated brand blue; every step backwards picks the
    # next dimmer entry.
    palette = [
        "#3b82f6",  # newest: bold blue
        "#5b8de6",
        "#6e96d6",
        "#7a99c4",
        "#8197b3",
        "#8693a3",
        "#878d95",
        "#888889",  # ~10 steps in — basically near-neutral but still
        # cooler than the gray history band.
    ]
    n = len(recent)
    series: list[dict[str, Any]] = []
    for idx, samples in enumerate(recent):
        is_latest = idx == n - 1
        # idx counts up with newer traces; reverse so 0 → latest.
        steps_back = n - 1 - idx
        color = palette[min(steps_back, len(palette) - 1)]
        if is_latest:
            line_style = {"width": 1.5, "opacity": 1.0, "color": color}
        else:
            # Slight fade so even the slow desaturation doesn't make
            # back-to-back captures stamp on top of each other.
            opacity = max(0.45, 0.95 - 0.07 * steps_back)
            line_style = {"width": 1.0, "opacity": opacity, "color": color}
        series.append(
            {
                "type": "line",
                "name": "Latest" if is_latest else f"-{steps_back}",
                "data": samples,
                "showSymbol": False,
                "smooth": False,
                "lineStyle": line_style,
                "z": 10 if is_latest else 5,
                "tooltip": {"show": is_latest},
                "silent": not is_latest,
            }
        )
    return series


def _axis_tick(ts: Any) -> str:
    """Compact ``YYYY-MM-DD HH:MM:SS`` for ECharts categorical axis labels.

    ECharts renders axis ticks as plain text, so the HTML-wrapped
    output of :func:`format_datetime` doesn't apply here.
    """
    if not ts:
        return ""
    s = str(ts)
    if "T" in s:
        date, _, rest = s.partition("T")
        time = rest.split(".", 1)[0].split("+", 1)[0].split("-", 1)[0]
        return f"{date} {time}"
    return s
