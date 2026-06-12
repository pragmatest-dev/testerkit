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
from nicegui import run, ui

from litmus.analysis.measurement_facets import (
    MEASUREMENT_FACETS,
    FacetKind,
    FacetSpec,
    FilterSet,
    LimitBandRow,
)
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.data._flight_errors import FlightPermanentError
from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    page_header,
    page_layout,
    push_url_state,
    render_empty_card,
    render_skeleton,
    subscribe_with_refresh,
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


def _default_y(y_options: list[str]) -> str:
    """Pick a sensible default Y — the measurement value, not a stimulus input.

    Prefers ``measurement_value`` / ``value`` over the first numeric
    column (which is often an ``in_*`` input). Falls back to the first
    option only when neither is present.
    """
    if not y_options:
        return ""
    for candidate in ("measurement_value", "value"):
        if candidate in y_options:
            return candidate
    return y_options[0]


def _default_x(x_options: list[str]) -> str:
    """Pick a sensible default X axis — a real parameter, not an id column.

    Prefers a swept stimulus input (``in_*`` — the natural parametric
    axis), then the vector index, then time. Falls back to the first
    non-id column, and only to ``x_options[0]`` when everything is an id.
    """
    if not x_options:
        return ""
    swept = [c for c in x_options if c.startswith("in_")]
    if swept:
        return swept[0]
    for candidate in ("vector_index", "run_started_at"):
        if candidate in x_options:
            return candidate
    non_id = [c for c in x_options if not c.endswith("_id")]
    return non_id[0] if non_id else x_options[0]


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
    the way to recover repeated keys (e.g. ``?part=A&part=B``).
    """
    return {k: request.query_params.getlist(k) for k in set(request.query_params)}


@ui.page("/explore")
async def explore_page(request: Request):
    """Parametric measurement viewer — filter-first, model-driven.

    Page handler returns immediately with chrome + skeletons. Schema and
    data queries run off the event loop via run.io_bound. The chart renders
    via ui.timer after the page is connected.

    URL state encodes the full view: each filter facet's selected
    values as repeated query keys (``?part=PN-100&part=PN-200``)
    plus ``y`` / ``x`` / ``chart_type`` / ``group_by`` / ``bins`` /
    ``limit`` / ``since`` / ``until``.
    """
    data_dir = str(resolve_data_dir())
    create_layout("Measurements")

    # Decode URL state — pure dict ops, no queries.
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

    # Fetch schema + counts + smart defaults off the event loop.
    init_result = await run.io_bound(
        _fetch_initial_schema,
        data_dir,
        is_bare_url,
        initial_filters,
        initial_y,
        initial_x,
        initial_chart_type,
        initial_group_by,
    )
    if init_result is None:
        # No measurements yet.
        _render_no_measurements_state()
        return
    if isinstance(init_result, str):
        # Error string
        with page_layout():
            page_header("Measurements", icon="scatter_plot")
            error_container = ui.column().classes("w-full")
            render_empty_card(error_container, "Schema unavailable", init_result)
        return

    (
        y_options,
        x_options,
        group_options,
        initial_y,
        initial_x,
        initial_chart_type,
        initial_group_by,
        initial_filters,
    ) = init_result

    # Mutable state captured by closures.
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
    chart_widget: Any = None
    chart_status: Any = None

    def _new_query() -> MeasurementsQuery:
        return MeasurementsQuery(_data_dir=data_dir)

    def _push_url() -> None:
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

    async def _refresh_string_facets() -> None:
        """Re-populate STRING facet options based on current filter set."""

        def _fetch() -> list[tuple[str, list, list]]:
            results = []
            with _new_query() as q:
                for facet in MEASUREMENT_FACETS:
                    if facet.kind is not FacetKind.STRING:
                        continue
                    if facet_widgets.get(facet.column) is None:
                        continue
                    opts = q.distinct_values(
                        facet.column, filters=state["filter_set"], exclude_self=True
                    )
                    current = state["filter_set"].string_filters.get(facet.column, [])
                    still_valid = [v for v in current if any(o.value == v for o in opts)]
                    results.append((facet.column, opts, still_valid))
            return results

        updates = await run.io_bound(_fetch)
        for column, opts, still_valid in updates:
            widget = facet_widgets.get(column)
            if widget is None:
                continue
            widget.options = {o.value: f"{o.value} ({o.count:,})" for o in opts}
            # Only update value if it actually changed — setting widget.value
            # always fires on_change (even when identical), which would trigger
            # a recursive _refresh_all and prevent the chart from rendering.
            current = state["filter_set"].string_filters.get(column, [])
            if still_valid != current:
                widget.value = still_valid
                state["filter_set"].string_filters[column] = still_valid
            widget.update()

    async def _refresh_cardinality() -> None:
        def _fetch():
            with _new_query() as q:
                return q.summary_counts(filters=state["filter_set"])

        counts = await run.io_bound(_fetch)
        cardinality_label.text = (
            f"{counts.total_rows:,} measurements · "
            f"{counts.distinct_runs:,} runs · "
            f"{counts.distinct_measurements:,} measurement names · "
            f"{counts.distinct_parts:,} parts"
        )

    async def _refresh_chart() -> None:
        # Status (skeleton / empty / error) lives in chart_status; the echart
        # element is persistent and always visible (so ECharts initializes its
        # canvas) — we update its options rather than clear and recreate it
        # (recreating disposes an uninitialized chart on unmount).
        render_skeleton(chart_status, "h-[28rem]")
        y_val = state["y"]
        x_val = state["x"]
        ct = state["chart_type"]
        if not y_val or (not x_val and ct != "histogram"):
            chart_status.clear()
            with chart_status:
                ui.label("Pick a Y and X column").classes("text-slate-500 italic")
            return

        def _fetch():
            with _new_query() as q:
                return q.parametric(
                    y=y_val,
                    x=x_val,
                    filters=state["filter_set"],
                    group_by=state["group_by"] or None,
                    chart_type=ct,
                    bins=state["bins"],
                    limit=state["limit"],
                )

        try:
            rows = await run.io_bound(_fetch)
        except (OSError, ValueError, RuntimeError) as exc:
            chart_status.clear()
            with chart_status:
                ui.label(f"Query failed: {exc}").classes("text-red-600")
                with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                    ui.code(traceback.format_exc()).classes("text-xs")
            return

        option = _build_chart_option([r.model_dump() for r in rows], ct, y_val, x_val)

        # Spec-limit overlay: when a single measurement is scoped and Y is
        # its value, draw the latest run's low/high envelope as step lines
        # tracking X. Off for histogram/bar and for multi-measurement views.
        meas = _single_scoped_measurement(state["filter_set"])
        plotting_value = y_val in ("measurement_value", "value")
        if option is not None and ct in ("scatter", "line") and plotting_value and meas:

            def _fetch_limits() -> list[LimitBandRow]:
                with _new_query() as q:
                    return q.latest_run_limits(x=x_val, filters=state["filter_set"])

            try:
                bounds = await run.io_bound(_fetch_limits)
            except (OSError, ValueError, RuntimeError):
                bounds = []
            _add_limit_series(option, bounds)

        chart_status.clear()
        # ``ui.echart.options`` is read-only — mutate the dict in place and
        # ``update()`` rather than reassigning it.
        chart_widget.options.clear()
        if option is None:
            chart_widget.update()
            with chart_status:
                ui.label("No data matches these selections").classes("text-slate-500 italic")
            return
        chart_widget.options.update(option)
        chart_widget.update()
        ui.timer(0.1, lambda: chart_widget.run_chart_method("resize"), once=True)

    _refreshing: dict[str, bool] = {"active": False}

    async def _refresh_all() -> None:
        if cardinality_label is None or chart_container is None:
            return
        # Gate re-entrant calls: if _refresh_string_facets triggers on_change
        # callbacks that call _refresh_all again, drop them. The in-progress
        # refresh will complete and render the chart.
        if _refreshing["active"]:
            return
        _refreshing["active"] = True
        try:
            _push_url()
            await _refresh_cardinality()
            await _refresh_string_facets()
            await _refresh_chart()
        finally:
            _refreshing["active"] = False

    async def _on_string_facet_change(facet: FacetSpec, e: Any) -> None:
        values = list(e.value or [])
        if values:
            state["filter_set"].string_filters[facet.column] = values
        else:
            state["filter_set"].string_filters.pop(facet.column, None)
        await _refresh_all()

    async def _on_enum_facet_change(facet: FacetSpec, e: Any) -> None:
        values = list(e.value or [])
        if values:
            state["filter_set"].enum_filters[facet.column] = values
        else:
            state["filter_set"].enum_filters.pop(facet.column, None)
        await _refresh_all()

    async def _on_since_change(e: Any) -> None:
        result = _parse_iso_date(e.value)
        if e.value and result is None:
            ui.notify("Invalid date format — use YYYY-MM-DD", type="warning")
            return
        state["filter_set"].since = result
        await _refresh_all()

    async def _on_until_change(e: Any) -> None:
        result = _parse_iso_date(e.value)
        if e.value and result is None:
            ui.notify("Invalid date format — use YYYY-MM-DD", type="warning")
            return
        state["filter_set"].until = result
        await _refresh_all()

    # ── Layout ──────────────────────────────────────────────────────
    with ui.column().classes("w-full p-6 gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("scatter_plot").classes("text-slate-600")
            ui.label("Measurements").classes("text-2xl font-semibold text-slate-700")

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        # FILTER section
        with ui.card().classes("w-full").props('data-testid="explore-filters"'):
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
        with ui.card().classes("w-full").props('data-testid="explore-plot-controls"'):
            ui.label("PLOT").classes("text-xs font-semibold text-slate-500 tracking-wider")
            with ui.row().classes("items-end gap-3 flex-wrap w-full"):

                async def _on_y_change(e: Any) -> None:
                    state["y"] = str(e.value or "")
                    await _refresh_all()

                async def _on_x_change(e: Any) -> None:
                    state["x"] = str(e.value or "")
                    await _refresh_all()

                async def _on_chart_type_change(e: Any) -> None:
                    state["chart_type"] = str(e.value or "scatter")
                    await _refresh_all()

                async def _on_group_by_change(e: Any) -> None:
                    state["group_by"] = str(e.value or "")
                    await _refresh_all()

                async def _on_bins_change(e: Any) -> None:
                    state["bins"] = int(e.value or DEFAULT_BINS)
                    await _refresh_chart()
                    _push_url()

                async def _on_limit_change(e: Any) -> None:
                    state["limit"] = int(e.value or DEFAULT_LIMIT)
                    await _refresh_chart()
                    _push_url()

                ui.select(
                    y_options,
                    value=state["y"],
                    label="Y axis",
                    with_input=True,
                    on_change=_on_y_change,
                ).classes("w-56")
                ui.select(
                    x_options,
                    value=state["x"],
                    label="X axis",
                    with_input=True,
                    on_change=_on_x_change,
                ).classes("w-56")
                ui.select(
                    CHART_TYPES,
                    value=state["chart_type"],
                    label="Chart",
                    on_change=_on_chart_type_change,
                ).classes("w-32")
                ui.select(
                    [""] + group_options,
                    value=state["group_by"],
                    label="Group by",
                    with_input=True,
                    on_change=_on_group_by_change,
                ).classes("w-48")
                ui.number(
                    label="Bins",
                    value=state["bins"],
                    format="%d",
                    min=2,
                    max=200,
                    on_change=_on_bins_change,
                ).classes("w-24")
                ui.number(
                    label="Limit",
                    value=state["limit"],
                    format="%d",
                    min=10,
                    max=100_000,
                    on_change=_on_limit_change,
                ).classes("w-28")
                ui.button("Refresh", icon="refresh", on_click=lambda: _refresh_all()).props(
                    "outline"
                )

        # Chart container — skeleton until first refresh fires.
        chart_container = ui.column().classes("w-full").props('data-testid="explore-chart"')
        with chart_container:
            chart_status = ui.column().classes("w-full")
            chart_widget = ui.echart({}).classes("w-full h-[28rem]")
        render_skeleton(chart_status, "h-[28rem]")

    # First load fires after page renders.
    ui.timer(0.0, _refresh_all, once=True)

    # Live updates on run.ended.
    try:
        event_store = EventStore.get_shared(resolve_data_dir())
        subscribe_with_refresh(event_store, ["run.ended"], _refresh_all)
    except (OSError, RuntimeError) as exc:
        logger.warning("Live updates unavailable: %s", exc)


def _fetch_initial_schema(
    data_dir: str,
    is_bare_url: bool,
    initial_filters: FilterSet,
    initial_y: str,
    initial_x: str,
    initial_chart_type: str,
    initial_group_by: str,
) -> tuple | None | str:
    """Pure data fetch for explore page init. Returns None (no data), str (error), or tuple."""
    try:
        with MeasurementsQuery(_data_dir=data_dir) as q:
            schema = q.describe_columns()
    except FlightPermanentError:
        return None
    except (OSError, ValueError, RuntimeError) as exc:
        return str(exc)

    try:
        with MeasurementsQuery(_data_dir=data_dir) as q:
            initial_counts = q.summary_counts()
    except (OSError, ValueError, RuntimeError):
        initial_counts = None
    if initial_counts is not None and initial_counts.total_rows == 0:
        return None

    y_options, x_options, group_options = _classify_columns(schema)

    if is_bare_url:
        try:
            with MeasurementsQuery(_data_dir=data_dir) as q:
                top_names = q.distinct_values("measurement_name", filters=FilterSet(), limit=20)
        except (OSError, ValueError, RuntimeError, FlightPermanentError):
            top_names = []
        real_names = [o for o in top_names if not o.value.startswith("_")]
        if real_names:
            initial_filters = FilterSet(string_filters={"measurement_name": [real_names[0].value]})
        if not initial_y:
            initial_y = _default_y(y_options)
        if not initial_x:
            initial_x = _default_x(x_options)

    if initial_y not in y_options:
        initial_y = _default_y(y_options)
    if initial_x not in x_options:
        initial_x = _default_x(x_options)
    if initial_chart_type not in CHART_TYPES:
        initial_chart_type = "scatter"
    if initial_group_by and initial_group_by not in group_options:
        initial_group_by = ""

    return (
        y_options,
        x_options,
        group_options,
        initial_y,
        initial_x,
        initial_chart_type,
        initial_group_by,
        initial_filters,
    )


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


def _single_scoped_measurement(filter_set: FilterSet) -> str | None:
    """The lone measurement_name in scope, or None when 0 or many are selected.

    The limit overlay needs exactly one measurement — limits belong to a
    measurement, so a chart mixing several has no single envelope to draw.
    """
    names = filter_set.string_filters.get("measurement_name", [])
    return names[0] if len(names) == 1 else None


# Spec limits draw black/dashed so they read as boundaries, distinct from
# the colored data series.
_LIMIT_COLOR = "#1f2937"


def _add_limit_series(option: dict[str, Any], bounds: list[LimitBandRow]) -> None:
    """Overlay the latest run's low/high envelope as step lines on a scatter/line option.

    Two dashed step series tracking the data's X axis — a staircase when
    the limit is condition-indexed, flat when it doesn't vary. Drawn
    ``silent`` so they don't steal tooltips from the data points.
    """
    sides = (
        ("Limit low", [[_coerce_x(b.x), b.low] for b in bounds if b.low is not None]),
        ("Limit high", [[_coerce_x(b.x), b.high] for b in bounds if b.high is not None]),
    )
    for name, points in sides:
        if not points:
            continue
        option["series"].append(
            {
                "type": "line",
                "name": name,
                "data": points,
                "step": "middle",
                "showSymbol": False,
                "silent": True,
                "lineStyle": {"type": "dashed", "color": _LIMIT_COLOR, "width": 1.5},
                "itemStyle": {"color": _LIMIT_COLOR},
                "z": 1,
            }
        )
        option["legend"]["data"].append(name)


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


def _build_chart_option(  # noqa: PLR0912
    rows: list[dict[str, Any]],
    chart_type: str,
    y_label: str,
    x_label: str,
) -> dict[str, Any] | None:
    """Build an ECharts option dict from rows (long-format → grouped series).

    Returns ``None`` for an empty result so the caller shows an empty state
    rather than swapping out the persistent chart element — recreating the
    ``ui.echart`` each refresh triggers an ECharts dispose-of-uninitialized
    lifecycle error.
    """
    if not rows:
        return None

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

    return option
