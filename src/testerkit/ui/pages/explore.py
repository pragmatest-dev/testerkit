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
from collections import defaultdict
from datetime import date
from typing import Any

from fastapi import Request
from nicegui import run, ui

from testerkit.analysis.measurement_facets import (
    MEASUREMENT_FACETS,
    ColumnSchema,
    DynamicFieldDescriptor,
    FacetKind,
    FacetSpec,
    FieldRef,
    FieldRole,
    FilterSet,
    LimitBandRow,
)
from testerkit.analysis.measurements_query import MeasurementsQuery
from testerkit.data._flight_errors import FlightPermanentError
from testerkit.data.data_dir import resolve_data_dir
from testerkit.data.event_store import EventStore
from testerkit.ui.shared.components import (
    page_header,
    page_layout,
    push_url_state,
    render_empty_card,
    render_skeleton,
    subscribe_with_refresh,
    utc_date_input,
)
from testerkit.ui.shared.layout import create_layout

logger = logging.getLogger(__name__)

CHART_TYPES = ["scatter", "line", "bar", "histogram"]
DEFAULT_BINS = 30
DEFAULT_LIMIT = 5000

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


# ---------------------------------------------------------------------------
# Selector helpers — map display label ↔ str | FieldRef
# ---------------------------------------------------------------------------


def _selector_label(sel: str | FieldRef) -> str:
    """Display label for a selector."""
    if isinstance(sel, FieldRef):
        return f"{sel.name} ({sel.role.value})"
    return sel


def _build_axis_options(
    schema: ColumnSchema,
) -> tuple[
    dict[str, str | FieldRef],
    dict[str, str | FieldRef],
    dict[str, str | FieldRef],
    dict[str, DynamicFieldDescriptor],
]:
    """Build label→selector maps and a polymorphism index from a ColumnSchema.

    Returns (y_map, x_map, group_map, field_descriptor_index).
    - y_map: numeric-eligible selectors (Y axis)
    - x_map: X-eligible selectors (numeric + str + date)
    - group_map: group_by-eligible selectors (string fixed + any dynamic)
    - field_descriptor_index: label → DynamicFieldDescriptor for value_type picker
    """
    y_map: dict[str, str | FieldRef] = {}
    x_map: dict[str, str | FieldRef] = {}
    group_map: dict[str, str | FieldRef] = {}
    field_desc: dict[str, DynamicFieldDescriptor] = {}

    for col in schema.fixed:
        col_type = (col.column_type or "").upper()
        is_numeric = any(t in col_type for t in _NUMERIC_TYPES)
        is_string = "VARCHAR" in col_type or "CHAR" in col_type
        is_date = "DATE" in col_type or "TIMESTAMP" in col_type
        label = col.name
        if is_numeric:
            y_map[label] = col.name
        if is_numeric or is_string or is_date:
            x_map[label] = col.name
        if is_string:
            group_map[label] = col.name

    for fd in schema.fields:
        label = f"{fd.name} ({fd.role.value})"
        ref = FieldRef(role=fd.role, name=fd.name)
        # All dynamic fields are Y candidates (presume numeric until proven otherwise)
        y_map[label] = ref
        x_map[label] = ref
        group_map[label] = ref
        field_desc[label] = fd

    return y_map, x_map, group_map, field_desc


def _default_y(y_map: dict[str, str | FieldRef]) -> str:
    """Default Y label — measurement_value, then the first measurement-role field."""
    if "measurement_value" in y_map:
        return "measurement_value"
    for label, sel in y_map.items():
        if isinstance(sel, FieldRef) and sel.role is FieldRole.MEASUREMENT:
            return label
    return next(iter(y_map), "")


def _default_x(x_map: dict[str, str | FieldRef]) -> str:
    """Default X label — the per-measurement occurrence ``index``, then
    ``vector_index``, then ``run_started_at``.

    ``index`` is defined for every measurement (0 for a once-per-run
    measurement, 0..N-1 across a sweep or repeats), so it never leaves the
    chart blank the way ``vector_index`` does for non-swept measurements.
    """
    for candidate in ("index", "vector_index", "run_started_at"):
        if candidate in x_map:
            return candidate
    return next(iter(x_map), "")


