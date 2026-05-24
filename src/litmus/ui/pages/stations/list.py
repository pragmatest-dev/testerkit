"""Station list page — table view with run-usage stats."""

from nicegui import ui

from litmus.ui.shared.components import data_table, format_datetime, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_stations, usage_stats_by


@ui.page("/stations")
def stations_page():
    """Stations listing — one row per station + usage stats from runs."""
    create_layout("Stations")

    stations = discover_stations()
    usage = usage_stats_by("station_id")

    with page_layout():
        # data-testid for the screenshot-regeneration script (see
        # scripts/regenerate-ui-screenshots.py MANIFEST).
        with (
            ui.row()
            .classes("items-center justify-between w-full")
            .props('data-testid="stations-header"')
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("settings_input_hdmi").classes("text-slate-600")
                ui.label("Test Stations").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Station",
                icon="add",
                on_click=lambda: ui.navigate.to("/stations/new"),
            ).props("color=primary")

        if not stations:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("settings_input_hdmi").classes("text-4xl text-slate-300")
                ui.label("No stations configured.").classes("text-slate-500 mt-2")
                ui.label("Create a station to define your test equipment setup.").classes(
                    "text-sm text-slate-400"
                )
                ui.button(
                    "Create Station",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/stations/new"),
                ).classes("mt-4")
            return

        columns = [
            {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {"name": "location", "label": "Location", "field": "location", "align": "left"},
            {
                "name": "instruments",
                "label": "Instruments",
                "field": "instruments",
                "align": "right",
            },
            {"name": "runs", "label": "Runs", "field": "runs", "align": "right", "sortable": True},
            {"name": "passed", "label": "Passed", "field": "passed", "align": "right"},
            {"name": "failed", "label": "Failed", "field": "failed", "align": "right"},
            {
                "name": "last_run",
                "label": "Last Run",
                "field": "last_run",
                "align": "left",
                "sortable": True,
            },
        ]
        rows = []
        for station in stations:
            stats = usage.get(station.id, {})
            rows.append(
                {
                    "id": station.id,
                    "name": station.name or "",
                    "location": station.location or "",
                    "instruments": len(station.instruments or {}),
                    "runs": stats.get("runs", 0),
                    "passed": stats.get("passed", 0),
                    "failed": stats.get("failed", 0),
                    "last_run": format_datetime(stats.get("last_run"))
                    if stats.get("last_run")
                    else "—",
                }
            )

        data_table(
            columns=columns,
            rows=rows,
            row_key="id",
            on_row_click=lambda r: ui.navigate.to(f"/stations/{r['id']}"),
            time_columns=["last_run"],
        ).props('data-testid="stations-table"')
