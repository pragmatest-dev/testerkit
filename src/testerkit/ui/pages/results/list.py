"""Results list page — wide scannable table with all run-level columns."""

import logging
from typing import Any

from nicegui import run, ui

from testerkit.ui.shared.components import (
    attach_status_chip,
    data_table,
    display_status,
    format_datetime,
    page_header,
    page_layout,
    render_no_data_card,
    stat_card,
    status_chip_classes,
    subscribe_with_refresh,
)
from testerkit.ui.shared.layout import create_layout, get_dialog_counts_by_run
from testerkit.ui.shared.services import count_recent_runs, get_recent_runs

logger = logging.getLogger(__name__)

_DEFAULT_RPP = 50


@ui.page("/results")
async def results_page() -> None:
    """Results listing — every run-level column the detail page shows."""
    create_layout("Test Results")

    with page_layout():
        page_header("Test Results", icon="history")

        # data-testid attributes are stable selectors for the
        # screenshot-regeneration script (scripts/regenerate-ui-
        # screenshots.py). Don't drop them without updating that
        # script's MANIFEST.
        stats_holder = ui.column().classes("w-full").props('data-testid="results-stats"')
        empty_holder = ui.column().classes("w-full").props('data-testid="results-empty"')
        table_holder = (
            ui.column().classes("w-full flex-1 min-h-0 gap-0").props('data-testid="results-table"')
        )

        state: dict[str, Any] = {"table": None}

        def _fetch_page(page: int, rpp: int) -> tuple[list[dict[str, Any]], int]:
            per_page = rpp if rpp > 0 else _DEFAULT_RPP
            offset = (page - 1) * per_page
            rows = get_recent_runs(limit=per_page, offset=offset, include_incomplete=True)
            total = count_recent_runs(include_incomplete=True)
            counts = get_dialog_counts_by_run()
            return [_row_for_run(r, counts) for r in rows], total

        async def refresh() -> None:
            table = state["table"]
            try:
                if table is not None:
                    page = int(table.pagination.get("page", 1) or 1)
                    rpp = int(table.pagination.get("rowsPerPage", _DEFAULT_RPP) or _DEFAULT_RPP)
                else:
                    page, rpp = 1, _DEFAULT_RPP
                # Run the blocking Flight queries off the event loop so
                # the page handler stays responsive (otherwise NiceGUI's
                # WebSocket heartbeat times out at 3s).
                new_rows, total = await run.io_bound(_fetch_page, page, rpp)
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load results (keeping current view): %s", exc)
                return

            if not new_rows:
                if table is None:
                    _render_stats(stats_holder, [], 0)
                    empty_holder.clear()
                    render_no_data_card(
                        empty_holder,
                        title="No test results found.",
                        reason="Launch a test to populate this list.",
                        icon="history",
                    )
                    # Convenience action — operators on the empty page
                    # often want to launch a test next; the inline
                    # button is faster than the sidebar.
                    with empty_holder, ui.row().classes("w-full justify-center"):
                        ui.button(
                            "Launch a Test",
                            icon="play_arrow",
                            on_click=lambda: ui.navigate.to("/launch"),
                        ).classes("mt-2")
                return

            _render_stats(stats_holder, new_rows, total)
            empty_holder.clear()

            if table is None:
                with table_holder:
                    state["table"] = _build_table(new_rows, total, _fetch_page)
                return

            table.pagination.update({"rowsNumber": total})
            table.update_rows(new_rows)

        await refresh()

        from testerkit.data.data_dir import resolve_data_dir
        from testerkit.data.event_store import EventStore

        try:
            event_store = EventStore.get_shared(resolve_data_dir())
            subscribe_with_refresh(event_store, ["run.started", "run.ended"], refresh)
        except (OSError, RuntimeError) as exc:
            logger.warning("Live updates unavailable: %s", exc)

        def _patch_dialog_counts() -> None:
            """Update each row's ``dialog_count`` in place every 1 s.

            Matches the sidebar Active Tests refresh cadence so the
            row-level bell badge stays in sync with the global tray
            without re-running the parquet query. Cheap — only the
            ``dialog_count`` field changes; q-table re-renders only
            the Outcome cell's bell span.
            """
            table = state.get("table")
            if table is None:
                return
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


def _render_stats(slot: ui.column, runs: list, total: int) -> None:
    slot.clear()
    if not total:
        return
    passed = sum(1 for r in runs if r.get("outcome") == "Passed")
    failed = sum(1 for r in runs if r.get("outcome") == "Failed")
    errored = sum(1 for r in runs if r.get("outcome") == "Errored")
    page_n = len(runs)
    pass_rate = int(passed / page_n * 100) if page_n else 0
    last_started = next((r["started"] for r in runs if r.get("started")), None)

    with slot, ui.card().classes("w-full"), ui.card_section().classes("py-2"):
        with ui.row().classes("gap-10 items-center"):
            stat_card(str(total), "Total Runs")
            stat_card(f"{pass_rate}%", "Pass Rate (page)", "text-blue-600")
            stat_card(str(passed), "Passed", "text-emerald-600")
            stat_card(str(failed), "Failed", "text-red-600")
            stat_card(str(errored), "Errored", "text-amber-600")
            if last_started:
                stat_card(last_started, "Latest")


def _row_for_run(r: Any, dialog_count_by_run: dict[str, int]) -> dict[str, Any]:
    status = display_status(
        started_at=r.started_at,
        ended_at=r.ended_at,
        outcome=r.outcome,
    )
    run_id = r.test_run_id or ""
    return {
        "full_run_id": run_id,
        "outcome": status,
        "outcome_class": status_chip_classes(status),
        "serial": r.uut_serial_number or "",
        "part_number": r.uut_part_number or "",
        "hostname": r.station_hostname or "",
        "project": r.project_name or "",
        "phase": r.test_phase or "",
        "started": format_datetime(r.started_at),
        "ended": format_datetime(r.ended_at),
        "steps": r.total_steps,
        "measurements": r.total_measurements,
        # Bell + amber count badge render in the Outcome cell when
        # this run has 1+ pending operator dialogs. ``with_dialog_
        # badge=True`` on attach_status_chip(...) below reads this.
        "dialog_count": dialog_count_by_run.get(run_id, 0),
    }


def _build_table(
    rows: list[dict[str, Any]],
    total: int,
    fetch_page: Any,
) -> ui.table:
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
        total_rows=total,
        fetch_page=fetch_page,
    )
    attach_status_chip(table, "outcome", with_dialog_badge=True)
    return table