# ---------------------------------------------------------------------------
# URL encode / decode for selectors
# ---------------------------------------------------------------------------


def _encode_selector_to_url(prefix: str, sel: str | FieldRef) -> dict[str, str]:
    """Encode a selector to flat URL params with the given prefix."""
    if isinstance(sel, FieldRef):
        params: dict[str, str] = {
            f"{prefix}_name": sel.name,
            f"{prefix}_role": sel.role.value,
        }
        if sel.value_type:
            params[f"{prefix}_value_type"] = sel.value_type
        return params
    return {prefix: sel}


def _decode_selector_from_url(
    prefix: str,
    qp: Any,
    label_map: dict[str, str | FieldRef],
) -> str:
    """Decode a selector from URL params, returning the display label."""
    name = qp.get(f"{prefix}_name", "")
    role = qp.get(f"{prefix}_role", "")
    if name and role:
        value_type = qp.get(f"{prefix}_value_type") or None
        ref = FieldRef(role=FieldRole(role), name=name, value_type=value_type)
        expected_label = _selector_label(ref)
        if expected_label in label_map:
            return expected_label
        return ""
    flat = qp.get(prefix, "")
    if flat and flat in label_map:
        return flat
    return ""


# ---------------------------------------------------------------------------
# Empty state + page handler
# ---------------------------------------------------------------------------


