"""Yield & manufacturing metrics page."""

import logging
import traceback

import pyarrow as pa
from nicegui import ui

from litmus.analysis import metrics, query
from litmus.config.project import load_project_config
from litmus.ui.shared.components import render_empty_card
from litmus.ui.shared.layout import create_layout

logger = logging.getLogger(__name__)


@ui.page("/yield")
def yield_page(
    results_dir: str = "",
    phase: str = "production",
    product: str = "",
    station: str = "",
    lot: str = "",
    since: str = "",
    until: str = "",
):
    """Yield analytics dashboard.

    Args:
        results_dir: Results directory path
        phase: Test phase filter (production, qual, development, all)
        product: Product ID filter
        station: Station ID filter
        lot: Lot number filter
        since: Start date filter (YYYY-MM-DD)
        until: End date filter (YYYY-MM-DD)
    """
    if not results_dir:
        from litmus.data.results_dir import resolve_results_dir

        results_dir = str(resolve_results_dir())

    create_layout("Yield Analytics")

    # Load initial data to populate dropdowns (reuse for initial render to avoid double-load)
    try:
        initial_table = query.load_runs(results_dir)
        products = query.get_unique_column_values(initial_table, "dut_part_number")
        if not products:
            products = query.get_unique_column_values(initial_table, "product_id")
        stations = query.get_unique_column_values(initial_table, "station_name")
        if not stations:
            stations = query.get_unique_column_values(initial_table, "station_id")
    except (OSError, pa.ArrowInvalid) as exc:
        logger.warning("Failed to load initial data: %s", exc)
        initial_table = None
        products = []
        stations = []

    def update_url():
        """Update URL with current filter values."""
        params = []
        if results_dir_input.value != results_dir:
            params.append(f"results_dir={results_dir_input.value}")
        if phase_filter.value != "production":
            params.append(f"phase={phase_filter.value}")
        if product_filter.value and product_filter.value != "All":
            params.append(f"product={product_filter.value}")
        if station_filter.value and station_filter.value != "All":
            params.append(f"station={station_filter.value}")
        if lot_filter.value:
            params.append(f"lot={lot_filter.value}")
        if since_filter.value:
            params.append(f"since={since_filter.value}")
        if until_filter.value:
            params.append(f"until={until_filter.value}")
        query_str = "&".join(params)
        new_url = f"/yield{'?' + query_str if query_str else ''}"
        ui.run_javascript(f"history.replaceState(null, '', '{new_url}')")

    with ui.column().classes("w-full p-6 gap-6"):
        # Header with filters
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("analytics").classes("text-slate-600")
                ui.label("Yield Analytics").classes("text-2xl font-semibold text-slate-700")

        # Helper to trigger refresh from current filter values (closure captures by reference)
        def _do_refresh():
            update_url()
            _refresh_dashboard(
                results_dir_input.value,
                phase_filter.value,
                None if product_filter.value == "All" else product_filter.value,
                None if station_filter.value == "All" else station_filter.value,
                lot_filter.value or None,
                since_filter.value or None,
                until_filter.value or None,
                summary_container,
                pareto_chart_container,
                cpk_table_container,
                trend_chart_container,
                time_stats_container,
            )

        # Filters row - use URL params as initial values
        with ui.row().classes("gap-4 flex-wrap w-full"):
            results_dir_input = ui.input(
                label="Results Directory",
                value=results_dir,
            ).classes("w-64")

            valid_phases = ["production", "qual", "development", "all"]
            phase_filter = ui.select(
                valid_phases,
                value=phase if phase in valid_phases else "production",
                label="Phase",
                on_change=lambda _: _do_refresh(),
            ).classes("w-40")

            product_filter = ui.select(
                ["All"] + products,
                value=product if product in products else "All",
                label="Product",
                on_change=lambda _: _do_refresh(),
            ).classes("w-48")

            station_filter = ui.select(
                ["All"] + stations,
                value=station if station in stations else "All",
                label="Station",
                on_change=lambda _: _do_refresh(),
            ).classes("w-48")

            lot_filter = ui.input(
                label="Lot (optional)",
                value=lot,
                placeholder="Leave blank for all",
            ).classes("w-40")

            with ui.input("Since (optional)", value=since).classes("w-40") as since_input:
                with since_input.add_slot("append"):
                    ui.icon("event").on(
                        "click", lambda: since_menu.open(),
                    ).classes("cursor-pointer")
                with ui.menu() as since_menu:
                    since_filter = ui.date(
                        value=since or None,
                        on_change=lambda _: _do_refresh(),
                    ).bind_value(since_input)

            with ui.input("Until (optional)", value=until).classes("w-40") as until_input:
                with until_input.add_slot("append"):
                    ui.icon("event").on(
                        "click", lambda: until_menu.open(),
                    ).classes("cursor-pointer")
                with ui.menu() as until_menu:
                    until_filter = ui.date(
                        value=until or None,
                        on_change=lambda _: _do_refresh(),
                    ).bind_value(until_input)

            ui.button(
                "Refresh",
                icon="refresh",
                on_click=lambda: _do_refresh(),
            ).props("outline")

        # Dashboard containers
        summary_container = ui.row().classes("w-full gap-4")
        pareto_chart_container = ui.column().classes("w-full")
        cpk_table_container = ui.column().classes("w-full")
        trend_chart_container = ui.column().classes("w-full")
        time_stats_container = ui.column().classes("w-full")

    # Initial load (reuse already-loaded table to avoid double-load)
    _refresh_dashboard(
        results_dir,
        phase if phase in ["production", "qual", "development", "all"] else "production",
        product if product else None,
        station if station else None,
        lot if lot else None,
        since if since else None,
        until if until else None,
        summary_container,
        pareto_chart_container,
        cpk_table_container,
        trend_chart_container,
        time_stats_container,
        preloaded_table=initial_table,
    )


