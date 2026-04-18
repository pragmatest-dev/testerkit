"""Results detail page."""

from typing import Any

from nicegui import ui

from litmus.data.models import RunSummary
from litmus.ui.shared.components import format_datetime, info_field, info_field_link
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    aggregate_run_stats,
    get_run_detail,
    get_session_measurements,
    list_all_runs,
)


@ui.page("/results/{run_id}")
def result_detail_page(run_id: str):
    """Single result detail page with tabbed interface."""
    run, measurements = get_run_detail(run_id)

    if run:
        create_layout(f"Run {(run.test_run_id or '')[:8]}")
    else:
        create_layout("Run Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if run:
            _render_run_detail(run_id, run, measurements)
        else:
            _render_not_found()


def _render_run_detail(run_id: str, run: RunSummary, measurements: list):
    """Render the run detail view."""
    run_outcome = run.outcome or ""

    stats = aggregate_run_stats(measurements)
    total_measurements = stats["total_measurements"]
    passed_measurements = stats["passed_measurements"]
    failed_measurements = stats["failed_measurements"]
    total_steps = stats["total_steps"]
    failed_steps = stats["failed_steps"]

    # Summary card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Test Run Summary").classes("text-lg font-semibold")
                    colors = {
                        "pass": "bg-emerald-100 text-emerald-800",
                        "fail": "bg-red-100 text-red-800",
                    }
                    ui.label(run_outcome.upper()).classes(
                        f"px-3 py-1 rounded text-sm font-medium "
                        f"{colors.get(run_outcome, 'bg-slate-100')}"
                    )
                ui.button(
                    "Back",
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to("/results"),
                ).props("flat")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                info_field("DUT Serial", run.dut_serial or "")
                info_field_link("Station", run.station_id or "", "/stations")
                info_field_link("Test Sequence", run.test_sequence_id or "", "/sequences")
                info_field("Started", format_datetime(run.started_at))
                info_field("Ended", format_datetime(run.ended_at))
                results_summary = (
                    f"{total_steps} steps, {total_measurements} measurements, "
                    f"{failed_measurements} failed"
                )
                info_field("Results", results_summary)

    # Check if this is a multi-slot run (slot_id present in measurements)
    has_slots = any(m.get("slot_id") for m in measurements)
    session_id = run.session_id

    # Tabbed content
    timeline_tab = None
    with ui.tabs().classes("w-full") as tabs:
        overview_tab = ui.tab("Overview", icon="dashboard")
        measurements_tab = ui.tab("Measurements", icon="science")
        if has_slots and session_id:
            timeline_tab = ui.tab("Execution Timeline", icon="timeline")
        history_tab = ui.tab("DUT History", icon="history")

    with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
        with ui.tab_panel(overview_tab):
            _render_overview_tab(
                total_steps,
                failed_steps,
                total_measurements,
                passed_measurements,
                failed_measurements,
            )

        with ui.tab_panel(measurements_tab):
            _render_measurements_tab(measurements)

        gantt_chart = None
        if has_slots and timeline_tab is not None and session_id:
            with ui.tab_panel(timeline_tab):
                # Load measurements from ALL sibling runs in the same session
                session_measurements = get_session_measurements(session_id)
                # Identify which slot this run belongs to
                current_slot_id = next(
                    (m.get("slot_id") for m in measurements if m.get("slot_id")),
                    None,
                )
                gantt_chart = _render_timeline_tab(
                    session_measurements,
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
):
    """Render the overview tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Test Statistics").classes("font-semibold")
        with ui.card_section():
            with ui.row().classes("gap-8"):
                _stat_card(str(total_steps), "Steps", "text-slate-700")
                _stat_card(str(total_steps - failed_steps), "Passed", "text-emerald-600")
                _stat_card(str(failed_steps), "Failed", "text-red-600")
                if total_steps > 0:
                    pct = int(((total_steps - failed_steps) / total_steps) * 100)
                    _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")

    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Measurement Statistics").classes("font-semibold")
        with ui.card_section():
            with ui.row().classes("gap-8"):
                _stat_card(str(total_meas), "Measurements", "text-slate-700")
                _stat_card(str(passed_meas), "Passed", "text-emerald-600")
                _stat_card(str(failed_meas), "Failed", "text-red-600")
                if total_meas > 0:
                    pct = int((passed_meas / total_meas) * 100)
                    _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")


def _stat_card(value: str, label: str, color_class: str):
    """Render a statistic card."""
    with ui.column().classes("items-center"):
        ui.label(value).classes(f"text-3xl font-bold {color_class}")
        ui.label(label).classes("text-sm text-slate-500")


def _render_measurements_tab(measurements: list):
    """Render the measurements tab."""
    if measurements:
        with ui.card().classes("w-full"):
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
                        f"{m.get('low_limit', '—')} – {m.get('high_limit', '—')}"
                        if m.get("low_limit") is not None or m.get("high_limit") is not None
                        else "—"
                    ),
                    "outcome": m.get("outcome", ""),
                }
                for m in measurements
            ]
            ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
    else:
        ui.label("No measurements recorded.").classes("text-slate-500 italic")


def _render_history_tab(run_id: str, run: RunSummary):
    """Render the DUT history tab."""
    dut_serial = run.dut_serial or ""
    all_runs = list_all_runs(limit=100)
    dut_runs = [r for r in all_runs if r.dut_serial == dut_serial and r.test_run_id != run_id]

    if dut_runs:
        with ui.card().classes("w-full"):
            ui.label(f"Other runs for DUT: {dut_serial}").classes("text-sm text-slate-500 mb-2")
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "sequence", "label": "Sequence", "field": "sequence", "align": "left"},
                {"name": "started", "label": "Started", "field": "started", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
            ]
            rows = [
                {
                    "run_id": (r.test_run_id or "")[:8],
                    "full_run_id": r.test_run_id or "",
                    "sequence": r.test_sequence_id or "",
                    "started": format_datetime(r.started_at),
                    "outcome": r.outcome or "",
                }
                for r in dut_runs[:10]
            ]
            table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
            table.on(
                "row-click",
                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
            )
    else:
        ui.label(f"No other runs found for DUT: {dut_serial}").classes("text-slate-500 italic")


def _render_timeline_tab(
    measurements: list,
    *,
    current_slot_id: str | None = None,
) -> Any:
    """Render the execution timeline tab for multi-DUT runs."""
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
                measurements,
                current_slot_id=current_slot_id,
            )


def _render_not_found():
    """Render run not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Run not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
