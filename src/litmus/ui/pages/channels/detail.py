"""Channel detail page — descriptor + filtered chart / data tabs."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import info_field
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_channels, query_channel


@ui.page("/channels/{channel_id}")
def channel_detail_page(channel_id: str) -> None:
    """Single-channel browser with a chart and a raw-data tab."""
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
        filters = _Filters()
        chart_card = ui.card().classes("w-full")
        data_card = ui.card().classes("w-full")

        def refresh() -> None:
            payload = query_channel(
                channel_id,
                session_id=filters.session_id() or None,
                since=filters.since() or None,
                until=filters.until() or None,
                last_n=filters.last_n(),
                max_points=1000,  # LTTB decimation for chart-friendly response
            )
            data = payload.get("data") or []
            _render_chart(chart_card, channel_id, data, descriptor)
            _render_data_table(data_card, data)

        with ui.card().classes("w-full"):
            with ui.row().classes("items-end gap-3 flex-wrap p-2"):
                filters.session_input = ui.input("Session ID").classes("w-64")
                filters.since_input = ui.input("Since (ISO)").classes("w-56")
                filters.until_input = ui.input("Until (ISO)").classes("w-56")
                filters.last_n_input = ui.number("Last N", value=0, min=0, step=100).classes("w-28")
                ui.button("Refresh", icon="refresh", on_click=refresh).props("color=primary")

        # Initial load
        refresh()


def _render_descriptor_card(descriptor: dict[str, Any]) -> None:
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.grid(columns=4).classes("gap-4"):
                info_field("Data type", descriptor.get("data_type") or "")
                info_field("Instrument role", descriptor.get("instrument_role") or "")
                info_field("Resource", descriptor.get("resource") or "")
                info_field("Units", descriptor.get("units") or "")
                info_field("First seen", _format_timestamp(descriptor.get("first_seen")))
                props = descriptor.get("properties") or {}
                if props:
                    info_field("Properties", ", ".join(f"{k}={v}" for k, v in props.items()))


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

        x_values = [_format_timestamp(r.get("timestamp")) for r in rows]
        units = descriptor.get("units") or ""
        y_label = f"value ({units})" if units else "value"

        if descriptor.get("data_type") == "array":
            # Array channels: plot the most recent capture as a single series.
            samples = rows[-1].get("samples") or []
            interval = rows[-1].get("sample_interval") or 1.0
            x_values = [f"{i * interval:.4g}" for i in range(len(samples))]
            y_data: list[Any] = list(samples)
            x_axis_label = "sample index"
        else:
            y_data = [_extract_scalar(r) for r in rows]
            x_axis_label = "timestamp"

        chart = ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "title": {
                    "text": channel_id,
                    "textStyle": {"fontSize": 14, "fontWeight": "normal"},
                    "left": "center",
                    "top": 8,
                },
                "xAxis": {
                    "type": "category",
                    "data": x_values,
                    "name": x_axis_label,
                    "nameLocation": "middle",
                    "nameGap": 30,
                },
                "yAxis": {"type": "value", "name": y_label},
                "series": [
                    {
                        "type": "line",
                        "data": y_data,
                        "showSymbol": False,
                        "smooth": False,
                    }
                ],
                "grid": {"left": 60, "right": 30, "top": 60, "bottom": 50},
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
    """Raw rows table — timestamp / value / source / session."""
    card.clear()
    with card:
        with ui.card_section():
            ui.label("Data").classes("font-semibold")
            ui.label(f"{len(rows)} sample(s) — most recent first.").classes(
                "text-xs text-slate-500"
            )

        if not rows:
            return

        with ui.card_section().classes("p-0"):
            columns = [
                {
                    "name": "timestamp",
                    "label": "Timestamp",
                    "field": "timestamp",
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
                    "timestamp": _format_timestamp(r.get("timestamp")),
                    "value": _value_summary(r),
                    "source": r.get("source_method") or "",
                    "session": (str(r.get("session_id"))[:8] if r.get("session_id") else ""),
                }
                for idx, r in enumerate(reversed(rows))
            ]
            ui.table(columns=columns, rows=table_rows, row_key="id").classes("w-full")


class _Filters:
    """Lazy-read filter values from the input widgets."""

    session_input: ui.input
    since_input: ui.input
    until_input: ui.input
    last_n_input: ui.number

    def session_id(self) -> str:
        return (self.session_input.value or "").strip()

    def since(self) -> str:
        return (self.since_input.value or "").strip()

    def until(self) -> str:
        return (self.until_input.value or "").strip()

    def last_n(self) -> int | None:
        try:
            v = int(self.last_n_input.value or 0)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None


def _extract_scalar(row: dict[str, Any]) -> Any:
    """Pick the numeric value from a scalar-channel row."""
    for key in ("value", "y", "reading"):
        if key in row and row[key] is not None:
            return row[key]
    # Fallback: first non-metadata field
    for k, v in row.items():
        if k not in ("timestamp", "source_method", "session_id") and v is not None:
            return v
    return None


def _value_summary(row: dict[str, Any]) -> str:
    """Render the row's value compactly (full numbers; truncate arrays)."""
    samples = row.get("samples")
    if isinstance(samples, list):
        head = ", ".join(f"{v:.4g}" if isinstance(v, (int, float)) else str(v) for v in samples[:4])
        more = "" if len(samples) <= 4 else f" … +{len(samples) - 4} more"
        return f"[{head}{more}]"
    val = _extract_scalar(row)
    if isinstance(val, (int, float)):
        return f"{val:.6g}"
    return "" if val is None else str(val)


def _format_timestamp(ts: Any) -> str:
    if not ts:
        return ""
    s = str(ts)
    if "T" in s:
        date, _, rest = s.partition("T")
        time = rest.split(".", 1)[0].split("+", 1)[0].split("-", 1)[0]
        return f"{date} {time}"
    return s