def _refresh_dashboard(
    results_dir: str,
    phase: str | None,
    product_id: str | None,
    station_id: str | None,
    lot: str | None,
    since: str | None,
    until: str | None,
    summary_container,
    pareto_chart_container,
    cpk_table_container,
    trend_chart_container,
    time_stats_container,
    preloaded_table=None,
):
    """Refresh all dashboard components."""
    # Clear all containers first
    summary_container.clear()
    pareto_chart_container.clear()
    cpk_table_container.clear()
    trend_chart_container.clear()
    time_stats_container.clear()

    try:
        # Load data (or use preloaded table if provided)
        if preloaded_table is not None:
            table = preloaded_table
        else:
            table = query.load_runs(results_dir)

        if table.num_rows == 0:
            with summary_container:
                ui.label("No data found in results directory").classes(
                    "text-slate-500 italic"
                )
            return

        # Apply filters
        table = query.apply_all_filters(
            table,
            phase=phase,
            product_id=product_id,
            station_id=station_id,
            lot=lot,
            since=since,
            until=until,
        )

        if table.num_rows == 0:
            with summary_container:
                ui.label("No data matches the selected filters").classes(
                    "text-slate-500 italic"
                )
            return

        # Convert to dicts for metrics functions
        runs = query.deduplicate_runs(table)
        measurements = table.to_pylist()

        # Calculate metrics
        fpy = metrics.calculate_fpy(runs)
        final_yield = metrics.calculate_final_yield(runs)
        pareto_data = metrics.pareto_analysis(measurements, top_n=10)
        cpk_data = metrics.calculate_cpk_for_measurements(measurements)
        trend_data = metrics.trend_by_period(runs, period="day")
        time_stats = metrics.timing_stats(runs)

        # Render components
        _render_summary_cards(summary_container, fpy, final_yield, len(runs), measurements)
        _render_pareto_chart(pareto_chart_container, pareto_data)
        _render_cpk_table(cpk_table_container, cpk_data)
        _render_trend_chart(trend_chart_container, trend_data)
        _render_time_stats(time_stats_container, time_stats)

    except (OSError, pa.ArrowInvalid, ValueError, KeyError) as e:
        with summary_container:
            ui.label(f"Error loading data: {e}").classes("text-red-600")
            with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                ui.code(traceback.format_exc()).classes("text-xs")


def _render_summary_cards(container, fpy: float, final_yield: float, total_runs: int, measurements):
    """Render summary metric cards."""
    container.clear()

    total_failures = sum(1 for m in measurements if m.get("outcome") == "fail")

    with container:
        _metric_card("First Pass Yield", f"{fpy * 100:.1f}%", "check_circle", "green")
        _metric_card("Final Yield", f"{final_yield * 100:.1f}%", "verified", "blue")
        _metric_card("Total Runs", str(total_runs), "list_alt", "slate")
        _metric_card("Total Failures", str(total_failures), "error", "red")