def _render_no_measurements_state() -> None:
    """Render the empty state for ``/explore`` when no measurements exist."""
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
                    """from testerkit.models.test_config import Limit\n\n"""
                    """def test_voltage_in_range(verify):\n"""
                    """    verify("vout", 3.3, limit=Limit(low=3.0, high=3.6, unit="V"))</pre>""",
                    sanitize=False,
                )
                ui.label(
                    "Run that with ``testerkit serve`` (or ``pytest`` directly), "
                    "then revisit this page."
                ).classes("text-xs text-slate-500 mt-2")


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

    URL state encodes the full view: filter facets as repeated query keys,
    plus ``y``/``y_name``/``y_role``/``y_value_type``, ``x``/``x_name``/
    ``x_role``/``x_value_type``, ``group_by``/``group_by_name``/
    ``group_by_role``, ``chart_type``, ``bins``, ``limit``, ``since``,
    and ``until``.
    """
    data_dir = str(resolve_data_dir())
    create_layout("Measurements")

    qp = request.query_params
    qd = _query_dict_from_request(request)
    is_bare_url = not qd
    initial_filters = FilterSet.from_url_params(qd)
    initial_chart_type = qp.get("chart_type", "scatter")
    try:
        initial_bins = int(qp.get("bins") or DEFAULT_BINS)
    except ValueError:
        initial_bins = DEFAULT_BINS
    try:
        initial_limit = int(qp.get("limit") or DEFAULT_LIMIT)
    except ValueError:
        initial_limit = DEFAULT_LIMIT

    init_result = await run.io_bound(
        _fetch_initial_schema,
        data_dir,
        is_bare_url,
        initial_filters,
        initial_chart_type,
    )
    if init_result is None:
        _render_no_measurements_state()
        return
    if isinstance(init_result, str):
        with page_layout():
            page_header("Measurements", icon="scatter_plot")
            error_container = ui.column().classes("w-full")
            render_empty_card(error_container, "Schema unavailable", init_result)
        return

    (
        y_map,
        x_map,
        group_map,
        field_desc,
        initial_y_label,
        initial_x_label,
        initial_chart_type,
        initial_filters,
    ) = init_result

    # Decode Y / X / group_by from URL after the maps are known.
    url_y = _decode_selector_from_url("y", qp, y_map) if not is_bare_url else ""
    url_x = _decode_selector_from_url("x", qp, x_map) if not is_bare_url else ""
    url_group = _decode_selector_from_url("group_by", qp, group_map) if not is_bare_url else ""
    initial_y_label = url_y or initial_y_label
    initial_x_label = url_x or initial_x_label
    initial_group_label = url_group

    state: dict[str, Any] = {
        "filter_set": initial_filters,
        "y_label": initial_y_label,
        "x_label": initial_x_label,
        "chart_type": initial_chart_type,
        "group_label": initial_group_label,
        "bins": initial_bins,
        "limit": initial_limit,
        # value_type overrides when user picks from the picker
        "y_value_type": qp.get("y_value_type") or None,
        "x_value_type": qp.get("x_value_type") or None,
    }

    facet_widgets: dict[str, Any] = {}
    cardinality_label: Any = None
    chart_container: Any = None
    chart_widget: Any = None
    chart_status: Any = None
    y_vtype_widget: Any = None
    x_vtype_widget: Any = None

    def _new_query() -> MeasurementsQuery:
        return MeasurementsQuery(_data_dir=data_dir)

    def _effective_selector(
        label: str,
        label_map: dict[str, str | FieldRef],
        value_type_override: str | None,
    ) -> str | FieldRef:
        sel = label_map.get(label, label)
        if isinstance(sel, FieldRef) and value_type_override:
            return FieldRef(role=sel.role, name=sel.name, value_type=value_type_override)
        return sel

    def _push_url() -> None:
        grouped: dict[str, list[str]] = {}
        for key, value in state["filter_set"].to_url_params():
            grouped.setdefault(key, []).append(value)
        params: dict[str, Any] = dict(grouped)

        y_sel = _effective_selector(state["y_label"], y_map, state["y_value_type"])
        x_sel = _effective_selector(state["x_label"], x_map, state["x_value_type"])
        group_sel = _effective_selector(state["group_label"], group_map, None)

        if state["y_label"]:
            params.update(_encode_selector_to_url("y", y_sel))
        if state["x_label"] and state["chart_type"] != "histogram":
            params.update(_encode_selector_to_url("x", x_sel))
        if state["chart_type"] != "scatter":
            params["chart_type"] = state["chart_type"]
        if state["group_label"]:
            params.update(_encode_selector_to_url("group_by", group_sel))
        if state["bins"] != DEFAULT_BINS:
            params["bins"] = str(state["bins"])
        if state["limit"] != DEFAULT_LIMIT:
            params["limit"] = str(state["limit"])
        push_url_state("/explore", params)

    async def _refresh_string_facets() -> None:
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

    def _update_vtype_widgets() -> None:
        """Show/hide value_type pickers based on current Y/X selections."""
        if y_vtype_widget is None or x_vtype_widget is None:
            return
        y_fd = field_desc.get(state["y_label"])
        x_fd = field_desc.get(state["x_label"])
        if y_fd is not None and len(y_fd.value_types) > 1:
            y_vtype_widget.options = y_fd.value_types
            y_vtype_widget.set_visibility(True)
        else:
            y_vtype_widget.set_visibility(False)
            state["y_value_type"] = None
        if x_fd is not None and len(x_fd.value_types) > 1:
            x_vtype_widget.options = x_fd.value_types
            x_vtype_widget.set_visibility(True)
        else:
            x_vtype_widget.set_visibility(False)
            state["x_value_type"] = None

    async def _refresh_chart() -> None:
        render_skeleton(chart_status, "h-[28rem]")
        y_label = state["y_label"]
        x_label = state["x_label"]
        ct = state["chart_type"]
        if not y_label or (not x_label and ct != "histogram"):
            chart_status.clear()
            with chart_status:
                ui.label("Pick a Y and X column").classes("text-slate-500 italic")
            return

        y_sel = _effective_selector(y_label, y_map, state["y_value_type"])
        x_sel = _effective_selector(x_label, x_map, state["x_value_type"])
        group_label = state["group_label"]
        group_sel: str | FieldRef | None = (
            _effective_selector(group_label, group_map, None) if group_label else None
        )

        def _fetch():
            with _new_query() as q:
                if ct == "histogram":
                    return q.histogram(
                        field=y_sel,
                        bins=state["bins"],
                        group_by=group_sel,
                        filters=state["filter_set"],
                    )
                return q.parametric(
                    y=y_sel,
                    x=x_sel,
                    filters=state["filter_set"],
                    group_by=group_sel,
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

        row_dicts = [r.model_dump() for r in rows]
        # Bar: aggregate client-side — average Y per distinct X, per group
        if ct == "bar":
            row_dicts = _aggregate_bar(row_dicts)

        option = _build_chart_option(row_dicts, ct, y_label, x_label)

        meas = _single_scoped_measurement(state["filter_set"])
        plotting_value = y_label == "measurement_value"
        if option is not None and ct in ("scatter", "line") and plotting_value and meas:

            def _fetch_limits() -> list[LimitBandRow]:
                with _new_query() as q:
                    return q.latest_run_limits(x=x_sel, filters=state["filter_set"])

            try:
                bounds = await run.io_bound(_fetch_limits)
            except (OSError, ValueError, RuntimeError):
                bounds = []
            _add_limit_series(option, bounds)

        chart_status.clear()
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
        if _refreshing["active"]:
            return
        _refreshing["active"] = True
        try:
            _push_url()
            _update_vtype_widgets()
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
        # e.args is the UTC string already converted in the browser by
        # utc_date_input's js_handler — Python never calls back to JS for this.
        args = e.args
        utc_str = (args[0] if isinstance(args, list) else args) or ""
        utc_str = str(utc_str) if utc_str is not None else ""
        if utc_str and _parse_iso_date(utc_str) is None:
            ui.notify("Invalid date format — use YYYY-MM-DD", type="warning")
            return
        state["filter_set"].since = _parse_iso_date(utc_str)
        await _refresh_all()

    async def _on_until_change(e: Any) -> None:
        args = e.args
        utc_str = (args[0] if isinstance(args, list) else args) or ""
        utc_str = str(utc_str) if utc_str is not None else ""
        if utc_str and _parse_iso_date(utc_str) is None:
            ui.notify("Invalid date format — use YYYY-MM-DD", type="warning")
            return
        state["filter_set"].until = _parse_iso_date(utc_str)
        await _refresh_all()

    # ── Layout ──────────────────────────────────────────────────────
    with ui.column().classes("w-full p-6 gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("scatter_plot").classes("text-slate-600")
            ui.label("Measurements").classes("text-2xl font-semibold text-slate-700")

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
                    state["y_label"] = str(e.value or "")
                    state["y_value_type"] = None
                    await _refresh_all()

                async def _on_x_change(e: Any) -> None:
                    state["x_label"] = str(e.value or "")
                    state["x_value_type"] = None
                    await _refresh_all()

                async def _on_chart_type_change(e: Any) -> None:
                    state["chart_type"] = str(e.value or "scatter")
                    await _refresh_all()

                async def _on_group_by_change(e: Any) -> None:
                    state["group_label"] = str(e.value or "")
                    await _refresh_all()

                async def _on_bins_change(e: Any) -> None:
                    state["bins"] = int(e.value or DEFAULT_BINS)
                    await _refresh_chart()
                    _push_url()

                async def _on_limit_change(e: Any) -> None:
                    state["limit"] = int(e.value or DEFAULT_LIMIT)
                    await _refresh_chart()
                    _push_url()

                async def _on_y_vtype_change(e: Any) -> None:
                    state["y_value_type"] = str(e.value or "") or None
                    await _refresh_chart()
                    _push_url()

                async def _on_x_vtype_change(e: Any) -> None:
                    state["x_value_type"] = str(e.value or "") or None
                    await _refresh_chart()
                    _push_url()

                ui.select(
                    list(y_map),
                    value=state["y_label"],
                    label="Y axis",
                    with_input=True,
                    on_change=_on_y_change,
                ).classes("w-56")

                # Value-type picker for Y (hidden unless Y is polymorphic)
                y_fd_init = field_desc.get(state["y_label"])
                y_has_multi = y_fd_init is not None and len(y_fd_init.value_types) > 1
                y_vtype_init = y_fd_init.value_types if y_has_multi else []
                y_vtype_widget = (
                    ui.select(
                        y_vtype_init,
                        value=state["y_value_type"],
                        label="Y type",
                        on_change=_on_y_vtype_change,
                    )
                    .classes("w-40")
                    .props("dense outlined")
                )
                y_vtype_widget.set_visibility(bool(y_vtype_init))

                ui.select(
                    list(x_map),
                    value=state["x_label"],
                    label="X axis",
                    with_input=True,
                    on_change=_on_x_change,
                ).classes("w-56")

                # Value-type picker for X (hidden unless X is polymorphic)
                x_fd_init = field_desc.get(state["x_label"])
                x_has_multi = x_fd_init is not None and len(x_fd_init.value_types) > 1
                x_vtype_init = x_fd_init.value_types if x_has_multi else []
                x_vtype_widget = (
                    ui.select(
                        x_vtype_init,
                        value=state["x_value_type"],
                        label="X type",
                        on_change=_on_x_vtype_change,
                    )
                    .classes("w-40")
                    .props("dense outlined")
                )
                x_vtype_widget.set_visibility(bool(x_vtype_init))

                ui.select(
                    CHART_TYPES,
                    value=state["chart_type"],
                    label="Chart",
                    on_change=_on_chart_type_change,
                ).classes("w-32")
                ui.select(
                    [""] + list(group_map),
                    value=state["group_label"],
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

        # Chart container
        chart_container = ui.column().classes("w-full").props('data-testid="explore-chart"')
        with chart_container:
            chart_status = ui.column().classes("w-full")
            chart_widget = ui.echart({}).classes("w-full h-[28rem]")
        render_skeleton(chart_status, "h-[28rem]")

    ui.timer(0.0, _refresh_all, once=True)

    try:
        event_store = EventStore.get_shared(resolve_data_dir())
        subscribe_with_refresh(event_store, ["run.ended"], _refresh_all)
    except (OSError, RuntimeError) as exc:
        logger.warning("Live updates unavailable: %s", exc)


def _fetch_initial_schema(
    data_dir: str,
    is_bare_url: bool,
    initial_filters: FilterSet,
    initial_chart_type: str,
) -> tuple | None | str:
    """Pure data fetch for explore page init. Returns None (no data), str (error), or tuple."""
    try:
        with MeasurementsQuery(_data_dir=data_dir) as q:
            schema: ColumnSchema = q.describe_columns()
            initial_counts = q.summary_counts()
    except FlightPermanentError:
        return None
    except (OSError, ValueError, RuntimeError) as exc:
        return str(exc)

    if initial_counts.total_rows == 0:
        return None

    y_map, x_map, group_map, field_desc = _build_axis_options(schema)

    initial_y_label = _default_y(y_map)
    initial_x_label = _default_x(x_map)

    if is_bare_url:
        try:
            with MeasurementsQuery(_data_dir=data_dir) as q:
                top_names = q.distinct_values("measurement_name", filters=FilterSet(), limit=20)
        except (OSError, ValueError, RuntimeError, FlightPermanentError):
            top_names = []
        real_names = [o for o in top_names if not o.value.startswith("_")]
        if real_names:
            initial_filters = FilterSet(string_filters={"measurement_name": [real_names[0].value]})

    if initial_chart_type not in CHART_TYPES:
        initial_chart_type = "scatter"

    return (
        y_map,
        x_map,
        group_map,
        field_desc,
        initial_y_label,
        initial_x_label,
        initial_chart_type,
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
    """Render one compact facet block and return its primary widget."""
    if facet.kind is FacetKind.DATE:
        with ui.row().classes("gap-2 items-end"):
            since_handle = utc_date_input(
                "Since",
                value=filter_set.since.isoformat() if filter_set.since else None,
                on_change=on_since_change,
                classes="w-36",
            )
            utc_date_input(
                "Until",
                value=filter_set.until.isoformat() if filter_set.until else None,
                on_change=on_until_change,
                classes="w-36",
            )
        return since_handle

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
            # Nest (not sibling .tooltip()) so it anchors implicitly to a
            # DOM parent that is guaranteed mounted — avoids the Quasar
            # "Anchor: target not found" console warning on initial render.
            with sel:
                ui.tooltip(facet.description)
        return sel

    # FacetKind.STRING
    current = filter_set.string_filters.get(facet.column, [])
    sel = (
        ui.select(
            {v: v for v in current},
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
        # Nest (not sibling .tooltip()) so it anchors implicitly to a
        # DOM parent that is guaranteed mounted — avoids the Quasar
        # "Anchor: target not found" console warning on initial render.
        with sel:
            ui.tooltip(facet.description)
    return sel


# ── Chart helpers ──────────────────────────────────────────────────────


def _aggregate_bar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Client-side bar aggregation: average Y per distinct (group, x)."""
    buckets: dict[tuple[str, Any], list[float]] = defaultdict(list)
    for r in rows:
        key = (str(r.get("group", "")), r.get("x"))
        y = r.get("y")
        if y is not None:
            buckets[key].append(float(y))
    result: list[dict[str, Any]] = []
    seen_x: dict[str, list[Any]] = defaultdict(list)
    for (grp, x_val), ys in buckets.items():
        seen_x[grp].append(x_val)
        result.append({"group": grp, "x": x_val, "y": sum(ys) / len(ys)})
    result.sort(key=lambda r: (str(r["group"]), str(r["x"])))
    return result


