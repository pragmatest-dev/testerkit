"""Instrument library list page."""

from datetime import date

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_instrument_assets, discover_instrument_types


@ui.page("/instruments")
def instruments_page():
    """Instruments listing page."""
    create_layout("Instruments")

    instrument_types = discover_instrument_types()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("precision_manufacturing").classes("text-slate-600")
                ui.label("Instrument Catalog").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Instrument",
                icon="add",
                on_click=lambda: ui.navigate.to("/instruments/new"),
            ).props("color=primary")

        # Info card
        with ui.card().classes("w-full bg-blue-50 border-blue-200"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("info", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Instrument Catalog").classes("font-semibold text-blue-900")
                        ui.label(
                            "Instrument definitions describe capabilities, SCPI commands, "
                            "and simulation defaults. User-defined instruments override "
                            "built-in ones."
                        ).classes("text-sm text-blue-800")

        if instrument_types:
            with ui.row().classes("gap-4 flex-wrap"):
                for entry in instrument_types:
                    _instrument_card(entry)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("precision_manufacturing").classes("text-4xl text-slate-300")
                ui.label("No instrument types defined.").classes("text-slate-500 mt-2")
                ui.button(
                    "Create Instrument",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/instruments/new"),
                ).classes("mt-4")

        # --- Instrument Inventory (asset files) ---
        _render_instrument_inventory()


def _instrument_card(entry):
    """Render an instrument card for an InstrumentCatalogEntry."""
    # Build capability summary strings
    cap_names = []
    for cap in entry.capabilities:
        name = f"{cap.function.value}_{cap.direction.value}"
        if name not in cap_names:
            cap_names.append(name)

    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("device_unknown").classes("text-2xl text-slate-600")
                    with ui.column().classes("gap-0"):
                        ui.label(entry.name or entry.type).classes("text-lg font-semibold")
                        ui.label(entry.type).classes("text-xs text-slate-500 font-mono")

        with ui.card_section():
            ui.label(entry.description or "").classes("text-sm text-slate-600")

            ui.label("Capabilities").classes("text-xs text-slate-500 uppercase mt-3")
            with ui.row().classes("gap-2 flex-wrap mt-1"):
                for cap in cap_names[:5]:
                    ui.badge(cap).props("outline")
                if len(cap_names) > 5:
                    ui.badge(f"+{len(cap_names) - 5} more").props("outline color=grey")

        with ui.card_actions():
            ui.button(
                "View",
                icon="visibility",
                on_click=lambda _, e=entry: ui.navigate.to(f"/instruments/{e.type}"),
            ).props("flat")
            ui.button(
                "Edit",
                icon="edit",
                on_click=lambda _, e=entry: ui.navigate.to(f"/instruments/{e.type}/edit"),
            ).props("flat")


def _render_instrument_inventory():
    """Render the instrument inventory section for physical asset files."""
    assets = discover_instrument_assets()

    ui.separator().classes("my-4")

    with ui.row().classes("items-center gap-2"):
        ui.icon("inventory_2").classes("text-slate-600")
        ui.label("Instrument Inventory").classes("text-lg font-semibold text-slate-700")
        ui.badge(str(len(assets))).props("outline")

    if assets:
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
            {"name": "cal_due", "label": "Cal Due", "field": "cal_due", "align": "left"},
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

        table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
        table.on(
            "row-click",
            lambda e: ui.navigate.to(f"/instruments/{e.args[1]['id']}"),
        )
    else:
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