def _metric_card(label: str, value: str, icon: str, color: str):
    """Render a metric card."""
    with ui.card().classes("flex-1 min-w-48"):
        with ui.column().classes("gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes(f"text-{color}-500")
                ui.label(label).classes("text-sm text-slate-600")
            ui.label(value).classes("text-3xl font-bold text-slate-800")


def _render_pareto_chart(container, pareto_data):
    """Render Pareto chart (combo: bars + cumulative line)."""
    container.clear()

    if not pareto_data:
        render_empty_card(container, "Top Failure Modes (Pareto)", "No failure data available")
        return

    with container:
        with ui.card().classes("w-full"):
            ui.label("Top Failure Modes (Pareto)").classes("text-lg font-semibold mb-4")

            # Build ECharts option
            categories = [
                f"{item['step_name']}: {item['measurement_name']}" for item in pareto_data
            ]
            counts = [item["count"] for item in pareto_data]
            cumulative = [item["cumulative_pct"] for item in pareto_data]

            option = {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
                "legend": {"data": ["Failure Count", "Cumulative %"]},
                "xAxis": [
                    {
                        "type": "category",
                        "data": categories,
                        "axisPointer": {"type": "shadow"},
                        "axisLabel": {"rotate": 45, "interval": 0},
                    }
                ],
                "yAxis": [
                    {"type": "value", "name": "Count"},
                    {
                        "type": "value",
                        "name": "Cumulative %",
                        "min": 0,
                        "max": 100,
                        "axisLabel": {"formatter": "{value}%"},
                    },
                ],
                "series": [
                    {
                        "name": "Failure Count",
                        "type": "bar",
                        "data": counts,
                        "itemStyle": {"color": "#3b82f6"},
                    },
                    {
                        "name": "Cumulative %",
                        "type": "line",
                        "yAxisIndex": 1,
                        "data": cumulative,
                        "itemStyle": {"color": "#ef4444"},
                        "lineStyle": {"width": 2},
                        "symbol": "circle",
                        "symbolSize": 6,
                    },
                ],
                "grid": {"bottom": 100},
            }

            ui.echart(option).classes("w-full h-80")


def _render_cpk_table(container, cpk_data):
    """Render Cpk table with color coding."""
    container.clear()

    if not cpk_data:
        render_empty_card(container, "Process Capability (Cpk)", "No Cpk data available")
        return

    with container:
        with ui.card().classes("w-full h-fit"):
            ui.label("Process Capability (Cpk)").classes("text-lg font-semibold mb-4")

            columns = [
                {
                    "name": "measurement",
                    "label": "Measurement",
                    "field": "measurement",
                    "align": "left",
                },
                {"name": "cpk", "label": "Cpk", "field": "cpk", "align": "center"},
                {"name": "mean", "label": "Mean", "field": "mean", "align": "center"},
                {"name": "sigma", "label": "σ", "field": "sigma", "align": "center"},
                {"name": "n", "label": "n", "field": "n", "align": "center"},
            ]

            rows = []
            for item in cpk_data[:15]:  # Top 15
                cpk_val = item.get("cpk")
                row = {
                    "measurement": item["measurement_name"],
                    "cpk": f"{cpk_val:.2f}" if cpk_val is not None else "N/A",
                    "mean": f"{(item.get('mean') or 0):.3f}",
                    "sigma": f"{(item.get('sigma') or 0):.3f}",
                    "n": str(item.get("n", 0)),
                }
                rows.append(row)

            table = ui.table(columns=columns, rows=rows, row_key="measurement")
            table.classes("w-full max-h-96")

            # Add custom styling for Cpk values
            table.add_slot(
                "body-cell-cpk",
                r"""
                <q-td :props="props">
                    <q-badge :color="
                        props.value === 'N/A' ? 'grey' :
                        parseFloat(props.value) >= 1.33 ? 'green' :
                        parseFloat(props.value) >= 1.0 ? 'orange' : 'red'
                    ">
                        {{ props.value }}
                    </q-badge>
                </q-td>
                """,
            )


def _render_trend_chart(container, trend_data):
    """Render yield trend over time."""
    container.clear()

    if not trend_data:
        render_empty_card(container, "Yield Trend Over Time", "No trend data available")
        return

    with container:
        with ui.card().classes("w-full"):
            ui.label("Yield Trend Over Time").classes("text-lg font-semibold mb-4")

            dates = [item["period"] for item in trend_data]
            yields = [item["yield_pct"] for item in trend_data]

            option = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": dates},
                "yAxis": {
                    "type": "value",
                    "min": 0,
                    "max": 100,
                    "axisLabel": {"formatter": "{value}%"},
                },
                "series": [
                    {
                        "name": "Yield",
                        "type": "line",
                        "data": yields,
                        "smooth": True,
                        "itemStyle": {"color": "#10b981"},
                        "areaStyle": {"opacity": 0.3},
                    }
                ],
            }

            ui.echart(option).classes("w-full h-64")


def _render_time_stats(container, time_stats):
    """Render test time statistics."""
    container.clear()

    if not time_stats:
        render_empty_card(container, "Test Time Statistics", "No timing data available")
        return

    with container:
        with ui.card().classes("w-full"):
            ui.label("Test Time Statistics").classes("text-lg font-semibold mb-4")

            with ui.row().classes("gap-6 flex-wrap"):
                _time_stat_card("Average", f"{(time_stats.get('avg_s') or 0):.1f}s")
                _time_stat_card("Minimum", f"{(time_stats.get('min_s') or 0):.1f}s")
                _time_stat_card("Maximum", f"{(time_stats.get('max_s') or 0):.1f}s")
                _time_stat_card("P95", f"{(time_stats.get('p95_s') or 0):.1f}s")


def _time_stat_card(label: str, value: str):
    """Render a small time stat card."""
    with ui.card().classes("px-6 py-4"):
        ui.label(label).classes("text-sm text-slate-600")
        ui.label(value).classes("text-xl font-bold text-slate-800 mt-1")


