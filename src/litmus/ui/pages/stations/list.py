"""Station list page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_stations


@ui.page("/stations")
def stations_page():
    """Stations listing page."""
    create_layout("Stations")

    stations = discover_stations()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("settings_input_hdmi").classes("text-slate-600")
                ui.label("Test Stations").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Station",
                icon="add",
                on_click=lambda: ui.navigate.to("/stations/new"),
            ).props("color=primary")

        if stations:
            with ui.row().classes("gap-4 flex-wrap"):
                for station in stations:
                    _station_card(station)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("settings_input_hdmi").classes("text-4xl text-slate-300")
                ui.label("No stations configured.").classes("text-slate-500 mt-2")
                ui.label("Create a station to define your test equipment setup.").classes(
                    "text-sm text-slate-400"
                )
                ui.button(
                    "Create Station",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/stations/new"),
                ).classes("mt-4")


def _station_card(station):
    """Render a station card for a StationConfig model."""
    instruments = station.instruments or {}

    with ui.card().classes("w-96"):
        with ui.card_section():
            with ui.row().classes("items-start justify-between"):
                ui.label(station.name or station.id).classes("text-lg font-semibold")
                ui.badge("Online", color="green").props("outline")

        with ui.card_section():
            ui.label(station.description or "").classes("text-sm text-slate-600")
            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                with ui.row().classes("items-center gap-1"):
                    ui.icon("tag", size="xs")
                    ui.label(station.id)
                with ui.row().classes("items-center gap-1"):
                    ui.icon("location_on", size="xs")
                    ui.label(station.location or "")

            if instruments:
                ui.label("Instruments").classes("text-xs text-slate-500 uppercase mt-4")
                for name, inst in instruments.items():
                    with ui.row().classes("items-center gap-2 mt-1"):
                        mocked = inst.mock or False
                        ui.icon("sim_card" if mocked else "cable", size="xs").classes(
                            "text-slate-400"
                        )
                        ui.label(name).classes("text-sm")
                        ui.label(inst.type or "").classes("text-xs text-slate-500")

        with ui.card_actions():
            ui.button(
                "View Details",
                icon="visibility",
                on_click=lambda _, s=station: ui.navigate.to(f"/stations/{s.id}"),
            ).props("flat")
            ui.button(
                "Start Test",
                icon="play_arrow",
                on_click=lambda _, s=station: ui.navigate.to(f"/launch?station={s.id}"),
            ).props("flat color=primary")
