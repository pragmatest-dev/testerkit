"""Channel detail page — descriptor + filtered chart / data tabs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from litmus.ui.shared.components import data_table, format_datetime, info_field, push_url_state
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_channels, query_channel


@ui.page("/channels/{channel_id}")
def channel_detail_page(
    channel_id: str,
    session: str = "",
    since: str = "",
    until: str = "",
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
            ui.label(channel_id).classes("text-2xl font-semibold")
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

        # Discover which sessions actually wrote to this channel so
        # the operator picks from a labeled list instead of typing a
        # UUID. We query unfiltered upfront; refresh() applies the
        # selected filters to the same in-memory data.
        all_rows = query_channel(channel_id).get("data") or []
        session_options = _build_session_options(all_rows)
        filters = _Filters(session_options=session_options)
        # URL-driven session selection — fall back to the wildcard
        # if the URL points at a session that no longer has data.
        initial_session = session if session in session_options else "(any)"

        # Forward-declared so refresh() can guard against early fires.
        # bind_value on date pickers propagates the initial input value
        # synchronously during construction, which triggers on_change
        # before chart_card / data_card are built below.
        chart_card: ui.card | None = None
        data_card: ui.card | None = None

        def refresh() -> None:
            if chart_card is None or data_card is None:
                return
            push_url_state(
                f"/channels/{channel_id}",
                {
                    "session": filters.session_id(),
                    "since": filters.since(),
                    "until": filters.until(),
                },
            )
            payload = query_channel(
                channel_id,
                session_id=filters.session_id() or None,
                since=filters.since() or None,
                until=filters.until() or None,
                max_points=1000,  # LTTB decimation for chart-friendly response
            )
            data = payload.get("data") or []
            _render_chart(chart_card, channel_id, data, descriptor)
            _render_data_table(data_card, data)

        # Filters first (above chart + data) so the page reads top-down.
        with ui.card().classes("w-full"):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.session_select = ui.select(
                    options=session_options,
                    value=initial_session,
                    label="Session",
                    on_change=lambda _: refresh(),
                ).classes("w-72")

                with ui.input("Since", value=since).classes("w-44") as since_input:
                    with since_input.add_slot("append"):
                        ui.icon("event").on("click", lambda: since_menu.open()).classes(
                            "cursor-pointer"
                        )
                    with ui.menu() as since_menu:
                        ui.date(on_change=lambda _: refresh()).bind_value(since_input)
                filters.since_input = since_input

                with ui.input("Until", value=until).classes("w-44") as until_input:
                    with until_input.add_slot("append"):
                        ui.icon("event").on("click", lambda: until_menu.open()).classes(
                            "cursor-pointer"
                        )
                    with ui.menu() as until_menu:
                        ui.date(on_change=lambda _: refresh()).bind_value(until_input)
                filters.until_input = until_input

                ui.button(
                    "Clear", icon="clear", on_click=lambda: _clear_filters(filters, refresh)
                ).props("flat dense")

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        chart_card = ui.card().classes("w-full").props('data-testid="channel-chart"')
        data_card = ui.card().classes("w-full").props('data-testid="channel-data"')

        refresh()


def _build_session_options(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Return ``{session_id_or_(any): label}`` for the session dropdown.

    Operators identify a session by "what was running when" —
    timestamp + client (pytest / jupyter / connect.py). The label
    is built from the canonical SessionStarted-event metadata via
    :func:`format_session_label` so this page agrees with the
    Events page's session filter. Sessions order most-recent-first
    (by last-write to this channel — operators remember "what I
    just ran").
    """
    from litmus.ui.shared.components import format_session_label
    from litmus.ui.shared.services import query_sessions

    last_seen: dict[str, str] = {}
    for row in rows:
        sid = row.get("session_id")
        if sid is None:
            continue
        sid = str(sid)
        ts = str(row.get("received_at") or "")
        if sid not in last_seen or ts > last_seen[sid]:
            last_seen[sid] = ts

    session_events = query_sessions().get("sessions") or []
    session_label_by_id: dict[str, str] = {
        str(s["session_id"]): format_session_label(s) for s in session_events if s.get("session_id")
    }

    options: dict[str, str] = {"(any)": "All sessions"}
    for sid in sorted(last_seen, key=lambda s: last_seen[s], reverse=True):
        options[sid] = session_label_by_id.get(sid) or f"(unknown) {sid[:8]}"
    return options


