"""Instrument library list page — Catalog + Inventory as tabs."""

from datetime import date

from nicegui import ui

from litmus.ui.shared.components import data_table, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_instrument_assets, discover_instrument_types


@ui.page("/instruments")
def instruments_page():
    """Instruments listing — Catalog (types) + Inventory (assets) tabs."""
    create_layout("Instruments")

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
                _render_inventory_tab()


def _render_catalog_tab():
    """Catalog = instrument type definitions (capabilities + SCPI + simulation)."""
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


def _render_inventory_tab():
    """Inventory = physical asset files (serials, calibration, manufacturer/model)."""
    assets = discover_instrument_assets()
    if not assets:
        with ui.card().classes("w-full bg-blue-50 border-blue-200"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("info", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("No instrument asset files found.").classes(
                            "font-semibold text-blue-900"
                        )
                        ui.label(
                            "Use `litmus station init` to discover instruments "
                            "and create asset files."
                        ).classes("text-sm text-blue-800")
        return

    columns = [
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
    ]
    rows = []
    for asset in assets:
        cal_due = asset.calibration.due_date
        if cal_due:
            if isinstance(cal_due, date):
                cal_str = cal_due.isoformat()
            else:
                cal_str = str(cal_due)
        else:
            cal_str = ""

        mfr = asset.info.manufacturer or ""
        model = asset.info.model or ""
        identity = f"{mfr} {model}".strip() if (mfr or model) else ""

        rows.append(
            {
                "id": asset.id,
                "driver": asset.driver or "",
                "identity": identity,
                "serial": str(asset.info.serial or ""),
                "cal_due": cal_str,
                "cal_lab": asset.calibration.lab or "",
            }
        )
    data_table(
        columns=columns,
        rows=rows,
        row_key="id",
        on_row_click=lambda r: ui.navigate.to(f"/instruments/{r['id']}"),
    ).props('data-testid="instruments-inventory-table"')
