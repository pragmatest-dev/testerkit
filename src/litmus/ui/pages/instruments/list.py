"""Instrument library list page — Catalog + Inventory as tabs.

The Inventory tab uses the merged-with-badge pattern (Configured /
In use / Observed) — same shape as ``/stations``, ``/products``,
and ``/fixtures``. The Catalog tab lists instrument *types* (templates,
not physical things); the observed side doesn't fit cleanly there and
is intentionally not surfaced.
"""

from typing import Any

from nicegui import ui

from litmus.ui.shared.components import (
    data_table,
    format_datetime,
    page_layout,
    push_url_state,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_instrument_types,
    instrument_assets_with_provenance,
)

# Filter chip vocabulary — keep in lockstep with InstrumentAssetRow.provenance.
# The Runs column already conveys "has activity", so the chip stays
# binary: Configured (YAML exists) vs Observed (orphan).
_FILTER_OPTIONS = ["All", "Configured", "Observed"]
_FILTER_TO_PROVENANCE = {
    "Configured": "configured",
    "Observed": "observed_only",
}


@ui.page("/instruments")
def instruments_page(filter: str = "All"):
    """Instruments listing — Catalog (types) + Inventory (assets) tabs."""
    create_layout("Instruments")

    active_filter = filter if filter in _FILTER_OPTIONS else "All"

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("precision_manufacturing").classes("text-slate-600")
                ui.label("Instruments").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Instrument",
                icon="add",
                on_click=lambda: ui.navigate.to("/instruments/new"),
            ).props("color=primary")

        with ui.tabs().props("inline-label no-caps dense").classes("w-full") as tabs:
            catalog_tab = ui.tab("Catalog", icon="menu_book")
            inventory_tab = ui.tab("Inventory", icon="inventory_2")
        ui.add_css(
            ".q-tab__icon { font-size: 1rem !important; }"
            ".q-tab { min-height: 32px !important; padding: 0 12px !important; }"
        )
        with ui.tab_panels(tabs, value=catalog_tab).classes("w-full"):
            with ui.tab_panel(catalog_tab):
                _render_catalog_tab()
            with ui.tab_panel(inventory_tab):
                _render_inventory_tab(active_filter)


def _render_catalog_tab():
    """Catalog = instrument type definitions (capabilities + SCPI + simulation).

    Templates, not physical things — the merged-with-badge pattern
    doesn't fit here, so the Catalog tab keeps its original shape.
    """
    instrument_types = discover_instrument_types()
    if not instrument_types:
        with ui.card().classes("w-full p-6 text-center"):
            ui.icon("precision_manufacturing").classes("text-4xl text-slate-300")
            ui.label("No instrument types defined.").classes("text-slate-500 mt-2")
            ui.button(
                "Create Instrument",
                icon="add",
                on_click=lambda: ui.navigate.to("/instruments/new"),
            ).classes("mt-4")
        return

    columns = [
        {"name": "type", "label": "Type", "field": "type", "align": "left", "sortable": True},
        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
        {"name": "description", "label": "Description", "field": "description", "align": "left"},
        {
            "name": "capabilities",
            "label": "Capabilities",
            "field": "capabilities",
            "align": "right",
        },
    ]
    rows = []
    for entry in instrument_types:
        cap_names = sorted(
            {f"{cap.function.value}_{cap.direction.value}" for cap in entry.capabilities}
        )
        rows.append(
            {
                "type": entry.type,
                "name": entry.name or "",
                "description": entry.description or "",
                "capabilities": len(cap_names),
            }
        )
    data_table(
        columns=columns,
        rows=rows,
        row_key="type",
        on_row_click=lambda r: ui.navigate.to(f"/instruments/{r['type']}"),
    ).props('data-testid="instruments-catalog-table"')


def _render_inventory_tab(active_filter: str):
    """Inventory = physical asset files joined with observed-from-runs.

    Each row carries a status chip (Configured / In use / Observed)
    derived by joining the asset YAMLs against distinct instrument ids
    seen in ``step_instruments_id`` across run history.
    """
    rows_data = instrument_assets_with_provenance()

    if not rows_data:
        with ui.card().classes("w-full bg-blue-50 border-blue-200"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("info", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("No instrument assets configured or observed.").classes(
                            "font-semibold text-blue-900"
                        )
                        ui.label(
                            "Use `litmus station init` to discover instruments "
                            "and create asset files."
                        ).classes("text-sm text-blue-800")
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
        {"name": "driver", "label": "Driver", "field": "driver", "align": "left"},
        {
            "name": "identity",
            "label": "Manufacturer / Model",
            "field": "identity",
            "align": "left",
        },
        {"name": "serial", "label": "Serial", "field": "serial", "align": "left"},
        {
            "name": "cal_due",
            "label": "Cal Due",
            "field": "cal_due",
            "align": "left",
            "sortable": True,
        },
        {"name": "cal_lab", "label": "Cal Lab", "field": "cal_lab", "align": "left"},
        {"name": "runs", "label": "Runs", "field": "runs", "align": "right", "sortable": True},
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
            "driver": r.driver,
            "identity": r.identity,
            "serial": r.serial,
            "cal_due": r.cal_due,
            "cal_lab": r.cal_lab,
            "runs": r.runs,
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
        push_url_state("/instruments", {"filter": selected})

    with ui.card().classes("w-full").props('data-testid="instruments-filters"'):
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
            ui.navigate.to(f"/instruments/{r['id']}")
            if r.get("provenance") != "observed_only"
            else None
        ),
        time_columns=["last_run"],
    )
    table.props('data-testid="instruments-inventory-table"')

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
