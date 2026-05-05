"""Parametric viewer — compare measurements across runs.

Filter-first UX: scope the data with chip multi-selects, then ask a
question with Y / X / group_by. ENUM facet options come straight from
the data-model enums (zero DB queries); STRING facet options come from
cross-filtered DISTINCT queries against the current filter set
(Tableau-style). All selections live in the URL query string so the
view is shareable by copy-paste.
"""

from __future__ import annotations

import datetime
import logging
import traceback
from datetime import date
from typing import Any

from fastapi import Request
from nicegui import ui

from litmus.analysis.measurement_facets import (
    MEASUREMENT_FACETS,
    FacetKind,
    FacetSpec,
    FilterSet,
)
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.ui.shared.components import (
    page_header,
    page_layout,
    push_url_state,
    render_empty_card,
)
from litmus.ui.shared.layout import create_layout

logger = logging.getLogger(__name__)

CHART_TYPES = ["scatter", "line", "bar", "histogram"]
DEFAULT_BINS = 30
DEFAULT_LIMIT = 5000

# Categorical columns DuckDB reports as VARCHAR — fine as group_by /
# X candidates. Numeric types stay candidates for Y. We keep the
# split coarse so users see all real columns; the SQL builder
# rejects bad identifiers anyway.
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


def _render_no_measurements_state() -> None:
    """Render the empty state for ``/explore`` when no measurements exist.

    The page is otherwise an elaborate filter-first dashboard with
    nothing to filter — that's a confusing landing for someone who
    just opened the app. Replace it with a single card that names
    the cause and points at the next step.

    Uses the shared :func:`page_layout` + :func:`page_header`
    primitives so the empty state shares the same outer shell as
    every other Litmus page.
    """
    with page_layout():
        page_header("Measurements", icon="scatter_plot")
        with ui.card().classes("w-full max-w-3xl"):
            with ui.card_section():
                ui.label("No measurements recorded yet.").classes("text-lg text-slate-700")
                ui.label(
                    "This page plots numeric measurements across runs — "
                    "yield trends, drift, distributions. It needs at "
                    "least one test that recorded a value via "
                    "``logger.measure()`` or the ``verify`` fixture."
                ).classes("text-sm text-slate-500 mt-1")
            ui.separator()
            with ui.card_section():
                ui.label("Quick start").classes("text-xs uppercase tracking-wider text-slate-500")
                ui.html(
                    """
                    <pre class="text-xs bg-slate-50 p-3 rounded mt-2 overflow-auto">"""
                    """from litmus.models.test_config import Limit\n\n"""
                    """def test_voltage_in_range(verify):\n"""
                    """    verify("vout", 3.3, limit=Limit(low=3.0, high=3.6, units="V"))</pre>""",
                    sanitize=False,
                )
                ui.label(
                    "Run that with ``litmus serve`` (or ``pytest`` directly), "
                    "then revisit this page."
                ).classes("text-xs text-slate-500 mt-2")


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


def _query_dict_from_request(request: Request) -> dict[str, list[str]]:
    """Multi-value query string → ``{key: [v1, v2, ...]}``.

    FastAPI's ``request.query_params`` is a multi-dict; ``getlist`` is
    the way to recover repeated keys (e.g. ``?product=A&product=B``).
    """
    return {k: request.query_params.getlist(k) for k in set(request.query_params)}


