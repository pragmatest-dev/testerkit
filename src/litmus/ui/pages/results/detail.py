"""Results detail page."""

from typing import Any

from nicegui import run, ui

from litmus.data.models import RunSummary
from litmus.ui.components.artifact_viewer import list_artifacts, render_artifact_buttons
from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    info_field,
    page_layout,
    render_skeleton,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    aggregate_run_stats,
    get_run_detail,
    get_session_steps,
    list_all_runs,
)


@ui.page("/results/{run_id}")
async def result_detail_page(run_id: str):
    """Single result detail page with tabbed interface.

    Page handler shows a skeleton immediately, fetches core data off
    the event loop via run.io_bound, then renders the full structure.
    Timeline and DUT History tabs load lazily on first activation.
    """
    create_layout(f"Run {run_id[:8]}")

    with page_layout(gap="gap-3"):
        # Skeleton — visible immediately while core data is fetching.
        loading_container = ui.column().classes("w-full gap-3")
        with loading_container:
            ui.card().classes("w-full h-20 animate-pulse bg-slate-200 rounded")
            ui.card().classes("w-full h-48 animate-pulse bg-slate-200 rounded")

        # Fetch run + steps + measurements off the event loop.
        run_obj, steps, measurements = await run.io_bound(get_run_detail, run_id)

        loading_container.delete()

        if run_obj:
            _render_run_detail(run_id, run_obj, steps, measurements)
        else:
            _render_not_found()


