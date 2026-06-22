"""Fixtures list page — table view with merged YAML + observed-from-runs rows."""

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_layout,
    push_url_state,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import fixtures_with_provenance

# Filter chip vocabulary — keep in lockstep with FixtureRow.provenance.
# The Runs column already conveys "has activity", so the chip stays
# binary: Configured (YAML exists) vs Observed (orphan).
_FILTER_OPTIONS = ["All", "Configured", "Observed"]
_FILTER_TO_PROVENANCE = {
    "Configured": "configured",
    "Observed": "observed_only",
}


@ui.page("/fixtures")
def fixtures_page(filter: str = "All"):
    """Fixtures list — one row per YAML fixture OR observed fixture id.

    Each row carries a Configured / Observed status chip (Observed =
    appears in run history without a YAML file). The filter chip row
    above the table narrows the view; selection round-trips through the URL via
    ``push_url_state``.
    """
    create_layout("Fixtures")

    rows_data = fixtures_with_provenance()
    active_filter = filter if filter in _FILTER_OPTIONS else "All"

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("hub").classes("text-slate-600")
                ui.label("Test Fixtures").classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(rows_data)} fixtures").props("outline")
            ui.button(
                "New Fixture",
                icon="add",
                on_click=lambda: ui.navigate.to("/fixtures/new"),
            ).props("color=primary")

        if not rows_data:
            with ui.card().classes("w-full p-8 text-center"):
                ui.icon("hub", size="xl").classes("text-slate-300")
                ui.label("No fixtures configured or observed").classes(
                    "text-xl text-slate-600 mt-4"
                )
                ui.label(
                    "Fixtures define how UUT pins connect to station instruments. "
                    "Each fixture is tied to a part family and maps pin names to "
                    "instrument names."
                ).classes("text-slate-500 mt-2")
                ui.button(
                    "Create Fixture",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/fixtures/new"),
                ).props("color=primary").classes("mt-4")
            return

        columns = [
            {
                "name": "provenance",
                "label": "Status",
                "field": "provenance",
                "align": "left",
                "sortable": True,
            },
            {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {
                "name": "part",
                "label": "Part",
                "field": "part",
                "align": "left",
                "sortable": True,
            },
            {"name": "revision", "label": "Rev", "field": "revision", "align": "left"},
            {
                "name": "connections",
                "label": "Connections",
                "field": "connections",
                "align": "right",
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

        def _to_table_row(r) -> dict:
            return {
                "id": r.id,
                "name": r.name,
                "part": r.part,
                "revision": r.revision,
                "connections": r.connections if r.provenance != "observed_only" else "—",
                "runs": r.runs,
                "passed": r.passed,
                "failed": r.failed,
                "last_run": format_datetime(r.last_run) if r.last_run else "—",
                "provenance": r.provenance,
            }

        all_rows = [_to_table_row(r) for r in rows_data]

        def _filtered(selected: str) -> list[dict]:
            if selected == "All":
                return all_rows
            wanted = _FILTER_TO_PROVENANCE.get(selected)
            return [row for row in all_rows if row["provenance"] == wanted]

        filter_buttons: dict[str, Any] = {}

        def _apply_filter(selected: str) -> None:
            for opt, btn in filter_buttons.items():
                if opt == selected:
                    btn.props(remove="outline")
                    btn.props("unelevated color=primary")
                else:
                    btn.props(remove="unelevated")
                    btn.props("outline color=primary")
            table.rows = _filtered(selected)
            table.update()
            push_url_state("/fixtures", {"filter": selected})

        with ui.card().classes("w-full").props('data-testid="fixtures-filters"'):
            with ui.row().classes("items-center gap-2"):
                ui.label("Show").classes("text-sm font-medium text-slate-600 mr-2")
                for opt in _FILTER_OPTIONS:
                    btn = ui.button(opt, on_click=lambda _e, o=opt: _apply_filter(o)).props(
                        "dense no-caps"
                    )
                    if opt == active_filter:
                        btn.props("unelevated color=primary")
                    else:
                        btn.props("outline color=primary")
                    filter_buttons[opt] = btn

        table = data_table(
            columns=columns,
            rows=_filtered(active_filter),
            row_key="id",
            on_row_click=lambda r: (
                ui.navigate.to(f"/fixtures/{r['id']}")
                if r.get("provenance") != "observed_only"
                else None
            ),
            time_columns=["last_run"],
        )
        table.props('data-testid="fixtures-table"')

        table.add_slot(
            "body-cell-provenance",
            """
            <q-td :props="props">
                <q-chip dense square
                    :color="props.value === 'observed_only' ? 'warning' : 'grey-4'"
                    :text-color="props.value === 'observed_only' ? 'white' : 'grey-9'">
                    {{ props.value === 'observed_only' ? 'Observed' : 'Configured' }}
                </q-chip>
            </q-td>
            """,
        )
