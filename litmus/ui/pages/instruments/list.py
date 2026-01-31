"""Instrument library list page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_instrument_types


@ui.page("/instruments")
def instruments_page():
    """Instruments listing page."""
    create_layout("Instruments")

    instrument_types = discover_instrument_types()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("precision_manufacturing").classes("text-slate-600")
                ui.label("Instrument Library").classes(
                    "text-lg font-semibold text-slate-700"
                )
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
                        ui.label("Instrument Library").classes(
                            "font-semibold text-blue-900"
                        )
                        ui.label(
                            "Instrument definitions describe capabilities, SCPI commands, "
                            "and simulation defaults. User-defined instruments override "
                            "built-in ones."
                        ).classes("text-sm text-blue-800")

        if instrument_types:
            with ui.row().classes("gap-4 flex-wrap"):
                for inst in instrument_types:
                    _instrument_card(inst)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("precision_manufacturing").classes("text-4xl text-slate-300")
                ui.label("No instrument types defined.").classes(
                    "text-slate-500 mt-2"
                )
                ui.button(
                    "Create Instrument",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/instruments/new"),
                ).classes("mt-4")


def _instrument_card(inst: dict):
    """Render an instrument card."""
    is_builtin = "library" in inst.get("source", "")

    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon(inst["icon"]).classes("text-2xl text-slate-600")
                    with ui.column().classes("gap-0"):
                        ui.label(inst["name"]).classes("text-lg font-semibold")
                        ui.label(inst["type"]).classes(
                            "text-xs text-slate-500 font-mono"
                        )
                if is_builtin:
                    ui.badge("Built-in", color="grey").props("outline dense")

        with ui.card_section():
            ui.label(inst["description"]).classes("text-sm text-slate-600")

            ui.label("Capabilities").classes("text-xs text-slate-500 uppercase mt-3")
            with ui.row().classes("gap-2 flex-wrap mt-1"):
                for cap in inst["capabilities"][:5]:
                    ui.badge(cap).props("outline")
                if len(inst["capabilities"]) > 5:
                    ui.badge(f"+{len(inst['capabilities']) - 5} more").props(
                        "outline color=grey"
                    )

        with ui.card_actions():
            ui.button(
                "View",
                icon="visibility",
                on_click=lambda i=inst: ui.navigate.to(f"/instruments/{i['type']}"),
            ).props("flat")
            ui.button(
                "Edit",
                icon="edit",
                on_click=lambda i=inst: ui.navigate.to(f"/instruments/{i['type']}/edit"),
            ).props("flat")