@ui.page("/explore")
def explore_page(request: Request):
    """Parametric measurement viewer — filter-first, model-driven.

    URL state encodes the full view: each filter facet's selected
    values as repeated query keys (``?product=PN-100&product=PN-200``)
    plus ``y`` / ``x`` / ``chart_type`` / ``group_by`` / ``bins`` /
    ``limit`` / ``since`` / ``until``.
    """
    from litmus.data.results_dir import resolve_results_dir

    results_dir = str(resolve_results_dir())

    create_layout("Measurements")

    # Schema fetch — drives the Y/X/group_by dropdowns.
    from litmus.data._flight_query import IndexOutOfDate

    try:
        with MeasurementsQuery(_results_dir=results_dir) as q:
            schema = q.describe_columns()
    except IndexOutOfDate:
        # No measurement parquets exist yet — daemon's measurements
        # view was deferred. Render the same empty state we use when
        # the table is present-but-empty so the user sees one
        # consistent landing.
        _render_no_measurements_state()
        return
    except (OSError, ValueError, RuntimeError) as exc:
        with page_layout():
            page_header("Measurements", icon="scatter_plot")
            error_container = ui.column().classes("w-full")
            render_empty_card(
                error_container,
                "Schema unavailable",
                f"Error loading schema: {exc}",
            )
        return

    # Schema exists but the table may be empty (the more common case):
    # no test run has recorded a measurement yet. Render an empty
    # state with a concrete next step rather than dropping the
    # operator into a fully-configured filter UI that has no data
    # to show.
    try:
        with MeasurementsQuery(_results_dir=results_dir) as q:
            initial_counts = q.summary_counts()
    except (OSError, ValueError, RuntimeError):
        initial_counts = None
    if initial_counts is not None and initial_counts.total_rows == 0:
        _render_no_measurements_state()
        return

    y_options, x_options, group_options = _classify_columns(schema)

    # Decode URL state.
    qp = request.query_params
    qd = _query_dict_from_request(request)
    is_bare_url = not qd
    initial_filters = FilterSet.from_url_params(qd)
    initial_y = qp.get("y", "")
    initial_x = qp.get("x", "")
    initial_chart_type = qp.get("chart_type", "scatter")
    initial_group_by = qp.get("group_by", "")
    try:
        initial_bins = int(qp.get("bins") or DEFAULT_BINS)
    except ValueError:
        initial_bins = DEFAULT_BINS
    try:
        initial_limit = int(qp.get("limit") or DEFAULT_LIMIT)
    except ValueError:
        initial_limit = DEFAULT_LIMIT

    # Smart defaults for bare URL — comparing `value` across many
    # measurement names at once is meaningless (different scales,
    # different units). Pick the most-populated measurement_name as
    # the starter scope. Y=value, X=vector_index (per-vector trend
    # within a run) when present, falling back to run_started_at
    # (cross-run trend) for older data without vector_index.
    if is_bare_url:
        try:
            with MeasurementsQuery(_results_dir=results_dir) as q:
                top_names = q.distinct_values("measurement_name", filters=FilterSet(), limit=20)
        except (OSError, ValueError, RuntimeError, IndexOutOfDate):
            top_names = []
        # Skip synthetic step-level rollups (Litmus convention: '_'
        # prefix marks names that don't carry a measurement value).
        real_names = [o for o in top_names if not o.value.startswith("_")]
        if real_names:
            initial_filters = FilterSet(string_filters={"measurement_name": [real_names[0].value]})
        # Default Y: prefer ``measurement_value`` (the canonical
        # column for real measurement values) over ``value``. Some
        # legacy / test parquets project a ``value`` column that
        # ``union_by_name`` lifts into the schema but leaves NULL
        # for production rows; landing the user on ``value`` produces
        # an empty graph for everyone.
        if not initial_y:
            for candidate in ("measurement_value", "value"):
                if candidate in y_options:
                    initial_y = candidate
                    break
        if not initial_x:
            for candidate in ("vector_index", "run_started_at"):
                if candidate in x_options:
                    initial_x = candidate
                    break

    # Sanitize URL-supplied selections against schema so a stale URL
    # gracefully degrades rather than blowing up.
    if initial_y not in y_options:
        initial_y = y_options[0] if y_options else ""
    if initial_x not in x_options:
        initial_x = x_options[0] if x_options else ""
    if initial_chart_type not in CHART_TYPES:
        initial_chart_type = "scatter"
    if initial_group_by and initial_group_by not in group_options:
        initial_group_by = ""

    # Mutable state captured by the closures below.
    state: dict[str, Any] = {
        "filter_set": initial_filters,
        "y": initial_y,
        "x": initial_x,
        "chart_type": initial_chart_type,
        "group_by": initial_group_by,
        "bins": initial_bins,
        "limit": initial_limit,
    }

    facet_widgets: dict[str, Any] = {}
    cardinality_label: Any = None
    chart_container: Any = None

    def _new_query() -> MeasurementsQuery:
        return MeasurementsQuery(_results_dir=results_dir)

    def _push_url() -> None:
        # Group multi-value facets so a single key carries a list —
        # ``push_url_state`` renders lists as repeated query keys.
        grouped: dict[str, list[str]] = {}
        for key, value in state["filter_set"].to_url_params():
            grouped.setdefault(key, []).append(value)
        params: dict[str, Any] = dict(grouped)
        if state["y"]:
            params["y"] = state["y"]
        if state["x"] and state["chart_type"] != "histogram":
            params["x"] = state["x"]
        if state["chart_type"] != "scatter":
            params["chart_type"] = state["chart_type"]
        if state["group_by"]:
            params["group_by"] = state["group_by"]
        if state["bins"] != DEFAULT_BINS:
            params["bins"] = str(state["bins"])
        if state["limit"] != DEFAULT_LIMIT:
            params["limit"] = str(state["limit"])
        push_url_state("/explore", params)

    def _refresh_string_facets() -> None:
        """Re-populate STRING facet options based on current filter set."""
        with _new_query() as q:
            for facet in MEASUREMENT_FACETS:
                if facet.kind is not FacetKind.STRING:
                    continue
                widget = facet_widgets.get(facet.column)
                if widget is None:
                    continue
                opts = q.distinct_values(
                    facet.column, filters=state["filter_set"], exclude_self=True
                )
                # value → "value (count)" so users see frequency without
                # losing the underlying value used in SQL.
                widget.options = {o.value: f"{o.value} ({o.count:,})" for o in opts}
                # Drop any selected values that vanished from the option set.
                current = state["filter_set"].string_filters.get(facet.column, [])
                still_valid = [v for v in current if v in widget.options]
                widget.value = still_valid
                if still_valid != current:
                    state["filter_set"].string_filters[facet.column] = still_valid
                widget.update()

    def _refresh_cardinality() -> None:
        with _new_query() as q:
            counts = q.summary_counts(filters=state["filter_set"])
        cardinality_label.text = (
            f"{counts.total_rows:,} measurements · "
            f"{counts.distinct_runs:,} runs · "
            f"{counts.distinct_measurements:,} measurement names · "
            f"{counts.distinct_products:,} products"
        )

    def _refresh_chart() -> None:
        chart_container.clear()
        y_val = state["y"]
        x_val = state["x"]
        ct = state["chart_type"]
        if not y_val or (not x_val and ct != "histogram"):
            with chart_container:
                ui.label("Pick a Y and X column").classes("text-slate-500 italic")
            return
        try:
            with _new_query() as q:
                rows = q.parametric(
                    y=y_val,
                    x=x_val,
                    filters=state["filter_set"],
                    group_by=state["group_by"] or None,
                    chart_type=ct,
                    bins=state["bins"],
                    limit=state["limit"],
                )
        except (OSError, ValueError, RuntimeError) as exc:
            with chart_container:
                ui.label(f"Query failed: {exc}").classes("text-red-600")
                with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                    ui.code(traceback.format_exc()).classes("text-xs")
            return
        with chart_container:
            _render_chart([r.model_dump() for r in rows], ct, y_val, x_val)

    def _refresh_all() -> None:
        _push_url()
        _refresh_cardinality()
        _refresh_string_facets()
        _refresh_chart()

    def _on_string_facet_change(facet: FacetSpec, e: Any) -> None:
        values = list(e.value or [])
        if values:
            state["filter_set"].string_filters[facet.column] = values
        else:
            state["filter_set"].string_filters.pop(facet.column, None)
        _refresh_all()

    def _on_enum_facet_change(facet: FacetSpec, e: Any) -> None:
        values = list(e.value or [])
        if values:
            state["filter_set"].enum_filters[facet.column] = values
        else:
            state["filter_set"].enum_filters.pop(facet.column, None)
        _refresh_all()

    def _on_since_change(e: Any) -> None:
        state["filter_set"].since = _parse_iso_date(e.value)
        _refresh_all()

    def _on_until_change(e: Any) -> None:
        state["filter_set"].until = _parse_iso_date(e.value)
        _refresh_all()

    # ── Layout ──────────────────────────────────────────────────────
    with ui.column().classes("w-full p-6 gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("scatter_plot").classes("text-slate-600")
            ui.label("Measurements").classes("text-2xl font-semibold text-slate-700")

        # FILTER section
        with ui.card().classes("w-full"):
            ui.label("FILTER").classes("text-xs font-semibold text-slate-500 tracking-wider")
            with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                for facet in MEASUREMENT_FACETS:
                    facet_widgets[facet.column] = _build_facet_widget(
                        facet,
                        state["filter_set"],
                        on_string_change=_on_string_facet_change,
                        on_enum_change=_on_enum_facet_change,
                        on_since_change=_on_since_change,
                        on_until_change=_on_until_change,
                    )
            cardinality_label = ui.label("…").classes("text-sm text-slate-600 italic mt-2")

        # PLOT section
        with ui.card().classes("w-full"):
            ui.label("PLOT").classes("text-xs font-semibold text-slate-500 tracking-wider")
            with ui.row().classes("items-end gap-3 flex-wrap w-full"):
                y_select = ui.select(
                    y_options,
                    value=state["y"],
                    label="Y axis",
                    with_input=True,
                    on_change=lambda e: (state.update(y=str(e.value or "")), _refresh_all()),
                ).classes("w-56")
                x_select = ui.select(
                    x_options,
                    value=state["x"],
                    label="X axis",
                    with_input=True,
                    on_change=lambda e: (state.update(x=str(e.value or "")), _refresh_all()),
                ).classes("w-56")
                chart_type_select = ui.select(
                    CHART_TYPES,
                    value=state["chart_type"],
                    label="Chart",
                    on_change=lambda e: (
                        state.update(chart_type=str(e.value or "scatter")),
                        _refresh_all(),
                    ),
                ).classes("w-32")
                group_select = ui.select(
                    [""] + group_options,
                    value=state["group_by"],
                    label="Group by",
                    with_input=True,
                    on_change=lambda e: (
                        state.update(group_by=str(e.value or "")),
                        _refresh_all(),
                    ),
                ).classes("w-48")
                bins_input = ui.number(
                    label="Bins",
                    value=state["bins"],
                    format="%d",
                    min=2,
                    max=200,
                    on_change=lambda e: (
                        state.update(bins=int(e.value or DEFAULT_BINS)),
                        _refresh_chart(),
                        _push_url(),
                    ),
                ).classes("w-24")
                limit_input = ui.number(
                    label="Limit",
                    value=state["limit"],
                    format="%d",
                    min=10,
                    max=100_000,
                    on_change=lambda e: (
                        state.update(limit=int(e.value or DEFAULT_LIMIT)),
                        _refresh_chart(),
                        _push_url(),
                    ),
                ).classes("w-28")
                ui.button("Refresh", icon="refresh", on_click=lambda: _refresh_all()).props(
                    "outline"
                )

        # Chart container
        chart_container = ui.column().classes("w-full")

    # Bind a few widgets to silence unused warnings (they're closure-captured above).
    _ = (y_select, x_select, chart_type_select, group_select, bins_input, limit_input)

    _refresh_all()

    # Subscribe to ``run.ended`` so the chart refreshes once a new
    # run finalizes (and its measurements parquet ingests). We
    # intentionally don't subscribe to ``test.measurement`` — the
    # measurements view is parquet-driven and only sees rows after
    # the canonical aggregate lands; per-event point append is
    # tracked in ROADMAP.
    from pathlib import Path as _Path

    from litmus.data.event_store import EventStore
    from litmus.ui.shared.components import subscribe_with_refresh

    try:
        event_store = EventStore(_results_dir=_Path(results_dir))
        subscribe_with_refresh(event_store, ["run.ended"], _refresh_all)
    except (OSError, RuntimeError) as exc:
        logger.warning("Live updates unavailable: %s", exc)


def _parse_iso_date(value: Any) -> date | None:
    """Parse an ISO date string from a date picker, or ``None`` for blanks."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _build_facet_widget(  # noqa: PLR0913
    facet: FacetSpec,
    filter_set: FilterSet,
    *,
    on_string_change: Any,
    on_enum_change: Any,
    on_since_change: Any,
    on_until_change: Any,
) -> Any:
    """Render one compact facet block and return its primary widget.

    Each facet is a single labelled select (or a since/until pair for
    dates). They flex-wrap horizontally in the parent row instead of
    stacking. Descriptions become tooltips so they don't bloat the
    section vertically.
    """
    if facet.kind is FacetKind.DATE:
        with ui.row().classes("gap-2 items-end"):
            since_input = (
                ui.input(
                    "Since",
                    value=filter_set.since.isoformat() if filter_set.since else "",
                )
                .classes("w-36")
                .props("dense outlined")
            )
            with since_input.add_slot("append"):
                ui.icon("event").on("click", lambda: since_menu.open()).classes("cursor-pointer")
            with ui.menu() as since_menu:
                ui.date(
                    value=filter_set.since.isoformat() if filter_set.since else None,
                    on_change=on_since_change,
                ).bind_value(since_input)
            until_input = (
                ui.input(
                    "Until",
                    value=filter_set.until.isoformat() if filter_set.until else "",
                )
                .classes("w-36")
                .props("dense outlined")
            )
            with until_input.add_slot("append"):
                ui.icon("event").on("click", lambda: until_menu.open()).classes("cursor-pointer")
            with ui.menu() as until_menu:
                ui.date(
                    value=filter_set.until.isoformat() if filter_set.until else None,
                    on_change=on_until_change,
                ).bind_value(until_input)
        return since_input

    if facet.kind is FacetKind.ENUM:
        assert facet.enum_class is not None
        options = [m.value for m in facet.enum_class.__members__.values()]
        current = filter_set.enum_filters.get(facet.column, [])
        sel = (
            ui.select(
                options,
                multiple=True,
                value=current,
                label=facet.label,
                with_input=False,
                on_change=lambda e, f=facet: on_enum_change(f, e),
            )
            .classes("w-56")
            .props("use-chips dense outlined")
        )
        if facet.description:
            sel.tooltip(facet.description)
        return sel

    # FacetKind.STRING — options populated lazily by _refresh_string_facets
    current = filter_set.string_filters.get(facet.column, [])
    sel = (
        ui.select(
            {v: v for v in current},  # seed with current selections so they render
            multiple=True,
            value=current,
            label=facet.label,
            with_input=True,
            on_change=lambda e, f=facet: on_string_change(f, e),
        )
        .classes("w-56")
        .props("use-chips dense outlined")
    )
    if facet.description:
        sel.tooltip(facet.description)
    return sel


# ── Chart helpers (preserved from previous iteration) ────────────────


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


def _x_axis_opt(label: str, x_type: str, *, data: list[Any] | None = None) -> dict[str, Any]:
    """Standard X-axis with centered name, units-friendly gap, bold style."""
    opt: dict[str, Any] = {
        "type": x_type,
        "name": label,
        "nameLocation": "middle",
        "nameGap": 32,
        "nameTextStyle": {"fontSize": 13, "fontWeight": "bold"},
        "axisLine": {"show": True},
        "axisTick": {"show": True},
    }
    if x_type == "value":
        opt["scale"] = True
    if data is not None:
        opt["data"] = data
    return opt


def _y_axis_opt(label: str) -> dict[str, Any]:
    """Standard Y-axis with rotated centered name, scale=True for headroom."""
    return {
        "type": "value",
        "name": label,
        "nameLocation": "middle",
        "nameRotate": 90,
        "nameGap": 50,
        "nameTextStyle": {"fontSize": 13, "fontWeight": "bold"},
        "scale": True,
        "axisLine": {"show": True},
        "axisTick": {"show": True},
    }


_GRID = {"left": 70, "right": 30, "top": 50, "bottom": 80, "containLabel": True}
_DATA_ZOOM_XY = [
    {"type": "inside", "xAxisIndex": 0},
    {"type": "inside", "yAxisIndex": 0},
    {"type": "slider", "xAxisIndex": 0, "bottom": 10, "height": 18},
]
_DATA_ZOOM_X = [
    {"type": "inside", "xAxisIndex": 0},
    {"type": "slider", "xAxisIndex": 0, "bottom": 10, "height": 18},
]


def _toolbox(*, allow_y_zoom: bool) -> dict[str, Any]:
    """ECharts toolbox: box-zoom, restore, save image, raw data view."""
    return {
        "right": 20,
        "top": 0,
        "feature": {
            "dataZoom": {
                "yAxisIndex": False if allow_y_zoom else "none",
                "title": {"zoom": "Box zoom", "back": "Reset zoom"},
            },
            "restore": {"title": "Reset"},
            "saveAsImage": {"title": "Save PNG", "name": "litmus_explore"},
            "dataView": {"title": "View data", "readOnly": True, "lang": ["Data", "Close", ""]},
        },
    }


def _series_label(group_key: str, fallback: str) -> str:
    """Display name for a series — empty / null groups become legible labels."""
    if group_key == "":
        return fallback
    if group_key in ("None", "null"):
        return "(no value)"
    return group_key


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

    by_group: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_group.setdefault(str(r.get("group", "")), []).append(r)

    legend_names = [_series_label(g, y_label) for g in by_group]

    if chart_type == "histogram":
        first = next(iter(by_group.values()))
        x_axis = [round(float(r["x"]), 4) for r in first]
        series = [
            {
                "type": "bar",
                "name": _series_label(grp, y_label),
                "data": [r["y"] for r in items],
                "stack": "histogram",
                "barCategoryGap": "5%",
            }
            for grp, items in by_group.items()
        ]
        option: dict[str, Any] = {
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "legend": {"data": legend_names, "top": 0},
            "toolbox": _toolbox(allow_y_zoom=False),
            "grid": _GRID,
            "xAxis": _x_axis_opt(y_label, "category", data=x_axis),
            "yAxis": _y_axis_opt("count"),
            "series": series,
            "dataZoom": _DATA_ZOOM_X,
        }
    elif chart_type == "bar":
        first = next(iter(by_group.values()))
        x_axis = [r["x"] for r in first]
        series = [
            {
                "type": "bar",
                "name": _series_label(grp, y_label),
                "data": [r["y"] for r in items],
            }
            for grp, items in by_group.items()
        ]
        option = {
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "legend": {"data": legend_names, "top": 0},
            "toolbox": _toolbox(allow_y_zoom=False),
            "grid": _GRID,
            "xAxis": _x_axis_opt(x_label, "category", data=x_axis),
            "yAxis": _y_axis_opt(y_label),
            "series": series,
            "dataZoom": _DATA_ZOOM_X,
        }
    else:  # scatter or line
        series_type = "line" if chart_type == "line" else "scatter"
        x_type = _x_axis_type(rows)
        series = [
            {
                "type": series_type,
                "name": _series_label(grp, y_label),
                "data": [[_coerce_x(r["x"]), r["y"]] for r in items],
                "symbolSize": 6,
                "showSymbol": True,
            }
            for grp, items in by_group.items()
        ]
        option = {
            "tooltip": {"trigger": "item"},
            "legend": {"data": legend_names, "top": 0},
            "toolbox": _toolbox(allow_y_zoom=True),
            "grid": _GRID,
            "xAxis": _x_axis_opt(x_label, x_type),
            "yAxis": _y_axis_opt(y_label),
            "series": series,
            "dataZoom": _DATA_ZOOM_XY,
        }

    chart = ui.echart(option).classes("w-full h-[28rem]")
    ui.timer(0.1, lambda: chart.run_chart_method("resize"), once=True)
