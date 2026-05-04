"""Results detail page."""

from typing import Any

from nicegui import ui

from litmus.data.models import RunSummary
from litmus.ui.components.artifact_viewer import list_artifacts, render_artifact_buttons
from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    info_field,
    page_layout,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    aggregate_run_stats,
    get_run_detail,
    get_session_steps,
    list_all_runs,
)


@ui.page("/results/{run_id}")
def result_detail_page(run_id: str):
    """Single result detail page with tabbed interface."""
    run, steps, measurements = get_run_detail(run_id)

    if run:
        create_layout(f"Run {(run.test_run_id or '')[:8]}")
    else:
        create_layout("Run Not Found")

    with page_layout(gap="gap-3"):
        if run:
            _render_run_detail(run_id, run, steps, measurements)
        else:
            _render_not_found()


def _render_run_detail(run_id: str, run: RunSummary, steps: list, measurements: list):
    """Render the run detail view."""
    run_outcome = run.outcome or ""

    stats = aggregate_run_stats(steps, measurements)
    total_measurements = stats["total_measurements"]
    passed_measurements = stats["passed_measurements"]
    failed_measurements = stats["failed_measurements"]
    total_steps = stats["total_steps"]
    failed_steps = stats["failed_steps"]

    # Summary card — sticky so it stays pinned when the active tab's
    # table scrolls. ``z-10`` keeps it above scrolled rows.
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
                # Banner = the values that uniquely identify the
                # artifact under test. Catalog ids (product_id,
                # station_id) live in the Overview "Run Details"
                # card so the banner stays scannable.
                info_field("Part Number", run.dut_part_number or "")
                info_field("Serial", run.dut_serial or "")
                info_field("Hostname", run.station_hostname or "")
                info_field("Project", run.project_name or "")
                info_field("Started", format_datetime(run.started_at))
                info_field("Ended", format_datetime(run.ended_at))

    # Check if this is a multi-slot run (slot_id present in measurements)
    has_slots = any(m.get("slot_id") for m in measurements)
    session_id = run.session_id

    # Tabbed content
    # ``inline-label`` puts icon + label on one line (Quasar's default
    # is stacked, which is bulky); ``no-caps`` keeps the labels in
    # natural case; the ``q-tab`` size override shrinks the icon.
    timeline_tab = None
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

    # ``flex-1 min-h-0`` lets the tab-panels region absorb remaining
    # vertical space so the inner table can be told ``h-full`` and
    # actually have a finite height to scroll within.
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
                # Pull every step row across sibling runs in this session
                # straight from the daemon's typed steps table.
                session_steps = get_session_steps(session_id)
                current_slot_id = next(
                    (m.get("slot_id") for m in measurements if m.get("slot_id")),
                    None,
                )
                gantt_chart = _render_timeline_tab(
                    session_steps,
                    current_slot_id=current_slot_id,
                )

        with ui.tab_panel(history_tab):
            _render_history_tab(run_id, run)

    # ECharts in hidden tabs can't compute layout — resize on tab switch
    if gantt_chart is not None:
        chart_id = gantt_chart.id
        tabs.on_value_change(
            lambda: ui.run_javascript(
                f"setTimeout(() => {{ const el = getElement({chart_id}); "
                f"if (el && el.chart) el.chart.resize(); }}, 100);"
            )
        )

    ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")


def _render_overview_tab(
    total_steps: int,
    failed_steps: int,
    total_meas: int,
    passed_meas: int,
    failed_meas: int,
    *,
    on_show_steps,
    on_show_measurements,
):
    """Render the overview tab — Test + Measurement stat cards side-by-side.

    Cards are clickable: clicking the Test card jumps to the Steps
    tab, clicking the Measurements card jumps to the Measurements
    tab. The hover affordance plus the title link signals it.
    """
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


def _stat_card(value: str, label: str, color_class: str):
    """Render a statistic card (delegates to the design-system primitive)."""
    from litmus.ui.shared.components import stat_card

    stat_card(value, label, color_class)


def _render_steps_tab(steps: list):
    """Render the Steps tab — full step inventory in execution order.

    Sources from the typed ``StepRow`` list returned by
    ``StepsQuery.list_for_run`` so steps without measurements
    (skipped, planned, setup-only) are still represented. Click a
    step to expand its details. The Measurements tab next to this
    one drills into actual measurement values.
    """
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
        data_table(
            columns=columns,
            rows=rows,
            row_key="step_index",
        )


def _render_measurements_tab(run_id: str, measurements: list):
    """Render the measurements tab."""
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
        data_table(
            columns=columns,
            rows=rows,
            row_key="name",
        )

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


def _render_history_tab(run_id: str, run: RunSummary):
    """Render the DUT history tab."""
    dut_serial = run.dut_serial or ""
    all_runs = list_all_runs(limit=100)
    dut_runs = [r for r in all_runs if r.dut_serial == dut_serial and r.test_run_id != run_id]

    if dut_runs:
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
        ui.label(f"No other runs found for DUT: {dut_serial}").classes("text-slate-500 italic")


def _render_timeline_tab(
    steps,
    *,
    current_slot_id: str | None = None,
) -> Any:
    """Render the execution timeline tab for multi-DUT runs.

    ``steps`` is the typed ``list[StepRow]`` returned by
    :func:`get_session_steps` — every step across the session's
    sibling runs.
    """
    from litmus.ui.components.execution_gantt import render_execution_gantt

    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Execution Timeline").classes("font-semibold")
            ui.label(
                "Combined view of all slots in this parallel session. "
                "This run's slot is highlighted."
            ).classes("text-sm text-slate-500")
        with ui.card_section().classes("w-full"):
            return render_execution_gantt(
                steps,
                current_slot_id=current_slot_id,
            )


def _render_not_found():
    """Render run not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Run not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