def _clear_filters(filters: _Filters, refresh: Callable[[], None]) -> None:
    """Reset all filter widgets to defaults and re-render."""
    filters.session_select.set_value("(any)")
    filters.since_input.set_value("")
    filters.until_input.set_value("")
    refresh()


def _render_descriptor_card(descriptor: dict[str, Any]) -> None:
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.grid(columns=4).classes("gap-4"):
                info_field("Data type", descriptor.get("data_type") or "")
                info_field("Instrument role", descriptor.get("instrument_role") or "")
                info_field("Resource", descriptor.get("resource") or "")
                info_field("Units", descriptor.get("units") or "")
                info_field("First seen", format_datetime(descriptor.get("first_seen")))
                attrs = descriptor.get("attributes") or {}
                if attrs:
                    info_field("Attributes", ", ".join(f"{k}={v}" for k, v in attrs.items()))


def _render_chart(
    card: ui.card,
    channel_id: str,
    rows: list[dict[str, Any]],
    descriptor: dict[str, Any],
) -> None:
    """ECharts line plot of scalar values; first-sample plot for arrays."""
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
            return

        x_values = [_axis_tick(r.get("received_at")) for r in rows]
        units = descriptor.get("units") or ""
        y_label = f"value ({units})" if units else "value"

        # Array-of-items capture: rows carry a ``value`` list (e.g.
        # scope.waveform's per-trigger waveform). Plot the most recent
        # capture as a single series. We detect array rows from the
        # row shape rather than the registry's declared ``data_type``,
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

        chart = ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                # Legend off — multi-series waveform overlays don't have
                # meaningful series names (each is a capture timestamp);
                # the styling — blue solid for the latest, gray faded
                # for older — already conveys ordering.
                "legend": {"show": False},
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
                    {"type": "slider", "filterMode": "none", "height": 18, "bottom": 18},
                ],
                "xAxis": {
                    "type": "category",
                    "data": x_values,
                    "name": x_axis_label,
                    "nameLocation": "middle",
                    "nameGap": 30,
                },
                "yAxis": {"type": "value", "name": y_label, "scale": y_axis_scale},
                "series": _build_chart_series(rows, last_values, y_data),
                "grid": {"left": 60, "right": 30, "top": 60, "bottom": 70},
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
        table_rows = [
            {
                "id": str(idx),
                "received_at": format_datetime(r.get("received_at")),
                "value": _value_summary(r),
                "source": r.get("source_method") or "",
                "session": (str(r.get("session_id"))[:8] if r.get("session_id") else ""),
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
    """Lazy-read filter values from the input widgets.

    ``session_select`` carries either ``"(any)"`` (the wildcard
    option) or a real session UUID; ``session_id`` returns ``""`` for
    the wildcard so callers can pass ``or None`` straight through.
    """

    session_select: ui.select
    since_input: ui.input
    until_input: ui.input

    def __init__(self, *, session_options: dict[str, str]) -> None:
        self._session_options = session_options

    def session_id(self) -> str:
        v = (self.session_select.value or "").strip()
        return "" if v in ("", "(any)") else v

    def since(self) -> str:
        return (self.since_input.value or "").strip()

    def until(self) -> str:
        return (self.until_input.value or "").strip()


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
) -> list[dict[str, Any]]:
    """Build the ECharts ``series`` array.

    For waveform channels with multiple captures, every capture gets
    its own translucent line so the operator sees an eye-diagram-
    style overlay (newer captures land on top of older). The latest
    capture stays opaque so it's identifiable. Capped at 50 captures
    to keep the render cheap.

    For scalar channels the series is a single line.
    """
    if last_values is None or not last_values:
        return [
            {
                "type": "line",
                "data": y_data,
                "showSymbol": False,
                "smooth": False,
            }
        ]

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
