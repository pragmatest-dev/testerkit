"""Parametric viewer — compare measurements across runs.

Pick any silver column for Y / X, optionally split by a categorical
group, switch chart types (scatter / line / bar / histogram). All
selections live in the URL query string so the view is shareable
by copy-paste.
"""

from __future__ import annotations

import datetime
import json
import logging
import traceback
from typing import Any
from urllib.parse import urlencode

from nicegui import ui

from litmus.analysis.metrics_store import MetricsStore
from litmus.ui.shared.layout import create_layout

logger = logging.getLogger(__name__)

CHART_TYPES = ["scatter", "line", "bar", "histogram"]

# Categorical columns DuckDB reports as VARCHAR — fine as group_by /
# X candidates. Numeric types stay candidates for Y. We keep the
# split coarse so users see all real columns; the SQL builder
# rejects bad identifiers via _safe_ident anyway.
_NUMERIC_TYPES = {
    "DOUBLE",
    "FLOAT",
    "REAL",
    "DECIMAL",
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "HUGEINT",
}


def _classify_columns(
    schema: list[dict[str, str]],
) -> tuple[list[str], list[str], list[str]]:
    """Return (y_candidates, x_candidates, group_candidates).

    Y wants numerics. X accepts numerics, dates, and bare strings.
    Group wants strings (low-cardinality presumed).
    """
    y_candidates: list[str] = []
    x_candidates: list[str] = []
    group_candidates: list[str] = []
    for col in schema:
        name = col["column_name"]
        col_type = (col.get("column_type") or "").upper()
        is_numeric = any(t in col_type for t in _NUMERIC_TYPES)
        is_string = "VARCHAR" in col_type or "CHAR" in col_type
        is_date = "DATE" in col_type or "TIMESTAMP" in col_type
        if is_numeric:
            y_candidates.append(name)
        if is_numeric or is_string or is_date:
            x_candidates.append(name)
        if is_string:
            group_candidates.append(name)
    return sorted(y_candidates), sorted(x_candidates), sorted(group_candidates)