def _render_run_detail(run_id: str, run_obj: RunSummary, steps: list, measurements: list) -> None:
    """Render the run detail view with lazy-loaded Timeline and DUT History tabs."""
    run_outcome = run_obj.outcome or ""

    stats = aggregate_run_stats(steps, measurements)
    total_measurements = stats["total_measurements"]
    passed_measurements = stats["passed_measurements"]
    failed_measurements = stats["failed_measurements"]
    total_steps = stats["total_steps"]
    failed_steps = stats["failed_steps"]

    # Summary card — sticky header
    with ui.card().classes("w-full sticky top-0 z-10"):
        with ui.card_section().classes("py-2 px-3"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-3"):
                    ui.label("Test Run Summary").classes("text-base font-semibold")
                    colors = {
                        "passed": "bg-emerald-100 text-emerald-800",
                        "done": "bg-emerald-100 text-emerald-800",
                        "failed": "bg-red-100 text-red-800",
                        "errored": "bg-amber-100 text-amber-800",
                        "terminated": "bg-amber-100 text-amber-800",
                        "aborted": "bg-red-100 text-red-800",
                        "skipped": "bg-slate-100 text-slate-700",
                    }
                    ui.label(run_outcome.upper()).classes(
                        f"px-2 py-0.5 rounded text-xs font-medium "
                        f"{colors.get(run_outcome, 'bg-slate-100')}"
                    )
                ui.button(
                    "Back",
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to("/results"),
                ).props("flat dense")

        with ui.card_section().classes("py-2 px-3"):
            with ui.row().classes("flex-wrap gap-x-10 gap-y-2 w-full"):
                info_field("Part Number", run_obj.dut_part_number or "")
                info_field("Serial", run_obj.dut_serial or "")
                info_field("Hostname", run_obj.station_hostname or "")
                info_field("Project", run_obj.project_name or "")
                info_field("Started", format_datetime(run_obj.started_at))
                info_field("Ended", format_datetime(run_obj.ended_at))

    has_slots = any(m.get("slot_id") for m in measurements)
    session_id = run_obj.session_id

    timeline_tab: Any = None
    with ui.tabs().props("inline-label no-caps dense").classes("w-full") as tabs:
        overview_tab = ui.tab("Overview", icon="dashboard")
        steps_tab = ui.tab("Steps", icon="list_alt")
        measurements_tab = ui.tab("Measurements", icon="science")
        if has_slots and session_id:
            timeline_tab = ui.tab("Execution Timeline", icon="timeline")
        history_tab = ui.tab("DUT History", icon="history")
    ui.add_css(
        ".q-tab__icon { font-size: 1rem !important; }"
        ".q-tab { min-height: 32px !important; padding: 0 12px !important; }"
    )

    # Per-lazy-tab containers — created in tab panels, filled by loaders below.
    timeline_container: Any = None
    history_container: Any = None

    with ui.tab_panels(tabs, value=overview_tab).classes("w-full flex-1 min-h-0") as tab_panels:
        with ui.tab_panel(overview_tab):
            _render_overview_tab(
                total_steps,
                failed_steps,
                total_measurements,
                passed_measurements,
                failed_measurements,
                on_show_steps=lambda: tab_panels.set_value(steps_tab),
                on_show_measurements=lambda: tab_panels.set_value(measurements_tab),
            )

        with ui.tab_panel(steps_tab):
            _render_steps_tab(steps)

        with ui.tab_panel(measurements_tab):
            _render_measurements_tab(run_id, measurements)

        gantt_chart = None
        if has_slots and timeline_tab is not None and session_id:
            with ui.tab_panel(timeline_tab):
                timeline_container = ui.column().classes("w-full")
                render_skeleton(timeline_container, "h-64")

        with ui.tab_panel(history_tab):
            history_container = ui.column().classes("w-full")
            render_skeleton(history_container, "h-32")

    # ECharts in hidden tabs need a resize on reveal.
    if gantt_chart is not None:
        chart_id = gantt_chart.id
        tabs.on_value_change(
            lambda: ui.run_javascript(
                f"setTimeout(() => {{ const el = getElement({chart_id}); "
                f"if (el && el.chart) el.chart.resize(); }}, 100);"
            )
        )

    # Lazy loaders for expensive secondary tabs. Each fires once when
    # their tab is first activated; subsequent switches are instant.
    timeline_loaded = {"done": False}
    history_loaded = {"done": False}

    async def _load_timeline() -> None:
        if timeline_loaded["done"] or timeline_container is None or not session_id:
            return
        timeline_loaded["done"] = True
        session_steps = await run.io_bound(get_session_steps, session_id)
        current_slot_id = next((m.get("slot_id") for m in measurements if m.get("slot_id")), None)
        nonlocal gantt_chart
        gantt_chart = _render_timeline_tab(
            timeline_container, session_steps, current_slot_id=current_slot_id
        )

    async def _load_history() -> None:
        if history_loaded["done"] or history_container is None:
            return
        history_loaded["done"] = True
        all_runs = await run.io_bound(list_all_runs, 100)
        _render_history_tab(history_container, run_id, run_obj, all_runs)

    async def _on_tab_change(_: Any) -> None:
        active = str(tabs.value or "")
        if active == "Execution Timeline":
            await _load_timeline()
        elif active == "DUT History":
            await _load_history()

    tabs.on_value_change(_on_tab_change)

    # Pre-load History in the background so the first click feels instant.
    # Timeline only loads if the tab exists and is explicitly activated
    # (less common; save the round-trip until needed).
    ui.timer(0.1, _load_history, once=True)

    ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")


def _render_overview_tab(
    total_steps: int,
    failed_steps: int,
    total_meas: int,
    passed_meas: int,
    failed_meas: int,
    *,
    on_show_steps: Any,
    on_show_measurements: Any,
) -> None:
    clickable = "cursor-pointer hover:shadow-md transition-shadow"
    with ui.row().classes("w-full gap-4 items-stretch"):
        with ui.card().classes(f"flex-1 {clickable}").on("click", lambda _: on_show_steps()):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Test Statistics").classes("font-semibold")
                    ui.icon("arrow_forward").classes("text-slate-400 text-sm")
            with ui.card_section():
                with ui.row().classes("gap-8"):
                    _stat_card(str(total_steps), "Steps", "text-slate-700")
                    _stat_card(str(total_steps - failed_steps), "Passed", "text-emerald-600")
                    _stat_card(str(failed_steps), "Failed", "text-red-600")
                    if total_steps > 0:
                        pct = int(((total_steps - failed_steps) / total_steps) * 100)
                        _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")

        with ui.card().classes(f"flex-1 {clickable}").on("click", lambda _: on_show_measurements()):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Measurement Statistics").classes("font-semibold")
                    ui.icon("arrow_forward").classes("text-slate-400 text-sm")
            with ui.card_section():
                with ui.row().classes("gap-8"):
                    _stat_card(str(total_meas), "Measurements", "text-slate-700")
                    _stat_card(str(passed_meas), "Passed", "text-emerald-600")
                    _stat_card(str(failed_meas), "Failed", "text-red-600")
                    if total_meas > 0:
                        pct = int((passed_meas / total_meas) * 100)
                        _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")


def _stat_card(value: str, label: str, color_class: str) -> None:
    from litmus.ui.shared.components import stat_card

    stat_card(value, label, color_class)


def _render_steps_tab(steps: list) -> None:
    if not steps:
        ui.label("No steps recorded.").classes("text-slate-500 italic")
        return

    columns = [
        {"name": "step_index", "label": "#", "field": "step_index", "align": "right"},
        {"name": "step_name", "label": "Step", "field": "step_name", "align": "left"},
        {"name": "step_path", "label": "Path", "field": "step_path", "align": "left"},
        {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
        {"name": "duration_s", "label": "Duration (s)", "field": "duration_s", "align": "right"},
        {
            "name": "measurement_count",
            "label": "Measurements",
            "field": "measurement_count",
            "align": "right",
        },
    ]
    rows = [
        {
            "step_index": s.step_index,
            "step_name": s.step_name or "",
            "step_path": s.step_path or "",
            "outcome": s.outcome or "",
            "duration_s": (f"{s.duration_s:.3f}" if s.duration_s is not None else "—"),
            "measurement_count": s.measurement_count if s.measurement_count is not None else 0,
        }
        for s in steps
    ]
    with ui.card().classes("w-full h-full flex flex-col"):
        with ui.card_section().classes("py-2"):
            ui.label(
                f"{len(steps)} steps in execution order — including skipped, "
                "planned, and setup-only steps."
            ).classes("text-sm text-slate-500")
        data_table(columns=columns, rows=rows, row_key="step_index")


def _render_measurements_tab(run_id: str, measurements: list) -> None:
    if not measurements:
        ui.label("No measurements recorded.").classes("text-slate-500 italic")
        return

    with ui.card().classes("w-full h-full flex flex-col"):
        columns = [
            {"name": "step", "label": "Step", "field": "step_name", "align": "left"},
            {"name": "name", "label": "Measurement", "field": "name", "align": "left"},
            {"name": "value", "label": "Value", "field": "value", "align": "right"},
            {"name": "limits", "label": "Limits", "field": "limits", "align": "center"},
            {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
        ]
        rows = [
            {
                "step_name": m.get("step_name", ""),
                "name": m.get("measurement_name", ""),
                "value": (
                    f"{m.get('value', '-')}{' ' + m.get('units', '') if m.get('units') else ''}"
                ),
                "limits": (
                    f"{m.get('limit_low', '—')} – {m.get('limit_high', '—')}"
                    if m.get("limit_low") is not None or m.get("limit_high") is not None
                    else "—"
                ),
                "outcome": m.get("outcome", ""),
            }
            for m in measurements
        ]
        data_table(columns=columns, rows=rows, row_key="name")

    artifact_rows = [m for m in measurements if list_artifacts(m)]
    if artifact_rows:
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Artifacts").classes("font-semibold")
                ui.label(
                    "Waveforms, screenshots, logs, and other large observations "
                    "captured during this run."
                ).classes("text-sm text-slate-500")
            with ui.card_section().classes("flex flex-col gap-3"):
                for m in artifact_rows:
                    render_artifact_buttons(run_id, m)


def _render_history_tab(
    container: Any,
    run_id: str,
    run_obj: RunSummary,
    all_runs: list,
) -> None:
    """Render DUT history from pre-fetched all_runs list."""
    container.clear()
    dut_serial = run_obj.dut_serial or ""
    dut_runs = [r for r in all_runs if r.dut_serial == dut_serial and r.test_run_id != run_id]

    if dut_runs:
        with container:
            ui.label(f"Other runs for DUT: {dut_serial}").classes("text-sm text-slate-500 mb-2")
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "project", "label": "Project", "field": "project", "align": "left"},
                {"name": "started", "label": "Started", "field": "started", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
            ]
            rows = [
                {
                    "run_id": (r.test_run_id or "")[:8],
                    "full_run_id": r.test_run_id or "",
                    "project": r.project_name or "",
                    "started": format_datetime(r.started_at),
                    "outcome": r.outcome or "",
                }
                for r in dut_runs[:10]
            ]
            data_table(
                columns=columns,
                rows=rows,
                row_key="run_id",
                on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
                time_columns=["started"],
            )
    else:
        with container:
            ui.label(f"No other runs found for DUT: {dut_serial}").classes("text-slate-500 italic")


def _render_timeline_tab(
    container: Any,
    steps: list,
    *,
    current_slot_id: str | None = None,
) -> Any:
    """Render execution timeline into container from pre-fetched steps."""
    from litmus.ui.components.execution_gantt import render_execution_gantt

    container.clear()
    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Execution Timeline").classes("font-semibold")
            ui.label(
                "Combined view of all slots in this parallel session. "
                "This run's slot is highlighted."
            ).classes("text-sm text-slate-500")
        with ui.card_section().classes("w-full"):
            return render_execution_gantt(steps, current_slot_id=current_slot_id)


def _render_not_found() -> None:
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Run not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
