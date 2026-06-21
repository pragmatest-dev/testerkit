"""Yield & manufacturing metrics page."""

import logging
import traceback
from datetime import UTC, date, timedelta
from typing import Any, TypedDict

from fastapi import Request
from nicegui import run, ui

from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.runs_query import RunsQuery
from litmus.analysis.steps_query import StepsQuery
from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    data_table,
    multi_select_filter,
    push_url_state,
    render_empty_card,
    render_skeleton,
    subscribe_with_refresh,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import get_runs_filter_options

logger = logging.getLogger(__name__)


class MetricsDashboardData(TypedDict):
    fpy: float
    final_yield: float
    total_runs: int
    total_failed: int
    pareto_data: list[dict]
    cpk_data: list[Any]
    trend_data: list[Any]
    time_stats: dict[str, Any]


@ui.page("/metrics")
async def metrics_page(
    request: Request,
    lot: str = "",
    since: str = "",
    until: str = "",
    tab: str = "",
    pareto_group: str = "",
):
    """Metrics dashboard — yield, pareto, cpk, retest, time-loss, assets.

    Page handler returns immediately with chrome + skeleton placeholders.
    Per-tab data loads asynchronously via ui.timer after the page paints.
    Only the active tab fetches; switching tabs loads that tab on demand.

    Args:
        request: FastAPI Request — used to read repeated query params.
        lot: Lot number filter (free text).
        since / until: Date range (YYYY-MM-DD).
        tab: Active tab name (Yield / Pareto / Cpk / Retest /
            Time loss / Assets).
        pareto_group: Pareto group-by lens (part / step /
            measurement) — only meaningful on the Pareto tab.
    """
    phase = request.query_params.getlist("phase")
    part = request.query_params.getlist("part")
    station = request.query_params.getlist("station")

    # Metrics are per-phase. Default to production — the phase yield / Cpk /
    # pareto are actually meaningful for (development is mock/dirty-git data;
    # characterization deliberately drives out-of-spec) — and to a recent
    # window rather than all of history. Both stay overridable via filters/URL.
    if not phase:
        phase = ["production"]
    if not since:
        since = (date.today() - timedelta(days=30)).isoformat()

    data_dir = str(resolve_data_dir())

    create_layout("Metrics")

    # Indexed distincts — ~50ms, fast enough to do before chrome.
    filter_options = await run.io_bound(get_runs_filter_options)
    parts = filter_options.get("uut_part_number", [])
    stations = filter_options.get("station_hostname", [])

    # ---------------------------------------------------------------------------
    # Forward-declare ALL mutable state first. Python 3.13 raises
    # NameError if a closure references a name that hasn't been assigned
    # yet in the enclosing scope — even if the closure is only *called*
    # after assignment. Forward-declaring to None satisfies the binder
    # while the closures below guard against None at call time.
    # ---------------------------------------------------------------------------
    phase_filter: Any = None
    part_filter: Any = None
    station_filter: Any = None
    lot_filter: Any = None
    since_filter: Any = None
    until_filter: Any = None
    pareto_group_select: Any = None
    tabs: Any = None
    summary_container: Any = None
    trend_chart_container: Any = None
    time_stats_container: Any = None
    pareto_chart_container: Any = None
    cpk_table_container: Any = None
    retest_container: Any = None
    time_loss_container: Any = None
    assets_container: Any = None

    valid_pareto_groups = {"part", "step", "measurement"}
    initial_pareto_group = pareto_group if pareto_group in valid_pareto_groups else "part"

    loaded_tabs: set[str] = set()
    filters_sig: dict[str, Any] = {"value": None}

    def update_url() -> None:
        if (
            phase_filter is None
            or part_filter is None
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
                "part": list(part_filter.value or []),
                "station": list(station_filter.value or []),
                "lot": lot_filter.value,
                "since": since_filter.value,
                "until": until_filter.value,
                "tab": tabs.value if tabs.value != "Yield" else "",
                "pareto_group": (
                    pareto_group_select.value if pareto_group_select.value != "part" else ""
                ),
            },
        )

    def _current_filters() -> tuple:
        return (
            tuple(sorted(phase_filter.value or [])),
            tuple(sorted(part_filter.value or [])),
            tuple(sorted(station_filter.value or [])),
            lot_filter.value or "",
            since_filter.value or "",
            until_filter.value or "",
            pareto_group_select.value or "part",
        )

    def _filter_args() -> tuple:
        return (
            list(phase_filter.value or []) or None,
            list(part_filter.value or []) or None,
            list(station_filter.value or []) or None,
            since_filter.value or None,
            until_filter.value or None,
        )

    # ---------------------------------------------------------------------------
    # Per-tab lazy loaders — defined before chrome so event handlers can
    # reference them safely. Each guards against None containers (the
    # containers are assigned inside the chrome-building block below).
    # ---------------------------------------------------------------------------

    async def _load_yield() -> None:
        if summary_container is None:
            return
        render_skeleton(summary_container, "h-24")
        render_skeleton(trend_chart_container, "h-64")
        render_skeleton(time_stats_container, "h-32")
        phase_, part_, station_, since_, until_ = _filter_args()
        try:
            data = await run.io_bound(
                _fetch_yield_data, data_dir, phase_, part_, station_, since_, until_
            )
        except (OSError, ValueError, RuntimeError) as exc:
            summary_container.clear()
            with summary_container:
                ui.label(f"Error loading data: {exc}").classes("text-red-600")
                with ui.expansion("Stack trace", icon="bug_report").classes("w-full"):
                    ui.code(traceback.format_exc()).classes("text-xs")
            return

        if data is None:
            outcomes = await run.io_bound(_fetch_run_level_counts, data_dir)
            summary_container.clear()
            _render_run_level_fallback_body(summary_container, outcomes)
            phase_label = ", ".join(phase_) if phase_ else "production"
            scope_msg = (
                f"No {phase_label} runs in the selected window. Metrics are "
                "per-phase — development is mock / dirty-git data and is "
                "excluded here. Change the Phase filter (e.g. development) or "
                "widen the date range."
            )
            render_empty_card(trend_chart_container, "Yield trend", scope_msg)
            render_empty_card(
                time_stats_container,
                "Test time statistics",
                scope_msg,
            )
            return

        _render_summary_cards(
            summary_container,
            data["fpy"],
            data["final_yield"],
            data["total_runs"],
            data["total_failed"],
        )
        _render_trend_chart(trend_chart_container, data["trend_data"])
        _render_time_stats(time_stats_container, data["time_stats"])

    async def _load_cpk() -> None:
        if cpk_table_container is None:
            return
        render_skeleton(cpk_table_container, "h-48")
        phase_, part_, station_, since_, until_ = _filter_args()
        data = await run.io_bound(
            _fetch_yield_data, data_dir, phase_, part_, station_, since_, until_
        )
        if data is None:
            render_empty_card(
                cpk_table_container,
                "Process Capability (Cpk)",
                "No measurements with limits — record values via "
                "``verify(name, value, limit=Limit(...))`` to populate.",
            )
            return
        _render_cpk_table(cpk_table_container, data["cpk_data"])

    async def _load_pareto() -> None:
        if pareto_chart_container is None or pareto_group_select is None:
            return
        render_skeleton(pareto_chart_container, "h-64")
        phase_, part_, station_, since_, until_ = _filter_args()
        group_by = pareto_group_select.value or "part"
        rows, title, subtitle, bucket_label = await run.io_bound(
            _fetch_pareto_data, data_dir, group_by, phase_, part_, station_, since_, until_
        )
        _render_failure_pareto_chart(
            pareto_chart_container,
            rows,
            title=title,
            subtitle=subtitle,
            bucket_label=bucket_label,
        )

    async def _load_retest() -> None:
        if retest_container is None:
            return
        render_skeleton(retest_container, "h-48")
        phase_, part_, station_, since_, until_ = _filter_args()
        rows = await run.io_bound(
            _safe_metric_query, data_dir, phase_, part_, station_, since_, until_, "retest"
        )
        _render_retest_body(retest_container, rows)

    async def _load_time_loss() -> None:
        if time_loss_container is None:
            return
        render_skeleton(time_loss_container, "h-48")
        phase_, part_, station_, since_, until_ = _filter_args()
        rows = await run.io_bound(
            _safe_metric_query,
            data_dir,
            phase_,
            part_,
            station_,
            since_,
            until_,
            "time_loss",
        )
        _render_time_loss_body(time_loss_container, rows)

    async def _load_assets() -> None:
        if assets_container is None:
            return
        render_skeleton(assets_container, "h-32")
        _, _, _, since_, until_ = _filter_args()
        pairs = await run.io_bound(_compute_instrument_utilization, data_dir, since_, until_)
        _render_assets_body(assets_container, pairs)

    _TAB_LOADERS: dict[str, Any] = {}

    async def _load_active_tab() -> None:
        if any(
            f is None
            for f in (
                phase_filter,
                part_filter,
                station_filter,
                lot_filter,
                since_filter,
                until_filter,
                pareto_group_select,
                tabs,
            )
        ):
            return
        sig = _current_filters()
        if sig != filters_sig["value"]:
            loaded_tabs.clear()
            filters_sig["value"] = sig
        active = str(tabs.value or "Yield")
        if active in loaded_tabs:
            return
        loaded_tabs.add(active)
        loader = _TAB_LOADERS.get(active)
        if loader:
            await loader()

    # Populated after all loaders are defined so the dict captures the
    # correct closure references. Tab name strings match ui.tab(name) calls.
    _TAB_LOADERS.update(
        {
            "Yield": _load_yield,
            "Pareto": _load_pareto,
            "Cpk": _load_cpk,
            "Retest": _load_retest,
            "Time loss": _load_time_loss,
            "Assets": _load_assets,
        }
    )

    async def _do_refresh() -> None:
        update_url()
        await _load_active_tab()

    # Event handlers — async def so NiceGUI preserves client/slot context.
    # Passing lambda-returning-coroutine to on_change causes NiceGUI to
    # schedule the coroutine as a bare Task without client context, which
    # breaks ui.run_javascript (used by push_url_state). Passing async def
    # directly makes NiceGUI wrap it with context before scheduling.
    async def _on_filter_change(_: Any = None) -> None:
        await _do_refresh()

    async def _on_tab_change(_: Any) -> None:
        update_url()
        await _load_active_tab()

    # ---------------------------------------------------------------------------
    # Build chrome. Container variables are assigned here; closures above
    # see the real values on the next call (Python closure cell semantics).
    # ---------------------------------------------------------------------------

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("analytics").classes("text-slate-600")
                ui.label("Metrics").classes("text-2xl font-semibold text-slate-700")

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        with ui.row().classes("gap-4 flex-wrap w-full").props('data-testid="metrics-filters"'):
            valid_phases = ["development", "validation", "characterization", "production"]
            initial_phase = [p for p in phase if p in valid_phases]
            phase_filter = multi_select_filter(
                "Phase",
                valid_phases,
                initial_phase,
                on_change=_on_filter_change,
                classes="w-56",
                placeholder="Phase (defaults to production)",
            )

            initial_part = [p for p in part if p in parts]
            part_filter = multi_select_filter(
                "Part",
                parts,
                initial_part,
                on_change=_on_filter_change,
                classes="w-64",
                placeholder="All parts",
            )

            initial_station = [s for s in station if s in stations]
            station_filter = multi_select_filter(
                "Station",
                stations,
                initial_station,
                on_change=_on_filter_change,
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
                    ui.icon("event").on("click", lambda: since_menu.open()).classes(
                        "cursor-pointer"
                    )
                with ui.menu() as since_menu:
                    since_filter = ui.date(
                        value=since or None,
                        on_change=_on_filter_change,
                    ).bind_value(since_input)

            with ui.input("Until (optional)", value=until).classes("w-40") as until_input:
                with until_input.add_slot("append"):
                    ui.icon("event").on("click", lambda: until_menu.open()).classes(
                        "cursor-pointer"
                    )
                with ui.menu() as until_menu:
                    until_filter = ui.date(
                        value=until or None,
                        on_change=_on_filter_change,
                    ).bind_value(until_input)

            ui.button("Refresh", icon="refresh", on_click=_on_filter_change).props("outline")

        with ui.tabs(on_change=_on_tab_change).classes("w-full") as tabs:
            yield_tab = ui.tab("Yield", icon="check_circle")
            pareto_tab = ui.tab("Pareto", icon="bar_chart")
            cpk_tab = ui.tab("Cpk", icon="show_chart")
            retest_tab = ui.tab("Retest", icon="loop")
            time_loss_tab = ui.tab("Time loss", icon="timer_off")
            assets_tab = ui.tab("Assets", icon="memory")

        valid_tab_names = {"Yield", "Pareto", "Cpk", "Retest", "Time loss", "Assets"}
        initial_tab_name = tab if tab in valid_tab_names else "Yield"
        tabs.set_value(initial_tab_name)

        with ui.tab_panels(tabs, value=initial_tab_name).classes("w-full"):
            with ui.tab_panel(yield_tab).props('data-testid="metrics-yield"'):
                summary_container = ui.row().classes("w-full gap-4")
                trend_chart_container = ui.column().classes("w-full")
                time_stats_container = ui.column().classes("w-full")
            with ui.tab_panel(pareto_tab).props('data-testid="metrics-pareto"'):
                pareto_group_select = ui.select(
                    options={
                        "part": "Part (most-failing uut_part_number)",
                        "step": "Step (most-failing step_path)",
                        "measurement": "Measurement (historical: limit-bearing measures)",
                    },
                    value=initial_pareto_group,
                    label="Group by",
                    on_change=_on_filter_change,
                ).classes("w-96")
                pareto_chart_container = ui.column().classes("w-full")
            with ui.tab_panel(cpk_tab).props('data-testid="metrics-cpk"'):
                cpk_table_container = ui.column().classes("w-full")
            with ui.tab_panel(retest_tab).props('data-testid="metrics-retest"'):
                retest_container = ui.column().classes("w-full")
            with ui.tab_panel(time_loss_tab).props('data-testid="metrics-time-loss"'):
                time_loss_container = ui.column().classes("w-full")
            with ui.tab_panel(assets_tab).props('data-testid="metrics-assets"'):
                assets_container = ui.column().classes("w-full")

    # Render skeleton only for the ACTIVE tab's containers. Non-active tab
    # panels are hidden by CSS — rendering skeletons in them would add
    # invisible height that causes spurious scrollbars.
    _skeleton_map = {
        "Yield": [
            (summary_container, "h-16"),
            (trend_chart_container, "h-48"),
            (time_stats_container, "h-20"),
        ],
        "Pareto": [(pareto_chart_container, "h-48")],
        "Cpk": [(cpk_table_container, "h-48")],
        "Retest": [(retest_container, "h-48")],
        "Time loss": [(time_loss_container, "h-48")],
        "Assets": [(assets_container, "h-32")],
    }
    for container, height in _skeleton_map.get(initial_tab_name, _skeleton_map["Yield"]):
        render_skeleton(container, height)

    # First data load fires after the page renders.
    ui.timer(0.0, _load_active_tab, once=True)

    # Live updates: only run.ended matters — aggregations are over completed runs.
    try:
        event_store = EventStore.get_shared(resolve_data_dir())
        subscribe_with_refresh(event_store, ["run.ended"], _do_refresh)
    except (OSError, RuntimeError) as exc:
        logger.warning("Live updates unavailable: %s", exc)


# ---------------------------------------------------------------------------
# Pure data-fetch functions — no UI; safe to call via run.io_bound
# ---------------------------------------------------------------------------


def _fetch_yield_data(
    data_dir: str,
    phase: str | list[str] | None,
    part: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> MetricsDashboardData | None:
    """Compute all yield dashboard data. Returns None when filters match no data."""
    with MeasurementsQuery(_data_dir=data_dir) as store:
        summary_rows = store.yield_summary(
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
            period="day",
        )
        if not summary_rows:
            return None

        total_runs = sum(r.total_runs for r in summary_rows)
        total_failed = sum(r.failed for r in summary_rows)
        fp_total = sum(r.first_pass_total for r in summary_rows)
        fp_passed = sum(r.first_pass_passed for r in summary_rows)
        final_passed = sum(r.final_passed for r in summary_rows)
        unique_serials = sum(r.unique_serials for r in summary_rows)

        fpy = fp_passed / fp_total if fp_total else 0.0
        final_yield = final_passed / unique_serials if unique_serials else 0.0

        pareto_rows = store.pareto(
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
            top_n=10,
        )
        pareto_data = []
        total_fails = sum(r.fail_count for r in pareto_rows)
        cumulative = 0.0
        for r in pareto_rows:
            pct = r.fail_count / total_fails * 100 if total_fails else 0
            cumulative += pct
            pareto_data.append(
                {
                    "step_name": r.step_name or "",
                    "measurement_name": r.measurement_name or "",
                    "count": r.fail_count,
                    "pct": round(pct, 1),
                    "cumulative_pct": round(cumulative, 1),
                }
            )

        cpk_data = store.cpk(
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
        )

        trend_data = store.trend(
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
            period="day",
        )

    durations = [r.avg_duration_s for r in summary_rows if r.avg_duration_s is not None]
    p95s = [r.p95_duration_s for r in summary_rows if r.p95_duration_s is not None]
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


def _fetch_run_level_counts(data_dir: str) -> dict[str, int]:
    """Return outcome → count dict from RunsQuery. Empty dict on error."""
    try:
        with RunsQuery(_data_dir=data_dir) as q:
            return q.count_by_outcome()
    except (OSError, ValueError, RuntimeError):
        return {}


def _fetch_pareto_data(
    data_dir: str,
    group_by: str,
    phase: str | list[str] | None,
    part: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
) -> tuple[list[dict[str, Any]], str, str, str]:
    """Fetch pareto rows for the selected lens. Returns (rows, title, subtitle, bucket_label).

    All three lenses return rows in the shared shape expected by
    ``_render_failure_pareto_chart``: ``{bucket, failed_count, total,
    fail_rate_pct}``. Measurement rows are normalized here.
    """
    if group_by == "step":
        try:
            with StepsQuery(_data_dir=data_dir) as q:
                rows = q.pareto(
                    top_n=15,
                    phase=phase,
                    part=part,
                    station=station,
                    since=since,
                    until=until,
                )
        except (OSError, ValueError, RuntimeError):
            rows = []
        return (
            rows,
            "Failing steps",
            "Top 15 ``step_path`` buckets with the most failed/errored steps.",
            "step",
        )
    elif group_by == "measurement":
        try:
            with MeasurementsQuery(_data_dir=data_dir) as q:
                raw = q.pareto(
                    part=part,
                    station=station,
                    phase=phase,
                    since=since,
                    until=until,
                    top_n=15,
                )
        except (OSError, ValueError, RuntimeError):
            raw = []
        return (
            _normalize_measurement_pareto_rows(raw),
            "Failing measurements",
            "Top 15 limit-bearing measurements with the most failures.",
            "measurement",
        )
    else:  # part (default)
        try:
            with RunsQuery(_data_dir=data_dir) as q:
                rows = q.pareto(
                    group_by="uut_part_number",
                    top_n=15,
                    phase=phase,
                    part=part,
                    station=station,
                    since=since,
                    until=until,
                )
        except (OSError, ValueError, RuntimeError):
            rows = []
        return (
            rows,
            "Failing parts",
            "Top 15 ``uut_part_number`` buckets with the most failed/errored runs.",
            "part",
        )


def _safe_metric_query(
    data_dir: str,
    phase: str | list[str] | None,
    part: str | list[str] | None,
    station: str | list[str] | None,
    since: str | None,
    until: str | None,
    method: str,
) -> list[Any]:
    """Run a MeasurementsQuery method, returning [] on any failure."""
    try:
        with MeasurementsQuery(_data_dir=data_dir) as store:
            fn = getattr(store, method)
            return fn(
                part=part,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period="day",
            )
    except (OSError, ValueError, RuntimeError):
        return []


def _compute_instrument_utilization(
    data_dir: str,
    since: str | None,
    until: str | None,
) -> list[dict]:
    """Pair connect/disconnect events into per-instrument utilization rows."""
    from datetime import datetime as _dt

    base = resolve_data_dir(data_dir)
    if not (base / "events").exists():
        return []
    since_dt = _dt.fromisoformat(since).replace(tzinfo=UTC) if since else None
    store = EventStore(_data_dir=base)
    try:
        connects = store.events(event_type="instrument.connected", since=since_dt)
        disconnects = store.events(event_type="instrument.disconnected", since=since_dt)
    finally:
        store.close()

    until_dt = _dt.fromisoformat(until).replace(tzinfo=UTC) if until else None

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


# ---------------------------------------------------------------------------
# Pure render functions — UI only; no data fetches; all run on event loop
# ---------------------------------------------------------------------------


def _render_run_level_fallback_body(
    container: Any,
    outcomes: dict[str, int],
) -> None:
    """Render run-level outcome cards from pre-fetched outcome counts."""
    container.clear()
    total = sum(outcomes.values())
    if total == 0:
        with container:
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

    with container:
        _metric_card("Total Runs", str(total), "list_alt", "slate")
        _metric_card("Pass Rate", f"{pass_rate:.1f}%", "check_circle", "green")
        _metric_card("Failed", str(failed), "error", "red")
        _metric_card("Errored", str(errored), "warning", "amber")


def _render_summary_cards(
    container: Any,
    fpy: float,
    final_yield: float,
    total_runs: int,
    total_failures: int,
) -> None:
    """Render summary metric cards."""
    container.clear()
    with container:
        _metric_card("First Pass Yield", f"{fpy * 100:.1f}%", "check_circle", "green")
        _metric_card("Final Yield", f"{final_yield * 100:.1f}%", "verified", "blue")
        _metric_card("Total Runs", str(total_runs), "list_alt", "slate")
        _metric_card("Total Failures", str(total_failures), "error", "red")


def _metric_card(label: str, value: str, icon: str, color: str) -> None:
    with ui.card().classes("flex-1 min-w-48"):
        with ui.column().classes("gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes(f"text-{color}-500")
                ui.label(label).classes("text-sm text-slate-600")
            ui.label(value).classes("text-3xl font-bold text-slate-800")


def _normalize_measurement_pareto_rows(raw: list[Any]) -> list[dict[str, Any]]:
    """Normalize MeasurementsQuery.pareto rows to the shared failure-pareto shape."""
    return [
        {
            "bucket": f"{r.step_name or ''}: {r.measurement_name or ''}",
            "failed_count": r.fail_count,
            "total": r.total_count,
            "fail_rate_pct": r.fail_rate,
        }
        for r in raw
    ]


def _render_failure_pareto_chart(
    container: Any,
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


def _render_cpk_table(container: Any, cpk_data: list[Any]) -> None:
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
            for item in cpk_data[:15]:
                cpk_val = item.cpk
                row = {
                    "measurement": item.measurement_name,
                    "cpk": f"{cpk_val:.2f}" if cpk_val is not None else "N/A",
                    "mean": f"{(item.mean or 0):.3f}",
                    "sigma": f"{(item.sigma or 0):.3f}",
                    "n": str(item.n),
                }
                rows.append(row)

            table = data_table(columns=columns, rows=rows, row_key="measurement")
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


def _render_trend_chart(container: Any, trend_data: list[Any]) -> None:
    """Render yield trend over time."""
    container.clear()

    if not trend_data:
        render_empty_card(container, "Yield Trend Over Time", "No trend data available")
        return

    with container:
        with ui.card().classes("w-full"):
            ui.label("Yield Trend Over Time").classes("text-lg font-semibold mb-4")

            dates = [str(item.period) for item in trend_data]
            yields = [item.yield_pct for item in trend_data]

            ui.echart(
                {
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
            ).classes("w-full h-64")


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
                avg_s = time_stats.get("avg_s")
                min_s = time_stats.get("min_s")
                max_s = time_stats.get("max_s")
                p95_s = time_stats.get("p95_s")
                _time_stat_card("Average", f"{avg_s:.1f}s" if avg_s is not None else "N/A")
                _time_stat_card("Minimum", f"{min_s:.1f}s" if min_s is not None else "N/A")
                _time_stat_card("Maximum", f"{max_s:.1f}s" if max_s is not None else "N/A")
                _time_stat_card("P95", f"{p95_s:.1f}s" if p95_s is not None else "N/A")


def _time_stat_card(label: str, value: str) -> None:
    with ui.card().classes("px-6 py-4"):
        ui.label(label).classes("text-sm text-slate-600")
        ui.label(value).classes("text-xl font-bold text-slate-800 mt-1")


def _render_retest_body(container: Any, rows: list[Any]) -> None:
    """Render the Retest tab from pre-fetched rows."""
    container.clear()
    if not rows:
        render_empty_card(
            container,
            "Retest rates",
            "No retest data — record UUTs across multiple sessions to populate.",
        )
        return

    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Retest rates").classes("font-semibold")
            ui.label(
                "How often unique UUTs needed more than one attempt to clear "
                "the same step. High retest rates flag flaky tests or marginal "
                "hardware."
            ).classes("text-xs text-slate-500")
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 50, "right": 30, "top": 30, "bottom": 50},
                "xAxis": {
                    "type": "category",
                    "data": [str(r.period) for r in rows],
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
                        "data": [r.retest_rate or 0 for r in rows],
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
                "name": "avg_retries",
                "label": "Avg retries",
                "field": "avg_retries",
                "align": "right",
            },
        ]
        data_table(
            columns=columns,
            rows=[
                {
                    "id": str(idx),
                    "period": str(r.period),
                    "serials": r.total_serials,
                    "retested": r.retested_count,
                    "rate": f"{r.retest_rate or 0:.1f}%",
                    "avg_retries": f"{r.avg_retries or 0:.2f}",
                }
                for idx, r in enumerate(rows)
            ],
            row_key="id",
        )


def _render_time_loss_body(container: Any, rows: list[Any]) -> None:
    """Render the Time-loss tab from pre-fetched rows."""
    container.clear()
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
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["pass", "fail", "error"], "top": 0},
                "grid": {"left": 60, "right": 30, "top": 40, "bottom": 50},
                "xAxis": {
                    "type": "category",
                    "data": [str(r.period) for r in rows],
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
                        "data": [r.pass_time_s or 0 for r in rows],
                    },
                    {
                        "name": "fail",
                        "type": "bar",
                        "stack": "time",
                        "itemStyle": {"color": "#ef4444"},
                        "data": [r.fail_time_s or 0 for r in rows],
                    },
                    {
                        "name": "error",
                        "type": "bar",
                        "stack": "time",
                        "itemStyle": {"color": "#f59e0b"},
                        "data": [r.error_time_s or 0 for r in rows],
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
        data_table(
            columns=columns,
            rows=[
                {
                    "id": str(idx),
                    "period": str(r.period),
                    "total": f"{(r.total_time_s or 0):.1f}",
                    "pass_s": f"{(r.pass_time_s or 0):.1f}",
                    "fail_s": f"{(r.fail_time_s or 0):.1f}",
                    "error_s": f"{(r.error_time_s or 0):.1f}",
                }
                for idx, r in enumerate(rows)
            ],
            row_key="id",
        )


def _render_assets_body(container: Any, pairs: list[dict[str, Any]]) -> None:
    """Render the Assets tab from pre-fetched instrument utilization pairs."""
    container.clear()
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
                "Filters Phase / Part / Station don't apply here — "
                "instruments are keyed by role + resource, not by run context."
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
        data_table(
            columns=columns,
            rows=[
                {
                    "id": f"{p['role']}|{p['resource']}",
                    "role": p["role"],
                    "resource": p["resource"],
                    "sessions": p["sessions"],
                    "connected_s": f"{p['connected_s']:.1f}",
                    "share": f"{p['connected_s'] / total * 100:.1f}%",
                }
                for p in pairs
            ],
            row_key="id",
        )