@ui.page("/explore")
def explore_page(  # noqa: PLR0913
    results_dir: str = "",
    y: str = "measurement_value",
    x: str = "run_started_at",
    chart_type: str = "scatter",
    group_by: str = "",
    bins: int = 30,
    limit: int = 5000,
    filters: str = "",
):
    """Parametric measurement viewer.

    Args:
        results_dir: Results directory path.
        y: Y-axis column name.
        x: X-axis column name (ignored for histogram).
        chart_type: One of scatter / line / bar / histogram.
        group_by: Optional column to split into series.
        bins: Histogram bin count.
        limit: Max rows for non-aggregated charts.
        filters: JSON-encoded ``{column: value}`` equality filters.
    """
    if not results_dir:
        from litmus.data.results_dir import resolve_results_dir

        results_dir = str(resolve_results_dir())

    create_layout("Parametric Viewer")

    # Load schema — drives dropdown options.
    try:
        store = MetricsStore(_results_dir=results_dir)
        schema = store.describe_silver()
        store.close()
    except (OSError, ValueError, RuntimeError) as e:
        with ui.column().classes("w-full p-6"):
            ui.label(f"Error loading schema: {e}").classes("text-red-600")
        return

    y_options, x_options, group_options = _classify_columns(schema)

    # Sanitize URL-supplied selections against the real schema. Keeps
    # a stale URL from blowing up the page.
    y = y if y in y_options else (y_options[0] if y_options else "")
    x = x if x in x_options else (x_options[0] if x_options else "")
    chart_type = chart_type if chart_type in CHART_TYPES else "scatter"
    group_by = group_by if group_by in group_options else ""

    try:
        initial_filters: dict[str, str] = json.loads(filters) if filters else {}
        if not isinstance(initial_filters, dict):
            initial_filters = {}
    except json.JSONDecodeError:
        initial_filters = {}

    chart_container: Any = None  # filled in below

    def _push_url() -> None:
        ct = str(chart_type_select.value or "scatter")
        params: dict[str, str] = {}
        if y_select.value:
            params["y"] = str(y_select.value)
        if x_select.value and ct != "histogram":
            params["x"] = str(x_select.value)
        if ct != "scatter":
            params["chart_type"] = ct
        if group_select.value:
            params["group_by"] = str(group_select.value)
        if int(bins_input.value or 30) != 30:
            params["bins"] = str(int(bins_input.value or 30))
        if int(limit_input.value or 5000) != 5000:
            params["limit"] = str(int(limit_input.value or 5000))
        if filters_input.value and filters_input.value.strip():
            params["filters"] = filters_input.value.strip()
        new_url = "/explore" + (f"?{urlencode(params)}" if params else "")
        ui.run_javascript(f"history.replaceState(null, '', {json.dumps(new_url)})")

    def _refresh() -> None:
        _push_url()
        chart_container.clear()
        raw_filters = (filters_input.value or "").strip()
        try:
            parsed_filters: dict[str, str] = json.loads(raw_filters) if raw_filters else {}
            if not isinstance(parsed_filters, dict):
                raise ValueError("Filters must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            with chart_container:
                ui.label(f"Bad filters JSON: {e}").classes("text-red-600")
            return

        y_val = str(y_select.value or "")
        x_val = str(x_select.value or "")
        ct_val = str(chart_type_select.value or "scatter")
        if not y_val or (not x_val and ct_val != "histogram"):
            with chart_container:
                ui.label("Pick a Y and X column").classes("text-slate-500 italic")
            return

        try:
            store = MetricsStore(_results_dir=results_dir)
            try:
                rows = store.parametric(
                    y=y_val,
                    x=x_val,
                    filters=parsed_filters,
                    group_by=str(group_select.value) if group_select.value else None,
                    chart_type=ct_val,
                    bins=int(bins_input.value or 30),
                    limit=int(limit_input.value or 5000),
                )
            finally:
                store.close()
        except (OSError, ValueError, RuntimeError) as e:
            with chart_container:
                ui.label(f"Query failed: {e}").classes("text-red-600")
                with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                    ui.code(traceback.format_exc()).classes("text-xs")
            return

        with chart_container:
            _render_chart(rows, ct_val, y_val, x_val)

    with ui.column().classes("w-full p-6 gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("scatter_plot").classes("text-slate-600")
            ui.label("Parametric Viewer").classes("text-2xl font-semibold text-slate-700")

        with ui.row().classes("gap-3 flex-wrap items-end w-full"):
            y_select = ui.select(
                y_options,
                value=y,
                label="Y axis",
                with_input=True,
                on_change=lambda _: _refresh(),
            ).classes("w-56")
            x_select = ui.select(
                x_options,
                value=x,
                label="X axis",
                with_input=True,
                on_change=lambda _: _refresh(),
            ).classes("w-56")
            chart_type_select = ui.select(
                CHART_TYPES,
                value=chart_type,
                label="Chart",
                on_change=lambda _: _refresh(),
            ).classes("w-32")
            group_select = ui.select(
                [""] + group_options,
                value=group_by,
                label="Group by",
                with_input=True,
                on_change=lambda _: _refresh(),
            ).classes("w-48")
            bins_input = ui.number(label="Bins", value=bins, format="%d", min=2, max=200).classes(
                "w-24"
            )
            limit_input = ui.number(
                label="Limit",
                value=limit,
                format="%d",
                min=10,
                max=100000,
            ).classes("w-28")
            ui.button("Refresh", icon="refresh", on_click=lambda: _refresh()).props("outline")

        filters_input = ui.input(
            label='Filters (JSON, e.g. {"product_id": "PN-100"})',
            value=json.dumps(initial_filters) if initial_filters else "",
            placeholder="{}",
        ).classes("w-full")
        filters_input.on("blur", lambda _: _refresh())

        chart_container = ui.column().classes("w-full")

    _refresh()


def _coerce_x(value: Any) -> Any:
    """Convert a Python value to something ECharts can plot.

    datetime → epoch ms (ECharts ``time`` axis expects this). Other
    types pass through.
    """
    if isinstance(value, datetime.datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, datetime.date):
        return int(
            datetime.datetime.combine(value, datetime.time.min, tzinfo=datetime.UTC).timestamp()
            * 1000
        )
    return value


def _x_axis_type(rows: list[dict[str, Any]]) -> str:
    """Pick the ECharts xAxis type based on the first non-null x value.

    datetime → ``time``, str → ``category``, numeric → ``value``.
    """
    for r in rows:
        xv = r.get("x")
        if xv is None:
            continue
        if isinstance(xv, datetime.datetime | datetime.date):
            return "time"
        if isinstance(xv, str):
            return "category"
        return "value"
    return "value"


def _render_chart(  # noqa: PLR0912
    rows: list[dict[str, Any]],
    chart_type: str,
    y_label: str,
    x_label: str,
) -> None:
    """Render rows into an ECharts plot. Long-format → grouped series."""
    if not rows:
        ui.label("No data matches these selections").classes("text-slate-500 italic")
        return

    # Group rows by `group` key.
    by_group: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_group.setdefault(str(r.get("group", "")), []).append(r)

    if chart_type == "histogram":
        # Categorical x-axis from bin midpoints; one bar series per group.
        # Pick the first group's x list as the canonical axis.
        first = next(iter(by_group.values()))
        x_axis = [round(float(r["x"]), 4) for r in first]
        series = [
            {
                "type": "bar",
                "name": grp or y_label,
                "data": [r["y"] for r in items],
                "stack": "histogram",
            }
            for grp, items in by_group.items()
        ]
        option: dict[str, Any] = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": list(by_group.keys()) if any(by_group.keys()) else []},
            "xAxis": {"type": "category", "data": x_axis, "name": y_label},
            "yAxis": {"type": "value", "name": "count"},
            "series": series,
        }
    elif chart_type == "bar":
        first = next(iter(by_group.values()))
        x_axis = [r["x"] for r in first]
        series = [
            {"type": "bar", "name": grp or y_label, "data": [r["y"] for r in items]}
            for grp, items in by_group.items()
        ]
        option = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": list(by_group.keys()) if any(by_group.keys()) else []},
            "xAxis": {"type": "category", "data": x_axis, "name": x_label},
            "yAxis": {"type": "value", "name": y_label},
            "series": series,
        }
    else:  # scatter or line
        series_type = "line" if chart_type == "line" else "scatter"
        x_type = _x_axis_type(rows)
        series = [
            {
                "type": series_type,
                "name": grp or y_label,
                "data": [[_coerce_x(r["x"]), r["y"]] for r in items],
                "symbolSize": 6,
            }
            for grp, items in by_group.items()
        ]
        x_axis_opt: dict[str, Any] = {"type": x_type, "name": x_label}
        if x_type == "value":
            x_axis_opt["scale"] = True
        option = {
            "tooltip": {"trigger": "item"},
            "legend": {"data": list(by_group.keys()) if any(by_group.keys()) else []},
            "xAxis": x_axis_opt,
            "yAxis": {"type": "value", "name": y_label, "scale": True},
            "series": series,
            "dataZoom": [{"type": "inside"}, {"type": "slider"}],
        }

    chart = ui.echart(option).classes("w-full h-96")
    ui.timer(0.1, lambda: chart.run_chart_method("resize"), once=True)
