"""Results list page — wide scannable table with all run-level columns.

Live: subscribes to ``run.started`` / ``run.ended`` events so the
table refreshes in place when a run begins or finishes — no page
reload, no flash. Operators see in-flight runs immediately because
``get_recent_runs`` is called with ``include_incomplete=True``.
"""

import logging
from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    attach_status_chip,
    data_table,
    display_status,
    format_datetime,
    page_header,
    page_layout,
    stat_card,
    status_chip_classes,
    subscribe_with_refresh,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import get_recent_runs

logger = logging.getLogger(__name__)


@ui.page("/results")
def results_page():
    """Results listing — every run-level column the detail page shows.

    The detail page's Overview is just a deep-link drill-down into a
    single row; everything that's there should be scannable from the
    list. Click any row to see step-level breakdown.
    """
    create_layout("Test Results")

    with page_layout():
        page_header("Test Results", icon="history")

        stats_holder = ui.column().classes("w-full")
        empty_holder = ui.column().classes("w-full")
        table_holder = ui.column().classes("w-full flex-1 min-h-0 gap-0")

        # ``state[table]`` is built lazily on the first tick that has
        # rows so the heavy ``data_table`` setup only runs once.
        # Subsequent refreshes mutate ``table.rows`` in place and
        # call ``table.update()`` — same no-flash pattern as the
        # /channels list.
        state: dict[str, Any] = {"table": None}

        def refresh() -> None:
            try:
                runs = get_recent_runs(limit=50, include_incomplete=True)
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load results: %s", exc)
                runs = []

            _render_stats(stats_holder, runs)

            if not runs:
                empty_holder.clear()
                with empty_holder, ui.card().classes("w-full p-6 text-center"):
                    ui.label("No test results found.").classes("text-slate-500")
                    ui.button(
                        "Launch a Test",
                        icon="play_arrow",
                        on_click=lambda: ui.navigate.to("/launch"),
                    ).classes("mt-4")
                if state["table"] is not None:
                    state["table"].rows.clear()
                    state["table"].update()
                return

            empty_holder.clear()
            new_rows = [_row_for_run(r) for r in runs]
            table = state["table"]
            if table is None:
                with table_holder:
                    state["table"] = _build_table(new_rows)
                return
            old_by_id = {r["full_run_id"]: r for r in table.rows}
            preserved: list[dict[str, Any]] = []
            for new_row in new_rows:
                existing = old_by_id.get(new_row["full_run_id"])
                if existing is None:
                    preserved.append(new_row)
                else:
                    existing.update(new_row)
                    preserved.append(existing)
            table.rows[:] = preserved
            table.update()

        refresh()

        # Subscribe to run lifecycle events so the table refreshes in
        # place when a run starts or finishes. Debounce coalesces
        # multi-slot bursts.
        from litmus.data.event_store import EventStore
        from litmus.data.results_dir import resolve_results_dir

        try:
            event_store = EventStore(_results_dir=resolve_results_dir())
            subscribe_with_refresh(
                event_store,
                ["run.started", "run.ended"],
                refresh,
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("Live updates unavailable: %s", exc)


def _render_stats(slot: ui.column, runs: list) -> None:
    """Replace ``slot`` content with the at-a-glance stat strip."""
    slot.clear()
    if not runs:
        return
    total = len(runs)
    passed = sum(1 for r in runs if r.outcome == "passed")
    failed = sum(1 for r in runs if r.outcome == "failed")
    errored = sum(1 for r in runs if r.outcome == "errored")
    pass_rate = int(passed / total * 100) if total else 0
    unique_duts = len({r.dut_serial for r in runs if r.dut_serial})
    last_run = max((r.started_at for r in runs if r.started_at), default=None)

    with slot, ui.card().classes("w-full"), ui.card_section().classes("py-2"):
        with ui.row().classes("gap-10 items-center"):
            stat_card(str(total), "Runs")
            stat_card(f"{pass_rate}%", "Pass Rate", "text-blue-600")
            stat_card(str(passed), "Passed", "text-emerald-600")
            stat_card(str(failed), "Failed", "text-red-600")
            stat_card(str(errored), "Errored", "text-amber-600")
            stat_card(str(unique_duts), "DUTs")
            stat_card(format_datetime(last_run) or "—", "Last Run")


def _row_for_run(r: Any) -> dict[str, Any]:
    """Build the q-table row dict for a single ``RunSummary``."""
    status = display_status(
        started_at=r.started_at,
        ended_at=r.ended_at,
        outcome=r.outcome,
    )
    return {
        "full_run_id": r.test_run_id or "",
        "outcome": status,
        "outcome_class": status_chip_classes(status),
        "serial": r.dut_serial or "",
        "part_number": r.dut_part_number or "",
        "hostname": r.station_hostname or "",
        "project": r.project_name or "",
        "phase": r.test_phase or "",
        "started": format_datetime(r.started_at),
        "ended": format_datetime(r.ended_at),
        "steps": r.total_steps,
        "measurements": r.total_measurements,
    }


def _build_table(rows: list[dict[str, Any]]) -> ui.table:
    """Construct the runs table once. Later ticks mutate ``.rows``."""
    # Column order follows the WATS / TestStand convention:
    # Outcome (colored chip first — eye catches the bad ones),
    # then UUT identity (Serial, Part Number), then where it ran
    # (Hostname, Project, Phase), then when (Started), then
    # volumetrics (Steps, Measurements), with Ended last.
    # Run ID intentionally not shown — the 8-char prefix is noise
    # for scanning. The full run id stays in each row's
    # ``full_run_id`` for the row-click → /results/{id} drill-down.
    sortable = {"sortable": True}
    columns = [
        {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center", **sortable},
        {"name": "serial", "label": "Serial", "field": "serial", "align": "left", **sortable},
        {
            "name": "part_number",
            "label": "Part Number",
            "field": "part_number",
            "align": "left",
            **sortable,
        },
        {"name": "hostname", "label": "Hostname", "field": "hostname", "align": "left", **sortable},
        {"name": "project", "label": "Project", "field": "project", "align": "left", **sortable},
        {"name": "phase", "label": "Phase", "field": "phase", "align": "left", **sortable},
        {"name": "started", "label": "Started", "field": "started", "align": "left", **sortable},
        {"name": "steps", "label": "Steps", "field": "steps", "align": "right", **sortable},
        {
            "name": "measurements",
            "label": "Meas",
            "field": "measurements",
            "align": "right",
            **sortable,
        },
        {"name": "ended", "label": "Ended", "field": "ended", "align": "left"},
    ]
    table = data_table(
        columns=columns,
        rows=rows,
        row_key="full_run_id",
        on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
        time_columns=["started", "ended"],
    )
    attach_status_chip(table, "outcome")
    return table
