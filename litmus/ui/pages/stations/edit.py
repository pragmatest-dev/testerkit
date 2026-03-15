"""Station edit page."""

from collections.abc import Callable

from nicegui import ui

from litmus.ui.shared.components import AutoSaver, setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_instrument_types,
    load_station_config,
    save_station,
)


@ui.page("/stations/{station_id}/edit")
def station_edit_page(station_id: str):
    """Station edit page with form interface."""
    config = load_station_config(station_id)

    if config:
        create_layout(f"Edit {config.name or station_id}")
    else:
        create_layout("Edit Station")

    if not config:
        with ui.column().classes("w-full p-6"):
            ui.label("Station not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Stations", "/stations").classes("text-blue-600 hover:underline")
        return

    # Mutable form data
    form_data = {
        "id": config.id or station_id,
        "name": config.name or "",
        "location": config.location or "",
        "description": config.description or "",
        "instruments": (
            {k: v.model_dump() for k, v in config.instruments.items()}
            if config.instruments else {}
        ),
    }

    # Get available instrument types for the dropdown
    instrument_types = discover_instrument_types()
    type_options = {t.type: t.name or t.type for t in instrument_types}

    # Auto-save
    def do_save():
        station_data = {
            "id": form_data["id"],
            "name": form_data["name"],
            "location": form_data["location"],
            "description": form_data["description"],
        }
        save_station(station_id, station_data, form_data["instruments"])

    saver = AutoSaver(do_save, delay=1.0)

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("edit").classes("text-slate-600")
                ui.label(f"Edit Station: {config.name or station_id}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2 items-center"):
                ui.label("Changes auto-saved").classes("text-sm text-slate-400 italic")
                ui.button(
                    "Back",
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to(f"/stations/{station_id}"),
                ).props("flat")

        # Tabs
        with ui.tabs().classes("w-full") as tabs:
            info_tab = ui.tab("Info", icon="info")
            instruments_tab = ui.tab("Instruments", icon="cable")

        setup_hash_sync_for_tabs(tabs, ["Info", "Instruments"])

        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
            with ui.tab_panel(info_tab):
                _render_info_tab(form_data, saver)

            with ui.tab_panel(instruments_tab):
                _render_instruments_tab(form_data, type_options, saver)

        ui.link("← Back to Station", f"/stations/{station_id}").classes(
            "text-blue-600 hover:underline mt-4"
        )


def _render_info_tab(form_data: dict, saver: AutoSaver):
    """Render the info edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Basic Information").classes("font-semibold mb-4")
            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input(
                    "Station ID",
                    form_data["id"],
                    readonly=True,
                )
                _labeled_input(
                    "Name",
                    form_data["name"],
                    on_change=lambda e: (
                        form_data.update({"name": e.value}),
                        saver.trigger(),
                    ),
                )
                _labeled_input(
                    "Location",
                    form_data["location"],
                    on_change=lambda e: (
                        form_data.update({"location": e.value}),
                        saver.trigger(),
                    ),
                )
                _labeled_textarea(
                    "Description",
                    form_data["description"],
                    on_change=lambda e: (
                        form_data.update({"description": e.value}),
                        saver.trigger(),
                    ),
                )


def _render_instruments_tab(form_data: dict, type_options: dict, saver: AutoSaver):
    """Render the instruments edit tab."""
    instruments = form_data["instruments"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Instruments").classes("font-semibold")

                def on_add_instrument(inst_name, inst_data):
                    form_data["instruments"][inst_name] = inst_data
                    saver.trigger()
                    ui.notify(f"Added instrument: {inst_name}", type="positive")

                ui.button(
                    "Add Instrument",
                    icon="add",
                    on_click=lambda: _show_add_instrument_dialog(type_options, on_add_instrument),
                ).props("flat color=primary dense")

            if instruments:
                for inst_name, inst_data in instruments.items():
                    _render_instrument_expansion(inst_name, inst_data, saver)
            else:
                ui.label(
                    "No instruments configured. Click 'Add Instrument' to add one."
                ).classes("text-slate-500 italic")


def _render_instrument_expansion(inst_name: str, inst_data: dict, saver: AutoSaver):
    """Render an instrument expansion panel."""
    mocked = inst_data.get("mock", False)
    with ui.expansion(inst_name, icon="cable").classes("w-full"):
        with ui.column().classes("gap-4 p-2"):
            with ui.row().classes("gap-4 items-end"):
                _labeled_input(
                    "Driver",
                    inst_data.get("driver", ""),
                    on_change=lambda e, d=inst_data: (
                        d.update({"driver": e.value}),
                        saver.trigger(),
                    ),
                )
                with ui.column().classes("gap-1 flex-1"):
                    ui.label("Resource (VISA)").classes("text-sm font-medium text-slate-700")
                    ui.input(
                        value=inst_data.get("resource", ""),
                        on_change=lambda e, d=inst_data: (
                            d.update({"resource": e.value}),
                            saver.trigger(),
                        ),
                    ).props("outlined dense").classes("w-full")
            with ui.row().classes("gap-4"):
                ui.checkbox(
                    "Mocked",
                    value=mocked,
                    on_change=lambda e, d=inst_data: (
                        d.update({"mock": e.value}),
                        saver.trigger(),
                    ),
                )
            if inst_data.get("description"):
                ui.label(inst_data["description"]).classes("text-sm text-slate-500")


# -----------------------------------------------------------------------------
# Form Components
# -----------------------------------------------------------------------------


def _labeled_input(label: str, value: str = "", readonly: bool = False, on_change=None):
    """Create a labeled input field."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        ui.input(value=value, on_change=on_change).props(props).classes("w-full")


def _labeled_textarea(label: str, value: str = "", on_change=None):
    """Create a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.textarea(value=value, on_change=on_change).props("outlined dense").classes("w-full")


# -----------------------------------------------------------------------------
# Dialogs
# -----------------------------------------------------------------------------


def _show_add_instrument_dialog(type_options: dict, on_add: Callable):
    """Show dialog to add a new instrument."""
    inst_form = {
        "name": "",
        "driver": "",
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
                    placeholder="e.g., dmm, psu",
                    on_change=lambda e: inst_form.update({"name": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Driver (Python import path)").classes(
                    "text-sm font-medium text-slate-700"
                )
                driver_input = ui.input(
                    placeholder="e.g., demo.drivers.DMM",
                    on_change=lambda e: inst_form.update({"driver": e.value}),
                ).props("outlined dense").classes("w-full")
            if type_options:
                with ui.column().classes("gap-1"):
                    ui.label("Or select from library").classes(
                        "text-xs text-slate-500"
                    )

                    def on_library_select(e, di=driver_input):
                        if e.value:
                            inst_form["driver"] = e.value
                            di.set_value(e.value)

                    ui.select(
                        options=type_options,
                        on_change=on_library_select,
                    ).props("outlined dense clearable").classes("w-full")
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
