"""Yield & manufacturing metrics page."""

from nicegui import ui

from litmus.analysis import metrics, query
from litmus.ui.shared.layout import create_layout


@ui.page("/yield")
def yield_page():
    """Yield analytics dashboard."""
    create_layout("Yield Analytics")

    # Load initial data to populate dropdowns (reuse for initial render to avoid double-load)
    try:
        initial_table = query.load_runs("demo/results")
        products = (
            _get_unique_values(initial_table, "dut_part_number")
            or _get_unique_values(initial_table, "product_id")
        )
        stations = (
            _get_unique_values(initial_table, "station_name")
            or _get_unique_values(initial_table, "station_id")
        )
    except Exception:
        initial_table = None
        products = []
        stations = []

    with ui.column().classes("w-full p-6 gap-6"):
        # Header with filters
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("analytics").classes("text-slate-600")
                ui.label("Yield Analytics").classes("text-2xl font-semibold text-slate-700")

        # Helper to trigger refresh from current filter values (closure captures by reference)
        def _do_refresh():
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

        # Filters row
        with ui.row().classes("gap-4 flex-wrap w-full"):
            results_dir_input = ui.input(
                label="Results Directory",
                value="demo/results",
            ).classes("w-64")

            phase_filter = ui.select(
                ["production", "qual", "development", "all"],
                value="production",
                label="Phase",
                on_change=lambda _: _do_refresh(),
            ).classes("w-40")

            product_filter = ui.select(
                ["All"] + products,
                value="All",
                label="Product",
                on_change=lambda _: _do_refresh(),
            ).classes("w-48")

            station_filter = ui.select(
                ["All"] + stations,
                value="All",
                label="Station",
                on_change=lambda _: _do_refresh(),
            ).classes("w-48")

            lot_filter = ui.input(
                label="Lot (optional)",
                placeholder="Leave blank for all",
            ).classes("w-40")

            with ui.input("Since (optional)").classes("w-40") as since_input:
                with since_input.add_slot("append"):
                    ui.icon("event").on(
                        "click", lambda: since_menu.open(),
                    ).classes("cursor-pointer")
                with ui.menu() as since_menu:
                    since_filter = ui.date(
                        on_change=lambda _: _do_refresh(),
                    ).bind_value(since_input)

            with ui.input("Until (optional)").classes("w-40") as until_input:
                with until_input.add_slot("append"):
                    ui.icon("event").on(
                        "click", lambda: until_menu.open(),
                    ).classes("cursor-pointer")
                with ui.menu() as until_menu:
                    until_filter = ui.date(
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
        "demo/results",
        "production",
        None,
        None,
        None,
        None,
        None,
        summary_container,
        pareto_chart_container,
        cpk_table_container,
        trend_chart_container,
        time_stats_container,
        preloaded_table=initial_table,
    )


def _refresh_dashboard(
    results_dir: str,
    phase: str,
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
        if phase != "all":
            table = query.filter_by_phase(table, [phase])

        if product_id:
            table = query.filter_by_product(table, product_id)

        if station_id:
            table = query.filter_by_station(table, station_id)

        if lot:
            table = query.filter_by_lot(table, lot)

        if since or until:
            table = query.filter_by_date_range(table, since=since, until=until)

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
        cpk_data = _calculate_cpk_for_all_measurements(measurements)
        trend_data = metrics.trend_by_period(runs, period="day")
        time_stats = metrics.test_time_stats(runs)

        # Render components
        _render_summary_cards(summary_container, fpy, final_yield, len(runs), measurements)
        _render_pareto_chart(pareto_chart_container, pareto_data)
        _render_cpk_table(cpk_table_container, cpk_data)
        _render_trend_chart(trend_chart_container, trend_data)
        _render_time_stats(time_stats_container, time_stats)

    except Exception as e:
        with summary_container:
            ui.label(f"Error loading data: {e}").classes("text-red-600")
            import traceback

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
        with container:
            with ui.card().classes("w-full"):
                ui.label("Top Failure Modes (Pareto)").classes("text-lg font-semibold mb-4")
                ui.label("No failure data available").classes("text-slate-500 italic")
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


def _calculate_cpk_for_all_measurements(measurements):
    """Calculate Cpk for all measurement types."""
    # Group by measurement_name
    by_name = {}
    for m in measurements:
        name = m.get("measurement_name")
        if name:
            if name not in by_name:
                by_name[name] = []
            by_name[name].append(m)

    cpk_results = []
    for name, meas_list in by_name.items():
        # Extract values and limits
        values = []
        lsl = None
        usl = None

        for m in meas_list:
            val = m.get("value")
            if val is not None:
                values.append(float(val))
            if lsl is None and m.get("low_limit") is not None:
                lsl = float(m.get("low_limit"))
            if usl is None and m.get("high_limit") is not None:
                usl = float(m.get("high_limit"))

        if values and (lsl is not None or usl is not None):
            result = metrics.calculate_cpk(values, lsl, usl, min_samples=10)
            if result:
                result["measurement_name"] = name
                cpk_results.append(result)

    # Sort by cpk descending
    cpk_results.sort(key=lambda x: x.get("cpk") or 0, reverse=True)
    return cpk_results


def _render_cpk_table(container, cpk_data):
    """Render Cpk table with color coding."""
    container.clear()

    if not cpk_data:
        with container:
            with ui.card().classes("w-full h-fit"):
                ui.label("Process Capability (Cpk)").classes("text-lg font-semibold mb-4")
                ui.label("No Cpk data available").classes("text-slate-500 italic")
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
        with container:
            with ui.card().classes("w-full"):
                ui.label("Yield Trend Over Time").classes("text-lg font-semibold mb-4")
                ui.label("No trend data available").classes("text-slate-500 italic")
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
        with container:
            with ui.card().classes("w-full"):
                ui.label("Test Time Statistics").classes("text-lg font-semibold mb-4")
                ui.label("No timing data available").classes("text-slate-500 italic")
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


def _get_unique_values(table, column_name: str) -> list[str]:
    """Extract unique non-null values from a table column."""
    if table.num_rows == 0 or column_name not in table.column_names:
        return []

    import pyarrow.compute as pc

    col = table[column_name]
    # Remove nulls and get unique values
    unique = pc.unique(col)
    values = [str(v) for v in unique.to_pylist() if v is not None]
    return sorted(values)
