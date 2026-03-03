"""Dashboard page."""

from nicegui import ui

from litmus.data.backends.parquet import ParquetBackend
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_stations


def format_datetime(dt):
    """Format datetime for display."""
    if not dt:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)[:16] if dt else ""


@ui.page("/")
def dashboard_page():
    """Main dashboard page."""
    create_layout("Dashboard")

    stations = discover_stations()
    backend = ParquetBackend(results_dir="results")
    runs = backend.list_runs(limit=10)

    if not stations and not runs:
        _getting_started_card()
        return

    with ui.column().classes("w-full p-6 gap-6"):
        # Stations section
        with ui.row().classes("items-center gap-2"):
            ui.icon("memory").classes("text-slate-600")
            ui.label("Stations").classes("text-lg font-semibold text-slate-700")

        if stations:
            with ui.row().classes("gap-4 flex-wrap"):
                for station in stations:
                    _station_card(station)
        else:
            ui.label("No stations configured.").classes("text-slate-500 italic")

        # Recent runs section
        with ui.row().classes("items-center gap-2 mt-4"):
            ui.icon("history").classes("text-slate-600")
            ui.label("Recent Runs").classes("text-lg font-semibold text-slate-700")

        _render_recent_runs(runs)


def _station_card(station):
    """Render a station card."""
    with ui.card().classes("w-80"):
        with ui.row().classes("items-start justify-between"):
            ui.label(station.name or station.id).classes("text-lg font-semibold")
            ui.badge("Ready", color="green").props("outline")
        ui.label(station.description or "").classes("text-sm text-slate-600 mt-1")
        with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
            with ui.row().classes("items-center gap-1"):
                ui.icon("tag", size="xs")
                ui.label(station.id)
            with ui.row().classes("items-center gap-1"):
                ui.icon("location_on", size="xs")
                ui.label(station.location or "")
        ui.button(
            "Start Test",
            icon="play_arrow",
            on_click=lambda _, s=station: ui.navigate.to(f"/launch?station={s.id}"),
        ).classes("mt-4 w-full").props("outline")


def _getting_started_card():
    """Render onboarding card for empty projects."""
    with ui.column().classes("w-full p-6 gap-6"):
        with ui.card().classes("w-full max-w-2xl mx-auto"):
            ui.label("Getting Started").classes("text-2xl font-bold text-slate-800")
            ui.label("Set up your first test bench in three steps.").classes(
                "text-slate-600 mt-1"
            )

            with ui.column().classes("gap-4 mt-6"):
                # Step 1
                with ui.row().classes("items-start gap-3"):
                    ui.badge("1", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Create a station").classes("font-semibold")
                        ui.label(
                            "Define which instruments are at your bench."
                        ).classes("text-sm text-slate-600")
                        with ui.row().classes("gap-2 mt-1"):
                            ui.button(
                                "New Station",
                                icon="add",
                                on_click=lambda: ui.navigate.to("/stations/new"),
                            ).props("outline size=sm")
                        ui.label("or from CLI: litmus station init").classes(
                            "text-xs text-slate-400 font-mono"
                        )

                # Step 2
                with ui.row().classes("items-start gap-3"):
                    ui.badge("2", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Write a test").classes("font-semibold")
                        ui.label("Scaffold a test file with instrument fixtures.").classes(
                            "text-sm text-slate-600"
                        )
                        ui.label("litmus new-test <name>").classes(
                            "text-xs text-slate-400 font-mono mt-1"
                        )

                # Step 3
                with ui.row().classes("items-start gap-3"):
                    ui.badge("3", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Run it").classes("font-semibold")
                        ui.label("Run with mock instruments first, then real hardware.").classes(
                            "text-sm text-slate-600"
                        )
                        ui.label("pytest --mock-instruments").classes(
                            "text-xs text-slate-400 font-mono mt-1"
                        )

            ui.separator().classes("mt-4")
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.icon("lightbulb", size="sm").classes("text-amber-500")
                ui.label("Or start with a full example:").classes(
                    "text-sm text-slate-600"
                )
                ui.label("litmus init --starter").classes(
                    "text-sm font-mono text-slate-500"
                )


def _render_recent_runs(runs=None):
    """Render recent runs table."""
    if runs is None:
        backend = ParquetBackend(results_dir="results")
        runs = backend.list_runs(limit=10)

    if runs:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
                {"name": "station", "label": "Station", "field": "station_id", "align": "left"},
                {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
            ]
            rows = [
                {
                    "run_id": r.get("test_run_id", "")[:8],
                    "full_run_id": r.get("test_run_id", ""),
                    "dut_serial": r.get("dut_serial", ""),
                    "station_id": r.get("station_id", ""),
                    "started_at": format_datetime(r.get("started_at")),
                    "outcome": r.get("outcome", ""),
                }
                for r in runs
            ]
            table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
            table.on(
                "row-click", lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}")
            )
    else:
        ui.label("No test runs yet.").classes("text-slate-500 italic")
