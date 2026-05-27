"""Station list page — table view with merged YAML + observed-from-runs rows."""

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_layout,
    push_url_state,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import stations_with_provenance

# Filter chip vocabulary — keep in lockstep with StationRow.provenance.
# The Runs column already conveys "has activity", so the chip stays
# binary: Configured (YAML exists) vs Observed (orphan).
_FILTER_OPTIONS = ["All", "Configured", "Observed"]
_FILTER_TO_PROVENANCE = {
    "Configured": "configured",
    "Observed": "observed_only",
}


@ui.page("/stations")
def stations_page(filter: str = "All"):
    """Stations list — one row per YAML station OR observed station id.

    Each row carries a Configured / Observed status chip (Observed =
    appears in run history without a YAML file). The filter chip row
    above the table narrows the view;
    filter selection is mirrored into the URL via ``push_url_state`` so
    deep links are shareable.
    """
    create_layout("Stations")

    rows_data = stations_with_provenance()

    active_filter = filter if filter in _FILTER_OPTIONS else "All"

    with page_layout():
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

        if not rows_data:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("settings_input_hdmi").classes("text-4xl text-slate-300")
                ui.label("No stations configured or observed.").classes("text-slate-500 mt-2")
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
            {
                "name": "provenance",
                "label": "Status",
                "field": "provenance",
                "align": "left",
                "sortable": True,
            },
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

        def _to_table_row(r) -> dict:
            return {
                "id": r.id,
                "name": r.name,
                "location": r.location,
                "instruments": r.instruments if r.provenance != "observed_only" else "—",
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

        # Filter chip row — above the table per UI consistency rule.
        # Wrapped in a card so it visually separates from the page
        # header above and the table below; matches the /events pattern.
        # Individual ui.button widgets in a gap'd row so each filter
        # reads as its own discrete chip (the default ui.toggle packs
        # them edge-to-edge with no separator).
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
            push_url_state("/stations", {"filter": selected})

        with ui.card().classes("w-full").props('data-testid="stations-filters"'):
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
                ui.navigate.to(f"/stations/{r['id']}")
                if r.get("provenance") != "observed_only"
                else None
            ),
            time_columns=["last_run"],
        )
        table.props('data-testid="stations-table"')

        # Render the provenance column as a coloured q-chip. Colour map:
        # configured → neutral grey, observed_only → warning amber.
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
