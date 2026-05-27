"""Product list page — table view with merged YAML + observed-from-runs rows."""

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_layout,
    push_url_state,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import products_with_provenance

# Filter chip vocabulary — keep in lockstep with ProductRow.provenance.
_FILTER_OPTIONS = ["All", "Configured", "In use", "Observed only"]
_FILTER_TO_PROVENANCE = {
    "Configured": "configured",
    "In use": "in_use",
    "Observed only": "observed_only",
}


@ui.page("/products")
def products_page(filter: str = "All"):
    """Products list — one row per YAML product OR observed product id.

    Each row carries a status chip showing whether it's configured-only,
    actively in use, or observed-only (appears in run history without a
    YAML file). The filter chip row above the table narrows the view;
    filter selection is mirrored into the URL via ``push_url_state``.
    """
    create_layout("Products")

    rows_data = products_with_provenance()
    active_filter = filter if filter in _FILTER_OPTIONS else "All"

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("inventory_2").classes("text-slate-600")
                ui.label("Product Specifications").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Product",
                icon="add",
                on_click=lambda: ui.navigate.to("/products/new"),
            ).props("color=primary")

        if not rows_data:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No products configured or observed.").classes("text-slate-500")
                ui.label("Create product folders in products/ directory.").classes(
                    "text-sm text-slate-400"
                )
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
            {"name": "revision", "label": "Rev", "field": "revision", "align": "left"},
            {
                "name": "characteristics",
                "label": "Chars",
                "field": "characteristics",
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
                "revision": r.revision,
                "characteristics": r.characteristics if r.provenance != "observed_only" else "—",
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

        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("Show:").classes("text-sm text-slate-500")
            toggle = ui.toggle(
                _FILTER_OPTIONS,
                value=active_filter,
            ).props("color=primary dense unelevated")

        table = data_table(
            columns=columns,
            rows=_filtered(active_filter),
            row_key="id",
            on_row_click=lambda r: (
                ui.navigate.to(f"/products/{r['id']}")
                if r.get("provenance") != "observed_only"
                else None
            ),
            time_columns=["last_run"],
        )
        table.props('data-testid="products-table"')

        table.add_slot(
            "body-cell-provenance",
            """
            <q-td :props="props">
                <q-chip dense square
                    :color="props.value === 'in_use' ? 'positive'
                        : props.value === 'observed_only' ? 'warning'
                        : 'grey-4'"
                    :text-color="props.value === 'in_use' || props.value === 'observed_only'
                        ? 'white' : 'grey-9'">
                    {{ props.value === 'in_use' ? 'In use'
                       : props.value === 'observed_only' ? 'Observed only'
                       : 'Configured' }}
                </q-chip>
            </q-td>
            """,
        )

        def _on_filter_change(e):
            selected = e.value or "All"
            table.rows = _filtered(selected)
            table.update()
            push_url_state("/products", {"filter": selected})

        toggle.on_value_change(_on_filter_change)
