"""Dashboard page."""

import logging

from nicegui import run, ui

from litmus.ui.shared.components import (
    attach_status_chip,
    data_table,
    display_status,
    format_datetime,
    render_no_data_card,
    render_skeleton,
    status_chip_classes,
)
from litmus.ui.shared.layout import create_layout, get_dialog_counts_by_run
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
                render_no_data_card(
                    ui.column().classes("w-full"),
                    title="No stations configured.",
                    reason="Add a station YAML under stations/ to populate this panel.",
                    icon="dns",
                )

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
    """Render recent runs table.

    The Outcome cell carries the standard status chip and — when the
    run has pending operator dialogs — an amber bell badge that
    deep-links straight to ``/live/{run_id}``. Same shape as the
    main ``/results`` table so the operator sees one visual idiom
    everywhere they look at run outcomes.
    """
    if runs:
        dialog_count_by_run = get_dialog_counts_by_run()

        # Station column shows ``station_hostname`` (the machine an
        # operator recognizes), not the internal slug. Universal
        # rule — see feedback_operator_facing_identifiers.md.
        columns = [
            {"name": "uut", "label": "UUT", "field": "uut_serial_number", "align": "left"},
            {"name": "station", "label": "Station", "field": "station_hostname", "align": "left"},
            {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
            {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
        ]
        rows = []
        for r in runs:
            run_id = r.test_run_id or ""
            status = display_status(
                started_at=r.started_at,
                ended_at=r.ended_at,
                outcome=r.outcome,
            )
            rows.append(
                {
                    "full_run_id": run_id,
                    "uut_serial_number": r.uut_serial_number or "",
                    "station_hostname": r.station_hostname or "",
                    "started_at": format_datetime(r.started_at),
                    "outcome": status,
                    "outcome_class": status_chip_classes(status),
                    "dialog_count": dialog_count_by_run.get(run_id, 0),
                }
            )
        table = data_table(
            columns=columns,
            rows=rows,
            row_key="full_run_id",
            on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
            time_columns=["started"],
        )
        attach_status_chip(table, "outcome", with_dialog_badge=True)

        def _patch_dialog_counts() -> None:
            """In-place dialog-count refresh on the dashboard table.

            Same 1 s cadence as ``/results`` and the sidebar so the
            bell badge picks up new prompts within a second without
            re-running the recent-runs query.
            """
            counts = get_dialog_counts_by_run()
            changed = False
            for row in table.rows:
                rid = row.get("full_run_id") or ""
                new_count = counts.get(rid, 0)
                if row.get("dialog_count") != new_count:
                    row["dialog_count"] = new_count
                    changed = True
            if changed:
                table.update()

        ui.timer(1.0, _patch_dialog_counts)
    else:
        render_no_data_card(
            ui.column().classes("w-full"),
            title="No test runs yet.",
            reason="Launch a test to populate this list.",
            icon="history",
        )
