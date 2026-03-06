"""Results detail page."""

from nicegui import ui

from litmus.config.project import load_project_config
from litmus.data.backends.parquet import ParquetBackend
from litmus.ui.shared.components import format_datetime
from litmus.ui.shared.layout import create_layout


@ui.page("/results/{run_id}")
def result_detail_page(run_id: str):
    """Single result detail page with tabbed interface."""
    backend = ParquetBackend(results_dir=load_project_config().results_dir)
    run = backend.get_run(run_id)
    measurements = backend.get_measurements(run_id, _file=run.get("_file")) if run else []

    if run:
        create_layout(f"Run {run.get('test_run_id', '')[:8]}")
    else:
        create_layout("Run Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if run:
            _render_run_detail(run_id, run, measurements, backend)
        else:
            _render_not_found()


def _render_run_detail(run_id: str, run: dict, measurements: list, backend):
    """Render the run detail view."""
    run_outcome = run.get("outcome") or ""

    # Calculate stats from measurements
    total_measurements = len(measurements)
    failed_measurements = sum(1 for m in measurements if m.get("outcome") == "fail")
    passed_measurements = sum(1 for m in measurements if m.get("outcome") == "pass")

    # Calculate step stats (a step fails if any measurement in it fails)
    steps: dict[str, str] = {}  # step_name -> worst outcome
    for m in measurements:
        step = m.get("step_name", "")
        meas_outcome = m.get("outcome") or ""
        if step not in steps:
            steps[step] = meas_outcome
        elif meas_outcome == "fail":
            steps[step] = "fail"
        elif meas_outcome == "error" and steps[step] != "fail":
            steps[step] = "error"
    total_steps = len(steps)
    failed_steps = sum(1 for o in steps.values() if o == "fail")

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
                _info_field("DUT Serial", run.get("dut_serial", ""))
                _info_field_link("Station", run.get("station_id", ""), "/stations")
                _info_field_link("Test Sequence", run.get("test_sequence_id", ""), "/sequences")
                _info_field("Started", format_datetime(run.get("started_at")))
                _info_field("Ended", format_datetime(run.get("ended_at")))
                results_summary = (
                    f"{total_steps} steps, {total_measurements} measurements, "
                    f"{failed_measurements} failed"
                )
                _info_field("Results", results_summary)

    # Tabbed content
    with ui.tabs().classes("w-full") as tabs:
        overview_tab = ui.tab("Overview", icon="dashboard")
        measurements_tab = ui.tab("Measurements", icon="science")
        history_tab = ui.tab("DUT History", icon="history")

    with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
        with ui.tab_panel(overview_tab):
            _render_overview_tab(
                total_steps, failed_steps,
                total_measurements, passed_measurements, failed_measurements,
            )

        with ui.tab_panel(measurements_tab):
            _render_measurements_tab(measurements)

        with ui.tab_panel(history_tab):
            _render_history_tab(run_id, run, backend)

    ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")


def _info_field(label: str, value: str):
    """Render an info field."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("font-semibold")


def _info_field_link(label: str, value: str, base_path: str):
    """Render an info field with a link."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        if value:
            ui.link(value, f"{base_path}/{value}").classes(
                "font-semibold text-blue-600 hover:underline"
            )
        else:
            ui.label("-").classes("font-semibold")


def _render_overview_tab(
    total_steps: int, failed_steps: int,
    total_meas: int, passed_meas: int, failed_meas: int,
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
                    "value": f"{m.get('value', '-')} {m.get('units', '')}".strip(),
                    "limits": f"{m.get('low_limit', '')} – {m.get('high_limit', '')}",
                    "outcome": m.get("outcome", ""),
                }
                for m in measurements
            ]
            ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
    else:
        ui.label("No measurements recorded.").classes("text-slate-500 italic")


def _render_history_tab(run_id: str, run: dict, backend):
    """Render the DUT history tab."""
    dut_serial = run.get("dut_serial", "")
    all_runs = backend.list_runs(limit=100)
    dut_runs = [
        r for r in all_runs if r.get("dut_serial") == dut_serial and r.get("test_run_id") != run_id
    ]

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
                    "run_id": r.get("test_run_id", "")[:8],
                    "full_run_id": r.get("test_run_id", ""),
                    "sequence": r.get("test_sequence_id", ""),
                    "started": format_datetime(r.get("started_at")),
                    "outcome": r.get("outcome", ""),
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


def _render_not_found():
    """Render run not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Run not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
