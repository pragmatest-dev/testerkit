"""Yield & manufacturing metrics page."""

import logging
import traceback
from datetime import UTC
from typing import Any, TypedDict

from fastapi import Request
from nicegui import ui

from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.ui.shared.components import (
    data_table,
    multi_select_filter,
    render_empty_card,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import get_yield_filter_options, load_yield_runs_table

logger = logging.getLogger(__name__)


class MetricsDashboardData(TypedDict):
    fpy: float
    final_yield: float
    total_runs: int
    total_failed: int
    pareto_data: list[dict]
    cpk_data: list[dict]
    trend_data: list[dict]
    time_stats: dict


@ui.page("/metrics")
def metrics_page(
    request: Request,
    lot: str = "",
    since: str = "",
    until: str = "",
    tab: str = "",
    pareto_group: str = "",
):
    """Metrics dashboard — yield, pareto, cpk, retest, time-loss, assets.

    Categorical filters (Phase / Product / Station) support
    multi-select. Multiple values per filter are encoded in the URL
    as repeated query keys: ``?phase=production&phase=qual``. The
    single-value URL form ``?phase=production`` still works (it
    degrades to a one-element list).

    Args:
        request: FastAPI Request — used to read repeated query
            params for the multi-select filters.
        lot: Lot number filter (free text).
        since / until: Date range (YYYY-MM-DD).
        tab: Active tab name (Yield / Pareto / Cpk / Retest /
            Time loss / Assets).
        pareto_group: Pareto group-by lens (product / step /
            measurement) — only meaningful on the Pareto tab.
    """
    # Categorical filters support multi-select — read repeated URL
    # params. Single-value or empty URLs degrade gracefully to ``[]``.
    phase = request.query_params.getlist("phase")
    product = request.query_params.getlist("product")
    station = request.query_params.getlist("station")

    from litmus.data.results_dir import resolve_results_dir

    # Results directory is platform infrastructure, not an operator
    # filter — resolve from project config and never expose it in
    # the UI. Operators don't pick where data lives; admins do, via
    # ``litmus.yaml``.
    results_dir = str(resolve_results_dir())

    create_layout("Metrics")

    # Load initial data to populate dropdowns (reuse for initial render to avoid double-load)
    initial_table = load_yield_runs_table(results_dir)
    if initial_table is None:
        logger.warning("Failed to load initial yield table from %s", results_dir)
    filter_options = get_yield_filter_options(initial_table)
    products = filter_options["products"]
    stations = filter_options["stations"]

    from litmus.ui.shared.components import push_url_state

    # Forward declarations — needed because ``bind_value`` on date
    # pickers fires ``on_change`` synchronously during construction
    # (propagating the initial value), and that callback chain calls
    # ``update_url`` / ``_do_refresh`` before later widgets are built.
    # Pre-declaring as None lets ``update_url`` short-circuit safely
    # during early fires; once construction completes, all are bound.
    phase_filter: Any = None
    product_filter: Any = None
    station_filter: Any = None
    lot_filter: Any = None
    since_filter: Any = None
    until_filter: Any = None
    pareto_group_select: Any = None
    tabs: Any = None

    def update_url():
        """Mirror filter values into the URL via the shared helper.

        Multi-select filters render as repeated query keys
        (``?phase=production&phase=qual``); ``push_url_state``
        handles list values natively.

        Returns early if any widget hasn't been constructed yet —
        ``bind_value`` triggers ``on_change`` synchronously during
        page setup, so this fires before all widgets exist.
        """
        if (
            phase_filter is None
            or product_filter is None
            or station_filter is None
            or lot_filter is None
            or since_filter is None
            or until_filter is None
            or pareto_group_select is None
            or tabs is None
        ):
            return
        push_url_state(
            "/metrics",
            {
                "phase": list(phase_filter.value or []),
                "product": list(product_filter.value or []),
                "station": list(station_filter.value or []),
                "lot": lot_filter.value,
                "since": since_filter.value,
                "until": until_filter.value,
                "tab": tabs.value if tabs.value != "Yield" else "",
                "pareto_group": (
                    pareto_group_select.value if pareto_group_select.value != "product" else ""
                ),
            },
        )

    with ui.column().classes("w-full p-6 gap-6"):
        # Header with filters
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("analytics").classes("text-slate-600")
                ui.label("Metrics").classes("text-2xl font-semibold text-slate-700")

        # Helper to trigger refresh from current filter values (closure captures by reference)
        def _do_refresh():
            update_url()
            # Same guard as ``update_url``: ``bind_value`` may fire
            # ``on_change`` before all widgets are constructed, so
            # short-circuit until everything's wired up.
            if (
                phase_filter is None
                or product_filter is None
                or station_filter is None
                or since_filter is None
                or until_filter is None
                or pareto_group_select is None
            ):
                return
            _refresh_dashboard(
                results_dir,
                list(phase_filter.value or []) or None,
                list(product_filter.value or []) or None,
                list(station_filter.value or []) or None,
                since_filter.value or None,
                until_filter.value or None,
                summary_container,
                pareto_chart_container,
                cpk_table_container,
                trend_chart_container,
                time_stats_container,
                retest_container,
                time_loss_container,
                assets_container,
                pareto_group=pareto_group_select.value or "product",
            )

        # Filters row - use URL params as initial values. All
        # categorical filters go through ``multi_select_filter``
        # (shared component) for consistency across pages: chip
        # display, autocomplete, multi-select.
        with ui.row().classes("gap-4 flex-wrap w-full"):
            # Canonical phases match the ``--test-phase`` CLI option
            # at ``pytest_plugin/hooks.py:pytest_addoption`` — keep
            # this list in sync with that source of truth.
            valid_phases = ["development", "validation", "characterization", "production"]
            initial_phase = [p for p in phase if p in valid_phases]
            phase_filter = multi_select_filter(
                "Phase",
                valid_phases,
                initial_phase,
                on_change=lambda _: _do_refresh(),
                classes="w-56",
                placeholder="All phases (excludes 'development' by default)",
            )

            initial_product = [p for p in product if p in products]
            product_filter = multi_select_filter(
                "Product",
                products,
                initial_product,
                on_change=lambda _: _do_refresh(),
                classes="w-64",
                placeholder="All products",
            )

            initial_station = [s for s in station if s in stations]
            station_filter = multi_select_filter(
                "Station",
                stations,
                initial_station,
                on_change=lambda _: _do_refresh(),
                classes="w-64",
                placeholder="All stations",
            )

            lot_filter = ui.input(
                label="Lot (optional)",
                value=lot,
                placeholder="Leave blank for all",
            ).classes("w-40")

            with ui.input("Since (optional)", value=since).classes("w-40") as since_input:
                with since_input.add_slot("append"):
                    ui.icon("event").on(
                        "click",
                        lambda: since_menu.open(),
                    ).classes("cursor-pointer")
                with ui.menu() as since_menu:
                    since_filter = ui.date(
                        value=since or None,
                        on_change=lambda _: _do_refresh(),
                    ).bind_value(since_input)

            with ui.input("Until (optional)", value=until).classes("w-40") as until_input:
                with until_input.add_slot("append"):
                    ui.icon("event").on(
                        "click",
                        lambda: until_menu.open(),
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

        # Tabs subordinate to filters — each tab is one analytical
        # lens, the filters above apply to whichever tab is active.
        # The active tab + Pareto group-by selection both mirror to
        # the URL so a deep link reopens the same view.
        with ui.tabs(on_change=lambda _: update_url()).classes("w-full") as tabs:
            yield_tab = ui.tab("Yield", icon="check_circle")
            pareto_tab = ui.tab("Pareto", icon="bar_chart")
            cpk_tab = ui.tab("Cpk", icon="show_chart")
            retest_tab = ui.tab("Retest", icon="loop")
            time_loss_tab = ui.tab("Time loss", icon="timer_off")
            assets_tab = ui.tab("Assets", icon="memory")
        tab_lookup = {
            "Yield": yield_tab,
            "Pareto": pareto_tab,
            "Cpk": cpk_tab,
            "Retest": retest_tab,
            "Time loss": time_loss_tab,
            "Assets": assets_tab,
        }
        initial_tab_obj = tab_lookup.get(tab, yield_tab)
        tabs.set_value(initial_tab_obj)
        with ui.tab_panels(tabs, value=initial_tab_obj).classes("w-full"):
            with ui.tab_panel(yield_tab):
                summary_container = ui.row().classes("w-full gap-4")
                trend_chart_container = ui.column().classes("w-full")
                time_stats_container = ui.column().classes("w-full")
            with ui.tab_panel(pareto_tab):
                # Group-by selector — pareto is "show me the top
                # things"; the dimension changes what "things" means.
                # Product (run-level) and Step (step-level) work even
                # without per-measurement data; Measurement (the
                # historical default) needs measurements recorded.
                #
                # Single-select by design: a pareto plots ONE
                # bucketing dimension on the X-axis. The other
                # filters (Phase / Product / Station) are
                # multi-select because they're scope filters, not
                # axis dimensions — one bucketing, many scopes.
                valid_pareto_groups = {"product", "step", "measurement"}
                initial_pareto_group = (
                    pareto_group if pareto_group in valid_pareto_groups else "product"
                )
                pareto_group_select = ui.select(
                    options={
                        "product": "Product (most-failing dut_part_number)",
                        "step": "Step (most-failing step_path)",
                        "measurement": "Measurement (historical: limit-bearing measures)",
                    },
                    value=initial_pareto_group,
                    label="Group by",
                    on_change=lambda _: _do_refresh(),
                ).classes("w-96")
                pareto_chart_container = ui.column().classes("w-full")
            with ui.tab_panel(cpk_tab):
                cpk_table_container = ui.column().classes("w-full")
            with ui.tab_panel(retest_tab):
                retest_container = ui.column().classes("w-full")
            with ui.tab_panel(time_loss_tab):
                time_loss_container = ui.column().classes("w-full")
            with ui.tab_panel(assets_tab):
                assets_container = ui.column().classes("w-full")

    _refresh_dashboard(
        results_dir,
        initial_phase or None,
        initial_product or None,
        initial_station or None,
        since if since else None,
        until if until else None,
        summary_container,
        pareto_chart_container,
        cpk_table_container,
        trend_chart_container,
        time_stats_container,
        retest_container,
        time_loss_container,
        assets_container,
        pareto_group=initial_pareto_group,
    )

    # Subscribe to ``run.ended`` only — yield / pareto / cpk / retest /
    # time-loss / assets are aggregations over **completed** runs.
    # An in-flight run has no outcome and would skew the math; we
    # only refresh once a new run finalizes. (run.started intentionally
    # omitted here.)
    from litmus.data.event_store import EventStore
    from litmus.ui.shared.components import subscribe_with_refresh

    try:
        from pathlib import Path

        event_store = EventStore(_results_dir=Path(results_dir))
        subscribe_with_refresh(
            event_store,
            ["run.ended"],
            _do_refresh,
        )
    except (OSError, RuntimeError) as exc:
        logger.warning("Live updates unavailable: %s", exc)


def _fetch_yield_data(
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> MetricsDashboardData | None:
    """Compute all yield dashboard data (pure — no UI).

    Returns a dict with keys: fpy, final_yield, total_runs, total_failed,
    pareto_data, cpk_data, trend_data, time_stats. Returns None when the
    filters match no data so the caller can render an empty-state.
    """
    with MeasurementsQuery(_results_dir=results_dir) as store:
        summary_rows = store.yield_summary(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
            period="day",
        )
        if not summary_rows:
            return None

        total_runs = sum(r.get("total_runs", 0) for r in summary_rows)
        total_failed = sum(r.get("failed", 0) for r in summary_rows)
        fp_total = sum(r.get("first_pass_total", 0) for r in summary_rows)
        fp_passed = sum(r.get("first_pass_passed", 0) for r in summary_rows)
        final_passed = sum(r.get("final_passed", 0) for r in summary_rows)
        unique_serials = sum(r.get("unique_serials", 0) for r in summary_rows)

        fpy = fp_passed / fp_total if fp_total else 0.0
        final_yield = final_passed / unique_serials if unique_serials else 0.0

        pareto_rows = store.pareto(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
            top_n=10,
        )
        pareto_data = []
        total_fails = sum(r.get("fail_count", 0) for r in pareto_rows)
        cumulative = 0.0
        for r in pareto_rows:
            pct = r["fail_count"] / total_fails * 100 if total_fails else 0
            cumulative += pct
            pareto_data.append(
                {
                    "step_name": r.get("step_name", ""),
                    "measurement_name": r.get("measurement_name", ""),
                    "count": r.get("fail_count", 0),
                    "pct": round(pct, 1),
                    "cumulative_pct": round(cumulative, 1),
                }
            )

        cpk_data = store.cpk(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )

        trend_data = store.trend(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until,
            period="day",
        )

    durations = [r["avg_duration_s"] for r in summary_rows if r.get("avg_duration_s") is not None]
    p95s = [r["p95_duration_s"] for r in summary_rows if r.get("p95_duration_s") is not None]
    time_stats = {
        "avg_s": round(sum(durations) / len(durations), 2) if durations else None,
        "min_s": round(min(durations), 2) if durations else None,
        "max_s": round(max(durations), 2) if durations else None,
        "p95_s": round(max(p95s), 2) if p95s else None,
        "count": total_runs,
    }

    return {
        "fpy": fpy,
        "final_yield": final_yield,
        "total_runs": total_runs,
        "total_failed": total_failed,
        "pareto_data": pareto_data,
        "cpk_data": cpk_data,
        "trend_data": trend_data,
        "time_stats": time_stats,
    }


def _refresh_dashboard(
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
    summary_container,
    pareto_chart_container,
    cpk_table_container,
    trend_chart_container,
    time_stats_container,
    retest_container,
    time_loss_container,
    assets_container,
    *,
    pareto_group: str = "product",
):
    """Refresh all dashboard components via MeasurementsQuery (DuckDB SQL on measurements view)."""
    summary_container.clear()
    pareto_chart_container.clear()
    cpk_table_container.clear()
    trend_chart_container.clear()
    time_stats_container.clear()
    retest_container.clear()
    time_loss_container.clear()
    assets_container.clear()

    # Retest / time-loss / assets are independent lenses — render
    # them regardless of whether the yield path returns data.
    _render_retest_tab(retest_container, results_dir, phase, product, station, since, until)
    _render_time_loss_tab(time_loss_container, results_dir, phase, product, station, since, until)
    _render_assets_tab(assets_container, results_dir, since, until)

    # Pareto tab is also independent — its data source depends on
    # the group-by selection (product / step / measurement), so we
    # query straight from the right Query class instead of riding
    # on the yield_data fetch.
    _render_pareto_tab(
        pareto_chart_container,
        results_dir,
        group_by=pareto_group,
        phase=phase,
        product=product,
        station=station,
        since=since,
        until=until,
    )

    try:
        data = _fetch_yield_data(
            results_dir,
            phase,
            product,
            station,
            since,
            until,
        )
    except (OSError, ValueError, RuntimeError) as e:
        with summary_container:
            ui.label(f"Error loading data: {e}").classes("text-red-600")
            with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                ui.code(traceback.format_exc()).classes("text-xs")
        return

    if data is None:
        # No measurement-level data → fall back to run-level metrics
        # on the Yield tab. The runs daemon has 1 row per run
        # regardless of whether any ``logger.measure()`` ran, so we
        # can always show pass-rate and outcome distribution.
        # Other tabs render their own empty cards so each tab has a
        # named cause + next-step instead of a silent blank panel.
        _render_run_level_fallback(summary_container, results_dir)
        render_empty_card(
            cpk_table_container,
            "Process Capability (Cpk)",
            "No measurements with limits — record values via "
            "``verify(name, value, limit=Limit(...))`` to populate.",
        )
        render_empty_card(
            trend_chart_container,
            "Yield trend",
            "No measurements yet — once tests record values, the trend appears here.",
        )
        render_empty_card(
            time_stats_container,
            "Test time statistics",
            "No measurement-level timing yet.",
        )
        return

    _render_summary_cards(
        summary_container,
        data["fpy"],
        data["final_yield"],
        data["total_runs"],
        data["total_failed"],
    )
    # Pareto is rendered independently above (see ``_render_pareto_tab``)
    # because the data source depends on the group-by selection.
    _render_cpk_table(cpk_table_container, data["cpk_data"])
    _render_trend_chart(trend_chart_container, data["trend_data"])
    _render_time_stats(time_stats_container, data["time_stats"])


def _render_run_level_fallback(summary_container, results_dir: str) -> None:
    """Render run-level metrics from :class:`RunsQuery`.

    Used when no measurement-level data exists. Shows what we can
    compute purely from run rows: total runs, pass rate, errored /
    failed counts. The cards answer "what's the run-level health?";
    the Pareto tab handles the failure-pareto drill-down.
    """
    from litmus.analysis.runs_query import RunsQuery

    try:
        with RunsQuery(_results_dir=results_dir) as q:
            outcomes = q.count_by_outcome()
    except (OSError, ValueError, RuntimeError):
        outcomes = {}

    total = sum(outcomes.values())
    if total == 0:
        with summary_container:
            ui.label("No runs recorded yet.").classes("text-slate-500 italic")
            ui.label(
                "Once a test runs (via ``litmus serve``, ``pytest`` directly, "
                "or any test runner that loads the litmus plugin) it will "
                "appear here."
            ).classes("text-xs text-slate-400")
        return

    passed = outcomes.get("passed", 0)
    failed = outcomes.get("failed", 0)
    errored = outcomes.get("errored", 0)
    pass_rate = (passed / total) * 100 if total else 0.0

    with summary_container:
        _metric_card("Total Runs", str(total), "list_alt", "slate")
        _metric_card("Pass Rate", f"{pass_rate:.1f}%", "check_circle", "green")
        _metric_card("Failed", str(failed), "error", "red")
        _metric_card("Errored", str(errored), "warning", "amber")


def _render_summary_cards(
    container,
    fpy: float,
    final_yield: float,
    total_runs: int,
    total_failures: int,
):
    """Render summary metric cards."""
    container.clear()

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


def _render_pareto_tab(
    container,
    results_dir: str,
    *,
    group_by: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Render the Pareto tab according to the selected group-by lens.

    Three lenses, each backed by a different Query class:

    * ``product`` (default) — :meth:`RunsQuery.failure_pareto` —
      run-level failures grouped by ``dut_part_number``. "Which
      product SKU is hurting yield?"
    * ``step`` — :meth:`StepsQuery.failure_pareto` — step-level
      failures grouped by ``step_path``. "Which test step has the
      most failures across runs?"
    * ``measurement`` — :meth:`MeasurementsQuery.pareto` —
      measurement-level failures (the historical default).
    """
    if group_by == "step":
        _render_step_failure_pareto(container, results_dir, phase, product, station, since, until)
    elif group_by == "measurement":
        _render_measurement_pareto(container, results_dir, phase, product, station, since, until)
    else:  # default: product
        _render_product_failure_pareto(
            container, results_dir, phase, product, station, since, until
        )


def _render_product_failure_pareto(
    container,
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Run-level failure pareto grouped by ``dut_part_number``."""
    from litmus.analysis.runs_query import RunsQuery

    try:
        with RunsQuery(_results_dir=results_dir) as q:
            rows = q.failure_pareto(
                group_by="dut_part_number",
                top_n=15,
                phase=phase,
                product=product,
                station=station,
                since=since,
                until=until,
            )
    except (OSError, ValueError, RuntimeError):
        rows = []
    _render_failure_pareto_chart(
        container,
        rows,
        title="Failing products",
        subtitle="Top 15 ``dut_part_number`` buckets with the most failed/errored runs.",
        bucket_label="product",
    )


def _render_step_failure_pareto(
    container,
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Step-level failure pareto grouped by ``step_path``."""
    from litmus.analysis.steps_query import StepsQuery

    try:
        with StepsQuery(_results_dir=results_dir) as q:
            rows = q.failure_pareto(
                top_n=15,
                phase=phase,
                product=product,
                station=station,
                since=since,
                until=until,
            )
    except (OSError, ValueError, RuntimeError):
        rows = []
    _render_failure_pareto_chart(
        container,
        rows,
        title="Failing steps",
        subtitle="Top 15 ``step_path`` buckets with the most failed/errored steps.",
        bucket_label="step",
    )


def _normalize_measurement_pareto_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Massage ``MeasurementsQuery.pareto`` rows into the shared shape.

    The shared chart renderer expects ``{bucket, failed_count, total,
    fail_rate_pct}``. ``RunsQuery.failure_pareto`` and
    ``StepsQuery.failure_pareto`` already emit that shape; the
    measurement-level pareto returns ``{step_name, measurement_name,
    total_count, fail_count, fail_rate}`` and needs to be unified here.
    """
    return [
        {
            "bucket": f"{r.get('step_name', '')}: {r.get('measurement_name', '')}",
            "failed_count": r.get("fail_count", 0),
            "total": r.get("total_count", 0),
            "fail_rate_pct": r.get("fail_rate"),
        }
        for r in raw
    ]


def _render_measurement_pareto(
    container,
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Historical measurement-level pareto (limit-bearing measurement failures)."""
    try:
        with MeasurementsQuery(_results_dir=results_dir) as q:
            raw = q.pareto(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                top_n=15,
            )
    except (OSError, ValueError, RuntimeError):
        raw = []
    if not raw:
        render_empty_card(
            container,
            "Failure pareto",
            "No measurement-level failures yet. Switch the group-by selector to "
            "Product or Step for run/step-level paretos that work without "
            "limit-bearing measurements.",
        )
        return
    _render_failure_pareto_chart(
        container,
        _normalize_measurement_pareto_rows(raw),
        title="Failing measurements",
        subtitle="Top 15 limit-bearing measurements with the most failures.",
        bucket_label="measurement",
    )


def _render_failure_pareto_chart(
    container,
    rows: list[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
    bucket_label: str,
) -> None:
    """Shared bar+cumulative chart for all failure-pareto lenses."""
    container.clear()
    if not rows:
        render_empty_card(
            container,
            title,
            f"No failed runs/steps recorded — once a {bucket_label} starts "
            "failing it'll show up here.",
        )
        return

    counts = [int(r.get("failed_count") or 0) for r in rows]
    total = sum(counts) or 1
    cumulative: list[float] = []
    running = 0.0
    for c in counts:
        running += c
        cumulative.append(round(running / total * 100, 1))

    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label(title).classes("font-semibold")
            ui.label(subtitle).classes("text-xs text-slate-500")
        ui.echart(
            {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
                "legend": {"data": ["Failures", "Cumulative %"], "top": 0},
                "grid": {"left": 60, "right": 60, "top": 40, "bottom": 110},
                "xAxis": [
                    {
                        "type": "category",
                        "data": [str(r.get("bucket") or "(none)") for r in rows],
                        "axisPointer": {"type": "shadow"},
                        "axisLabel": {"rotate": 30, "interval": 0, "fontSize": 10},
                    }
                ],
                "yAxis": [
                    {"type": "value", "name": "Failures"},
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
                        "name": "Failures",
                        "type": "bar",
                        "data": counts,
                        "itemStyle": {"color": "#ef4444"},
                    },
                    {
                        "name": "Cumulative %",
                        "type": "line",
                        "yAxisIndex": 1,
                        "data": cumulative,
                        "itemStyle": {"color": "#1e293b"},
                        "lineStyle": {"width": 2},
                        "symbol": "circle",
                        "symbolSize": 6,
                    },
                ],
            }
        ).classes("w-full h-96")


def _render_cpk_table(container: Any, cpk_data: list[dict[str, Any]]) -> None:
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

            table = data_table(columns=columns, rows=rows, row_key="measurement")

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


def _render_trend_chart(container: Any, trend_data: list[dict[str, Any]]) -> None:
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


def _render_time_stats(container: Any, time_stats: dict[str, Any]) -> None:
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


def _render_retest_tab(
    container,
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Retest rates: how often DUTs require multiple attempts.

    Wraps :meth:`MeasurementsQuery.retest` (same source the
    ``litmus metrics retest`` CLI uses).
    """
    rows = _safe_metric_query(results_dir, phase, product, station, since, until, "retest")
    if not rows:
        render_empty_card(
            container,
            "Retest rates",
            "No retest data — record DUTs across multiple sessions to populate.",
        )
        return

    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Retest rates").classes("font-semibold")
            ui.label(
                "How often unique DUTs needed more than one attempt to clear "
                "the same step. High retest rates flag flaky tests or marginal "
                "hardware."
            ).classes("text-xs text-slate-500")
        # Bar of retest rate per period — quick scan for trend.
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 50, "right": 30, "top": 30, "bottom": 50},
                "xAxis": {
                    "type": "category",
                    "data": [str(r.get("period", "")) for r in rows],
                    "name": "period",
                    "nameLocation": "middle",
                    "nameGap": 28,
                },
                "yAxis": {
                    "type": "value",
                    "name": "retest %",
                    "axisLabel": {"formatter": "{value}%"},
                },
                "series": [
                    {
                        "type": "bar",
                        "data": [r.get("retest_rate", 0) for r in rows],
                        "itemStyle": {"color": "#f59e0b"},
                    }
                ],
            }
        ).classes("w-full h-72")
        columns = [
            {"name": "period", "label": "Period", "field": "period", "align": "left"},
            {"name": "serials", "label": "Serials", "field": "serials", "align": "right"},
            {"name": "retested", "label": "Retested", "field": "retested", "align": "right"},
            {"name": "rate", "label": "Rate", "field": "rate", "align": "right"},
            {
                "name": "avg_attempts",
                "label": "Avg attempts",
                "field": "avg_attempts",
                "align": "right",
            },
        ]
        table_rows = [
            {
                "id": str(idx),
                "period": str(r.get("period", "")),
                "serials": r.get("total_serials", 0),
                "retested": r.get("retested_count", 0),
                "rate": f"{r.get('retest_rate', 0):.1f}%",
                "avg_attempts": f"{r.get('avg_attempts', 0):.1f}",
            }
            for idx, r in enumerate(rows)
        ]
        data_table(columns=columns, rows=table_rows, row_key="id")


def _render_time_loss_tab(
    container,
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> None:
    """Time lost to failures and errors per period.

    Wraps :meth:`MeasurementsQuery.time_loss` (same source the
    ``litmus metrics time-loss`` CLI uses).
    """
    rows = _safe_metric_query(results_dir, phase, product, station, since, until, "time_loss")
    if not rows:
        render_empty_card(
            container,
            "Time loss",
            "No timing data yet — once tests run with measurements, this populates.",
        )
        return

    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Time loss").classes("font-semibold")
            ui.label(
                "Wall-clock time spent on failed and errored runs per period — "
                "where the rig was busy but produced no good data."
            ).classes("text-xs text-slate-500")
        # Stacked bar: pass / fail / error time per period.
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["pass", "fail", "error"], "top": 0},
                "grid": {"left": 60, "right": 30, "top": 40, "bottom": 50},
                "xAxis": {
                    "type": "category",
                    "data": [str(r.get("period", "")) for r in rows],
                    "name": "period",
                    "nameLocation": "middle",
                    "nameGap": 28,
                },
                "yAxis": {"type": "value", "name": "seconds"},
                "series": [
                    {
                        "name": "pass",
                        "type": "bar",
                        "stack": "time",
                        "itemStyle": {"color": "#10b981"},
                        "data": [r.get("pass_time_s", 0) or 0 for r in rows],
                    },
                    {
                        "name": "fail",
                        "type": "bar",
                        "stack": "time",
                        "itemStyle": {"color": "#ef4444"},
                        "data": [r.get("fail_time_s", 0) or 0 for r in rows],
                    },
                    {
                        "name": "error",
                        "type": "bar",
                        "stack": "time",
                        "itemStyle": {"color": "#f59e0b"},
                        "data": [r.get("error_time_s", 0) or 0 for r in rows],
                    },
                ],
            }
        ).classes("w-full h-72")
        columns = [
            {"name": "period", "label": "Period", "field": "period", "align": "left"},
            {"name": "total", "label": "Total (s)", "field": "total", "align": "right"},
            {"name": "pass", "label": "Pass (s)", "field": "pass_s", "align": "right"},
            {"name": "fail", "label": "Fail (s)", "field": "fail_s", "align": "right"},
            {"name": "error", "label": "Error (s)", "field": "error_s", "align": "right"},
        ]
        table_rows = [
            {
                "id": str(idx),
                "period": str(r.get("period", "")),
                "total": f"{(r.get('total_time_s', 0) or 0):.1f}",
                "pass_s": f"{(r.get('pass_time_s', 0) or 0):.1f}",
                "fail_s": f"{(r.get('fail_time_s', 0) or 0):.1f}",
                "error_s": f"{(r.get('error_time_s', 0) or 0):.1f}",
            }
            for idx, r in enumerate(rows)
        ]
        data_table(columns=columns, rows=table_rows, row_key="id")


def _render_assets_tab(container, results_dir: str, since: str | None, until: str | None) -> None:
    """Per-instrument utilization derived from connect/disconnect events.

    The events daemon stores ``InstrumentConnected`` /
    ``InstrumentDisconnected`` rows with timestamp + role + resource;
    we pair them up to compute time-connected per instrument over
    the window. Phase / product / station filters don't apply here
    (the data is keyed by instrument, not by run context).
    """
    pairs = _compute_instrument_utilization(results_dir, since, until)
    if not pairs:
        render_empty_card(
            container,
            "Asset utilization",
            "No instrument lifecycle events recorded in the selected window. "
            "Pages that drive instruments emit ``InstrumentConnected`` / "
            "``InstrumentDisconnected`` events automatically.",
        )
        return

    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Asset utilization").classes("font-semibold")
            ui.label(
                "Instrument time-connected over the selected window. "
                "Filters Phase / Product / Station don't apply here — "
                "instruments are keyed by role + resource, not by run "
                "context."
            ).classes("text-xs text-slate-500")
        columns = [
            {"name": "role", "label": "Role", "field": "role", "align": "left"},
            {"name": "resource", "label": "Resource", "field": "resource", "align": "left"},
            {"name": "sessions", "label": "Sessions", "field": "sessions", "align": "right"},
            {
                "name": "connected_s",
                "label": "Connected (s)",
                "field": "connected_s",
                "align": "right",
            },
            {"name": "share", "label": "Share", "field": "share", "align": "right"},
        ]
        total = sum(p["connected_s"] for p in pairs) or 1
        table_rows = [
            {
                "id": f"{p['role']}|{p['resource']}",
                "role": p["role"],
                "resource": p["resource"],
                "sessions": p["sessions"],
                "connected_s": f"{p['connected_s']:.1f}",
                "share": f"{p['connected_s'] / total * 100:.1f}%",
            }
            for p in pairs
        ]
        data_table(columns=columns, rows=table_rows, row_key="id")


def _safe_metric_query(
    results_dir: str,
    phase: str | list[str] | None,
    product: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
    method: str,
) -> list[dict]:
    """Run a MeasurementsQuery method, returning [] on any failure.

    Centralizes the boilerplate around `MeasurementsQuery` open/close
    + filter passthrough so the per-tab renderers stay focused on
    presentation.
    """
    try:
        with MeasurementsQuery(_results_dir=results_dir) as store:
            fn = getattr(store, method)
            return fn(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period="day",
            )
    except (OSError, ValueError, RuntimeError):
        return []


def _compute_instrument_utilization(
    results_dir: str,
    since: str | None,
    until: str | None,
) -> list[dict]:
    """Pair connect/disconnect events into per-instrument utilization.

    Reads ``InstrumentConnected`` / ``InstrumentDisconnected`` from
    the events daemon. For each (role, resource) pair, sums the
    duration of every connected interval that overlaps the
    [since, until] window. ``sessions`` is the count of distinct
    sessions that connected this instrument.

    Returns rows ordered by ``connected_s`` descending so the
    busiest instruments float to the top.
    """
    from datetime import datetime as _dt

    from litmus.data.event_store import EventStore
    from litmus.data.results_dir import resolve_results_dir as _resolve

    base = _resolve(results_dir)
    if not (base / "events").exists():
        return []
    since_dt = _dt.fromisoformat(since).replace(tzinfo=UTC) if since else None
    store = EventStore(_results_dir=base)
    try:
        connects = store.events(event_type="instrument.connected", since=since_dt)
        disconnects = store.events(event_type="instrument.disconnected", since=since_dt)
    finally:
        store.close()

    until_dt = _dt.fromisoformat(until).replace(tzinfo=UTC) if until else None

    # Index disconnects by (session, role) so we can pair each
    # connect with the next matching disconnect from the same session.
    disconnect_idx: dict[tuple[str, str], list[_dt]] = {}
    for ev in disconnects:
        sid = str(ev.get("session_id") or "")
        role = str(ev.get("role") or ev.get("instrument_role") or "")
        key = (sid, role)
        ts = ev.get("occurred_at")
        if isinstance(ts, str):
            ts = _dt.fromisoformat(ts)
        if isinstance(ts, _dt):
            disconnect_idx.setdefault(key, []).append(ts)

    accum: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in connects:
        role = str(ev.get("role") or ev.get("instrument_role") or "")
        resource = str(ev.get("resource") or "")
        sid = str(ev.get("session_id") or "")
        ts = ev.get("occurred_at")
        if isinstance(ts, str):
            ts = _dt.fromisoformat(ts)
        if not isinstance(ts, _dt):
            continue
        # First matching disconnect for this session+role; fall back
        # to the until cutoff or now if none arrives (interval still
        # open at query time).
        matches = disconnect_idx.get((sid, role)) or []
        end = next((d for d in matches if d > ts), None)
        if end is None:
            end = until_dt or _dt.now(tz=UTC)
        duration = max(0.0, (end - ts).total_seconds())
        key = (role, resource)
        bucket = accum.setdefault(
            key, {"role": role, "resource": resource, "sessions": set(), "connected_s": 0.0}
        )
        bucket["sessions"].add(sid)
        bucket["connected_s"] += duration

    rows = [
        {
            "role": v["role"],
            "resource": v["resource"],
            "sessions": len(v["sessions"]),
            "connected_s": v["connected_s"],
        }
        for v in accum.values()
    ]
    rows.sort(key=lambda r: r["connected_s"], reverse=True)
    return rows