def _coerce_x(value: Any) -> Any:
    """Convert a Python value to something ECharts can plot."""
    if isinstance(value, datetime.datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, datetime.date):
        return int(
            datetime.datetime.combine(value, datetime.time.min, tzinfo=datetime.UTC).timestamp()
            * 1000
        )
    return value


def _single_scoped_measurement(filter_set: FilterSet) -> str | None:
    """The lone measurement_name in scope, or None."""
    names = filter_set.string_filters.get("measurement_name", [])
    return names[0] if len(names) == 1 else None


_LIMIT_COLOR = "#1f2937"


def _add_limit_series(option: dict[str, Any], bounds: list[LimitBandRow]) -> None:
    """Overlay the latest run's low/high envelope as step lines."""
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
    """Pick the ECharts xAxis type based on the first non-null x value."""
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
    """Standard X-axis with centered name."""
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


def _center_single_value_x(x_axis: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Center the plot when a numeric X spans a single distinct value.

    A value axis with one distinct X (index=0 for a once-per-run measurement,
    a constant condition, a filtered-to-one-value sweep) otherwise pins the
    lone column to an edge. Symmetric ``min``/``max`` padding puts it in the
    middle. General to ANY value-axis X with one distinct value — not specific
    to ``index``. Mutates ``x_axis`` in place; a no-op when X varies or isn't
    numeric.
    """
    if x_axis.get("type") != "value":
        return
    xs = {r.get("x") for r in rows if r.get("x") is not None}
    if len(xs) != 1:
        return
    val = next(iter(xs))
    if val is None:
        return
    try:
        v = float(val)
    except (TypeError, ValueError):
        return
    # Symmetric unit pad → v sits at the exact midpoint (min+max)/2.
    x_axis["min"] = v - 1
    x_axis["max"] = v + 1


def _y_axis_opt(label: str) -> dict[str, Any]:
    """Standard Y-axis with rotated centered name."""
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
            "saveAsImage": {"title": "Save PNG", "name": "testerkit_explore"},
            "dataView": {"title": "View data", "readOnly": True, "lang": ["Data", "Close", ""]},
        },
    }


def _series_label(group_key: str, fallback: str) -> str:
    """Display name for a series."""
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
    """Build an ECharts option dict from rows (long-format → grouped series)."""
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
        x_axis_cfg = _x_axis_opt(x_label, x_type)
        _center_single_value_x(x_axis_cfg, rows)
        option = {
            "tooltip": {"trigger": "item"},
            "legend": {"data": legend_names, "top": 0},
            "toolbox": _toolbox(allow_y_zoom=True),
            "grid": _GRID,
            "xAxis": x_axis_cfg,
            "yAxis": _y_axis_opt(y_label),
            "series": series,
            "dataZoom": _DATA_ZOOM_XY,
        }

    return option
