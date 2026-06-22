"""UUTs list page — every row is a device observed in run history.

UUTs are never declared in YAML (the unit-under-test is identified at
runtime by serial). Unlike the other entity-observed-view pages, this
one has no provenance chip and no filter — every row is observed-only
by definition.
"""

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_header,
    page_layout,
    render_no_data_card,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import uuts_from_runs


@ui.page("/uuts")
def uuts_page():
    """UUTs list — one row per distinct ``uut_serial`` in run history."""
    create_layout("UUTs")

    rows_data = uuts_from_runs()

    with page_layout():
        page_header("UUTs", icon="memory", badge=f"{len(rows_data)} observed")

        if not rows_data:
            render_no_data_card(
                ui.column().classes("w-full"),
                title="No UUTs observed yet.",
                reason=(
                    "Run a test against a station to populate this list. "
                    "Every distinct UUT serial that appears in run history "
                    "shows up here."
                ),
                icon="memory",
            )
            return

        columns = [
            {
                "name": "serial",
                "label": "Serial",
                "field": "serial",
                "align": "left",
                "sortable": True,
            },
            {
                "name": "part_number",
                "label": "Part Number",
                "field": "part_number",
                "align": "left",
                "sortable": True,
            },
            {
                "name": "lot_number",
                "label": "Lot",
                "field": "lot_number",
                "align": "left",
                "sortable": True,
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

        rows = [
            {
                "serial": r.serial,
                "part_number": r.part_number,
                "lot_number": r.lot_number,
                "runs": r.runs,
                "passed": r.passed,
                "failed": r.failed,
                "last_run": format_datetime(r.last_run) if r.last_run else "—",
            }
            for r in rows_data
        ]

        data_table(
            columns=columns,
            rows=rows,
            row_key="serial",
            time_columns=["last_run"],
        ).props('data-testid="uuts-table"')
