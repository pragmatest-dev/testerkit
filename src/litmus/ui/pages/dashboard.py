"""Dashboard page."""

import logging

from nicegui import run, ui

from litmus.ui.shared.components import data_table, format_datetime, render_skeleton
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_stations, get_recent_runs

logger = logging.getLogger(__name__)


@ui.page("/")
async def dashboard_page():
    """Main dashboard page.

    Page handler returns immediately with skeleton placeholders.
    Stations and recent runs load off the event loop via run.io_bound.
    """
    create_layout("Dashboard")

    # data-testid attributes are stable selectors for the
    # screenshot-regeneration script (scripts/regenerate-ui-
    # screenshots.py). Don't drop them without updating that
    # script's MANIFEST.
    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("memory").classes("text-slate-600")
            ui.label("Stations").classes("text-lg font-semibold text-slate-700")
        stations_container = (
            ui.row().classes("gap-4 flex-wrap w-full").props('data-testid="dashboard-stations"')
        )
        render_skeleton(stations_container, "h-24")

        with ui.row().classes("items-center gap-2 mt-4"):
            ui.icon("history").classes("text-slate-600")
            ui.label("Recent Runs").classes("text-lg font-semibold text-slate-700")
        runs_container = ui.column().classes("w-full").props('data-testid="dashboard-runs"')
        render_skeleton(runs_container, "h-48")

    async def _load_dashboard() -> None:
        stations = await run.io_bound(discover_stations)
        try:
            recent_runs = await run.io_bound(get_recent_runs, 10)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load recent runs: %s", exc)
            recent_runs = []

        if not stations and not recent_runs:
            stations_container.delete()
            runs_container.delete()
            _getting_started_card()
            return

        stations_container.clear()
        with stations_container:
            if stations:
                for station in stations:
                    _station_card(station)
            else:
                ui.label("No stations configured.").classes("text-slate-500 italic")

        runs_container.clear()
        with runs_container:
            _render_recent_runs(recent_runs)

    ui.timer(0.0, _load_dashboard, once=True)


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
            ui.label("Set up your first test bench in three steps.").classes("text-slate-600 mt-1")

            with ui.column().classes("gap-4 mt-6"):
                # Step 1
                with ui.row().classes("items-start gap-3"):
                    ui.badge("1", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Create a station").classes("font-semibold")
                        ui.label("Define which instruments are at your bench.").classes(
                            "text-sm text-slate-600"
                        )
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
                ui.label("Or start with a full example:").classes("text-sm text-slate-600")
                ui.label("litmus init --starter").classes("text-sm font-mono text-slate-500")


def _render_recent_runs(runs: list) -> None:
    """Render recent runs table."""
    if runs:
        # Station column shows ``station_hostname`` (the machine an
        # operator recognizes), not the internal slug. Universal
        # rule — see feedback_operator_facing_identifiers.md.
        columns = [
            {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
            {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
            {"name": "station", "label": "Station", "field": "station_hostname", "align": "left"},
            {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
            {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
        ]
        rows = [
            {
                "run_id": (r.test_run_id or "")[:8],
                "full_run_id": r.test_run_id or "",
                "dut_serial": r.dut_serial or "",
                "station_hostname": r.station_hostname or "",
                "started_at": format_datetime(r.started_at),
                "outcome": r.outcome or "",
            }
            for r in runs
        ]
        data_table(
            columns=columns,
            rows=rows,
            row_key="run_id",
            on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
            time_columns=["started"],
        )
    else:
        ui.label("No test runs yet.").classes("text-slate-500 italic")
