"""Results list page."""

from nicegui import ui

from litmus.config.project import load_project_config
from litmus.data.backends.parquet import ParquetBackend
from litmus.ui.shared.components import format_datetime
from litmus.ui.shared.layout import create_layout


@ui.page("/results")
def results_page():
    """Results listing page."""
    create_layout("Test Results")

    backend = ParquetBackend(results_dir=load_project_config().results_dir)
    runs = backend.list_runs(limit=50)

    with ui.column().classes("w-full p-6 gap-6"):
        if runs:
            with ui.card().classes("w-full"):
                columns = [
                    {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                    {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
                    {"name": "station", "label": "Station", "field": "station_id", "align": "left"},
                    {"name": "test", "label": "Test", "field": "test_sequence_id", "align": "left"},
                    {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
                    {
                        "name": "measurements", "label": "Measurements",
                        "field": "measurements", "align": "center",
                    },
                    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
                ]
                rows = [
                    {
                        "run_id": r.get("test_run_id", "")[:8],
                        "full_run_id": r.get("test_run_id", ""),
                        "dut_serial": r.get("dut_serial", ""),
                        "station_id": r.get("station_id", ""),
                        "test_sequence_id": r.get("test_sequence_id", ""),
                        "started_at": format_datetime(r.get("started_at")),
                        "measurements": (
                            f"{r.get('total_measurements', 0)}"
                            f" ({r.get('failed_measurements', 0)} fail)"
                        ),
                        "outcome": r.get("outcome", ""),
                    }
                    for r in runs
                ]
                table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
                table.on(
                    "row-click", lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}")
                )
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No test results found.").classes("text-slate-500")
                ui.button(
                    "Launch a Test", icon="play_arrow", on_click=lambda: ui.navigate.to("/launch")
                ).classes("mt-4")
