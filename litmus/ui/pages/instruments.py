"""Instruments page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_instrument_types


@ui.page("/instruments")
def instruments_page():
    """Instruments listing page."""
    create_layout("Instruments")

    instrument_types = discover_instrument_types()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("precision_manufacturing").classes("text-slate-600")
            ui.label("Instrument Types").classes("text-lg font-semibold text-slate-700")

        with ui.row().classes("gap-4 flex-wrap"):
            for inst in instrument_types:
                _instrument_card(inst)


def _instrument_card(inst: dict):
    """Render an instrument card."""
    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center gap-3"):
                ui.icon(inst["icon"]).classes("text-2xl text-slate-600")
                with ui.column().classes("gap-0"):
                    ui.label(inst["name"]).classes("text-lg font-semibold")
                    ui.label(inst["type"]).classes("text-xs text-slate-500 font-mono")

        with ui.card_section():
            ui.label(inst["description"]).classes("text-sm text-slate-600")

            ui.label("Capabilities").classes("text-xs text-slate-500 uppercase mt-3")
            with ui.row().classes("gap-2 flex-wrap mt-1"):
                for cap in inst["capabilities"]:
                    ui.badge(cap).props("outline")
