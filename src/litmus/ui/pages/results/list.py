"""Results list page."""

import logging

from nicegui import ui

from litmus.ui.shared.components import format_datetime
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import get_recent_runs

logger = logging.getLogger(__name__)


@ui.page("/results")
def results_page():
    """Results listing page."""
    create_layout("Test Results")

    try:
        runs = get_recent_runs(limit=50)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to load results: %s", exc)
        runs = []

    with ui.column().classes("w-full p-6 gap-6"):
        if runs:
            with ui.card().classes("w-full"):
                columns = [
                    {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                    {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
                    {"name": "station", "label": "Station", "field": "station_id", "align": "left"},
                    {
                        "name": "project",
                        "label": "Project",
                        "field": "project_name",
                        "align": "left",
                    },
                    {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
                    {
                        "name": "measurements",
                        "label": "Measurements",
                        "field": "measurements",
                        "align": "center",
                    },
                    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
                ]
                rows = [
                    {
                        "run_id": (r.test_run_id or "")[:8],
                        "full_run_id": r.test_run_id or "",
                        "dut_serial": r.dut_serial or "",
                        "station_id": r.station_id or "",
                        "project_name": r.project_name or "",
                        "started_at": format_datetime(r.started_at),
                        "measurements": f"{r.total_measurements} (0 fail)",
                        "outcome": r.outcome or "",
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
