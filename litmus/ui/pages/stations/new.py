"""New station creation page."""

import re
from collections.abc import Callable

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    create_station,
    discover_instrument_types,
    discover_stations,
    save_station,
)


@ui.page("/stations/new")
def new_station_page():
    """Create a new station."""
    create_layout("New Station")

    # Get existing station IDs to check for duplicates
    existing_ids = {s.id for s in discover_stations()}

    # Get available instrument types
    instrument_types = discover_instrument_types()
    type_options = {t.type: t.name or t.type for t in instrument_types}

    # Form state
    form = {
        "station_id": "",
        "name": "",
        "location": "",
        "description": "",
        "instruments": {},
    }
    validation = {
        "id_error": "",
        "name_error": "",
    }

    # For reactively updating the instruments list
    instruments_container = None

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("items-center gap-2"):
            ui.icon("add_circle").classes("text-slate-600")
            ui.label("Create New Station").classes("text-lg font-semibold text-slate-700")

        # Form card
        with ui.card().classes("w-full max-w-2xl"):
            with ui.card_section():
                ui.label("Station Information").classes("font-semibold mb-4")

                with ui.column().classes("gap-4 w-full"):
                    # Station ID
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Station ID").classes("text-sm font-medium text-slate-700")
                        ui.label(
                            "Unique identifier (lowercase, letters/numbers/hyphens only)"
                        ).classes("text-xs text-slate-400")
                        id_input = (
                            ui.input(
                                placeholder="e.g., bench-1, lab-station-a",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                        ui.label("").classes("text-xs text-red-500").bind_text_from(
                            validation, "id_error"
                        )

                        def validate_id(e):
                            value = e.value.lower().strip()
                            form["station_id"] = value
                            id_input.value = value

                            if not value:
                                validation["id_error"] = "Station ID is required"
                            elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", value):
                                validation["id_error"] = (
                                    "Must start/end with letter or number, "
                                    "only contain letters, numbers, hyphens"
                                )
                            elif value in existing_ids:
                                validation["id_error"] = "Station ID already exists"
                            else:
                                validation["id_error"] = ""

                        id_input.on("change", validate_id)

                    # Name
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Name").classes("text-sm font-medium text-slate-700")
                        name_input = (
                            ui.input(
                                placeholder="e.g., Engineering Lab Bench 1",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                        ui.label("").classes("text-xs text-red-500").bind_text_from(
                            validation, "name_error"
                        )

                        def validate_name(e):
                            value = e.value.strip()
                            form["name"] = value
                            if not value:
                                validation["name_error"] = "Name is required"
                            else:
                                validation["name_error"] = ""

                        name_input.on("change", validate_name)

                    # Location
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Location").classes("text-sm font-medium text-slate-700")
                        ui.label("Optional - physical location").classes("text-xs text-slate-400")
                        ui.input(
                            placeholder="e.g., Building A, Room 101",
                            on_change=lambda e: form.update({"location": e.value.strip()}),
                        ).props("outlined dense").classes("w-full")

                    # Description
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Description").classes("text-sm font-medium text-slate-700")
                        ui.textarea(
                            placeholder="Brief description of the station...",
                            on_change=lambda e: form.update({"description": e.value.strip()}),
                        ).props("outlined dense").classes("w-full")

            # Instruments section
            with ui.card_section():
                with ui.row().classes("items-center justify-between w-full mb-4"):
                    ui.label("Instruments").classes("font-semibold")
                    ui.label("Optional - add instruments after creating").classes(
                        "text-xs text-slate-400"
                    )

                    def on_add_instrument(inst_name, inst_data):
                        form["instruments"][inst_name] = inst_data
                        _refresh_instruments_list()

                    ui.button(
                        "Add Instrument",
                        icon="add",
                        on_click=lambda: _show_add_instrument_dialog(
                            type_options, on_add_instrument
                        ),
                    ).props("flat color=primary dense")

                instruments_container = ui.column().classes("w-full gap-2")

                def _refresh_instruments_list():
                    instruments_container.clear()
                    with instruments_container:
                        if form["instruments"]:
                            for inst_name, inst_data in form["instruments"].items():
                                _render_instrument_row(
                                    inst_name, inst_data, form, _refresh_instruments_list
                                )
                        else:
                            ui.label(
                                "No instruments added yet. You can add them now or "
                                "after creating the station."
                            ).classes("text-slate-500 italic")

                _refresh_instruments_list()

            with ui.card_actions().classes("justify-end"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/stations"),
                ).props("flat")

                def create():
                    # Validate
                    if not form["station_id"]:
                        validation["id_error"] = "Station ID is required"
                        return
                    if not form["name"]:
                        validation["name_error"] = "Name is required"
                        return
                    if validation["id_error"] or validation["name_error"]:
                        ui.notify("Please fix validation errors", type="warning")
                        return

                    # Create station
                    result = create_station(
                        station_id=form["station_id"],
                        name=form["name"],
                        location=form["location"],
                        description=form["description"],
                    )

                    if result:
                        # If instruments were added, save them too
                        if form["instruments"]:
                            station_data = {
                                "id": form["station_id"],
                                "name": form["name"],
                            }
                            if form["location"]:
                                station_data["location"] = form["location"]
                            if form["description"]:
                                station_data["description"] = form["description"]
                            save_station(
                                form["station_id"],
                                station_data,
                                form["instruments"],
                            )

                        ui.notify(
                            f"Station '{result.name}' created successfully",
                            type="positive",
                        )
                        ui.navigate.to(f"/stations/{result.id}")
                    else:
                        ui.notify(
                            "Station ID already exists",
                            type="negative",
                        )

                ui.button(
                    "Create Station",
                    icon="add",
                    on_click=create,
                ).props("color=primary")

        # Help text
        with ui.card().classes("w-full max-w-2xl bg-blue-50"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("lightbulb").classes("text-blue-500 mt-0.5")
                    with ui.column().classes("gap-1"):
                        ui.label("About Stations").classes("font-medium text-blue-700")
                        ui.label(
                            "A station represents a physical test setup with instruments. "
                            "Products are matched to stations based on instrument capabilities."
                        ).classes("text-sm text-blue-600")
                        ui.label("You can add instruments now or edit the station later.").classes(
                            "text-sm text-blue-600"
                        )

        ui.link("← Back to Stations", "/stations").classes("text-blue-600 hover:underline")


def _render_instrument_row(inst_name: str, inst_data: dict, form: dict, refresh_callback):
    """Render an instrument row with remove button."""
    with ui.row().classes("items-center justify-between w-full py-2 px-3 bg-slate-50 rounded"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("cable").classes("text-slate-400")
            with ui.column().classes("gap-0"):
                ui.label(inst_name).classes("font-medium")
                ui.label(inst_data.get("type", "")).classes("text-xs text-slate-500")
        with ui.row().classes("items-center gap-2"):
            if inst_data.get("mock"):
                ui.badge("Mocked", color="blue").props("outline dense")
            if inst_data.get("resource"):
                ui.badge(inst_data["resource"], color="grey").props("outline dense")

            def remove(name=inst_name):
                del form["instruments"][name]
                refresh_callback()

            ui.button(icon="close", on_click=remove).props("flat dense round")


def _show_add_instrument_dialog(type_options: dict, on_add: Callable):
    """Show dialog to add a new instrument."""
    inst_form = {
        "name": "",
        "type": list(type_options.keys())[0] if type_options else "",
        "resource": "",
        "mock": False,
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Instrument").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            with ui.column().classes("gap-1"):
                ui.label("Name").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., dmm1, psu1",
                    on_change=lambda e: inst_form.update({"name": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Type").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options=type_options,
                    value=inst_form["type"],
                    on_change=lambda e: inst_form.update({"type": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Resource (VISA address)").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., TCPIP::192.168.1.100::INSTR",
                    on_change=lambda e: inst_form.update({"resource": e.value}),
                ).props("outlined dense").classes("w-full")
            ui.checkbox(
                "Mocked",
                on_change=lambda e: inst_form.update({"mock": e.value}),
            )
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not inst_form["name"]:
                    ui.notify("Instrument name is required", type="warning")
                    return
                name = inst_form.pop("name")
                on_add(name, dict(inst_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()
