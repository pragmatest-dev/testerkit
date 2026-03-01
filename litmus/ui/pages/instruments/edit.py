"""Catalog entry edit page."""

from nicegui import ui

from litmus.ui.shared.components import setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    load_catalog_entry_by_type,
    save_catalog_entry,
)


@ui.page("/instruments/{instrument_type}/edit")
def instrument_edit_page(instrument_type: str):
    """Instrument definition edit page."""
    entry = load_catalog_entry_by_type(instrument_type)

    if entry:
        create_layout(f"Edit {entry.name or instrument_type}")
    else:
        create_layout("Instrument Not Found")

    if not entry:
        with ui.column().classes("w-full p-6"):
            ui.label("Instrument not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Instruments", "/instruments").classes(
                "text-blue-600 hover:underline"
            )
        return

    # Form state — convert model to mutable dicts for NiceGUI binding
    form_data = {
        "instrument": {
            "type": entry.type or instrument_type,
            "name": entry.name or "",
            "description": entry.description or "",
            "icon": "device_unknown",
            "driver_class": "",
        },
        "capabilities": [cap.model_dump() for cap in entry.capabilities],
        "scpi_commands": {},
        "simulation": {},
    }

    from litmus.config.models import Direction, MeasurementFunction

    # Function and direction options — derived from model enums
    direction_options = [d.value for d in Direction]
    function_options = [f.value for f in MeasurementFunction]

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("edit").classes("text-slate-600")
                ui.label(f"Edit Instrument: {entry.name or instrument_type}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to(f"/instruments/{instrument_type}"),
                ).props("flat")

                def save_changes():
                    save_data = {
                        "instrument": form_data["instrument"],
                        "capabilities": form_data["capabilities"],
                    }
                    if form_data["scpi_commands"]:
                        save_data["scpi_commands"] = form_data["scpi_commands"]
                    if form_data["simulation"]:
                        save_data["simulation"] = form_data["simulation"]

                    if save_catalog_entry(instrument_type, save_data):
                        ui.notify("Instrument saved successfully", type="positive")
                        ui.navigate.to(f"/instruments/{instrument_type}")
                    else:
                        ui.notify("Failed to save instrument", type="negative")

                ui.button("Save", icon="save", on_click=save_changes).props(
                    "color=primary"
                )

        # Tabs
        with ui.tabs().classes("w-full") as tabs:
            info_tab = ui.tab("Info", icon="info")
            caps_tab = ui.tab("Capabilities", icon="tune")
            scpi_tab = ui.tab("SCPI Commands", icon="terminal")
            sim_tab = ui.tab("Simulation", icon="sim_card")

        setup_hash_sync_for_tabs(tabs, ["Info", "Capabilities", "SCPI Commands", "Simulation"])

        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
            with ui.tab_panel(info_tab):
                _render_info_tab(form_data)

            with ui.tab_panel(caps_tab):
                _render_capabilities_tab(
                    form_data, direction_options, function_options
                )

            with ui.tab_panel(scpi_tab):
                _render_scpi_tab(form_data)

            with ui.tab_panel(sim_tab):
                _render_simulation_tab(form_data)

        ui.link("← Back to Instrument", f"/instruments/{instrument_type}").classes(
            "text-blue-600 hover:underline mt-4"
        )


def _render_info_tab(form_data: dict):
    """Render the info edit tab."""
    inst = form_data["instrument"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Basic Information").classes("font-semibold mb-4")

            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input(
                    "Type",
                    inst["type"],
                    readonly=True,
                )
                _labeled_input(
                    "Name",
                    inst["name"],
                    on_change=lambda e: inst.update({"name": e.value}),
                )
                _labeled_textarea(
                    "Description",
                    inst["description"],
                    on_change=lambda e: inst.update({"description": e.value}),
                )
                _labeled_input(
                    "Icon",
                    inst["icon"],
                    placeholder="Material icon name (e.g., speed, power)",
                    on_change=lambda e: inst.update({"icon": e.value}),
                )
                _labeled_input(
                    "Driver Class",
                    inst.get("driver_class", ""),
                    placeholder="e.g., litmus.instruments.dmm.DMM",
                    on_change=lambda e: inst.update({"driver_class": e.value}),
                )


def _render_capabilities_tab(
    form_data: dict,
    direction_options: list,
    function_options: list,
):
    """Render the capabilities edit tab."""
    capabilities = form_data["capabilities"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Capabilities").classes("font-semibold")
                    ui.badge(f"{len(capabilities)} capabilities").props("outline")

                ui.button(
                    "Add Capability",
                    icon="add",
                    on_click=lambda: _show_add_capability_dialog(
                        form_data,
                        direction_options,
                        function_options,
                        caps_container,
                    ),
                ).props("flat color=primary dense")

        caps_container = ui.column().classes("w-full gap-2")

        def refresh_caps():
            caps_container.clear()
            with caps_container:
                if capabilities:
                    for i, cap in enumerate(capabilities):
                        _render_capability_card(
                            i,
                            cap,
                            form_data,
                            direction_options,
                            function_options,
                            refresh_caps,
                        )
                else:
                    ui.label(
                        "No capabilities defined. Click 'Add Capability' to create one."
                    ).classes("text-slate-500 italic")

        refresh_caps()


def _render_capability_card(
    index: int,
    cap: dict,
    form_data: dict,
    direction_options: list,
    function_options: list,
    refresh_callback,
):
    """Render a capability card with edit/delete options."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("tune").classes("text-slate-500")
                    ui.label(cap.get("name", "")).classes("font-semibold font-mono")
                    ui.badge(cap.get("direction", "")).props("outline")
                    ui.badge(cap.get("function", "")).props("outline color=blue")
                ui.button(
                    icon="delete",
                    on_click=lambda _, i=index: _delete_capability(
                        form_data["capabilities"], i, refresh_callback
                    ),
                ).props("flat dense round color=red")

        with ui.expansion("Edit Capability", icon="edit").classes("w-full"):
            with ui.column().classes("gap-4 p-2"):
                _labeled_input(
                    "Name",
                    cap.get("name", ""),
                    on_change=lambda e, c=cap: c.update({"name": e.value}),
                )
                _labeled_input(
                    "Description",
                    cap.get("description", ""),
                    on_change=lambda e, c=cap: c.update({"description": e.value}),
                )

                with ui.row().classes("gap-4 w-full"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Function").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options=function_options,
                            value=cap.get("function", "dc_voltage"),
                            on_change=lambda e, c=cap: c.update({"function": e.value}),
                        ).props("outlined dense").classes("w-full")

                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Direction").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options=direction_options,
                            value=cap.get("direction", "input"),
                            on_change=lambda e, c=cap: c.update({"direction": e.value}),
                        ).props("outlined dense").classes("w-full")


def _render_scpi_tab(form_data: dict):
    """Render the SCPI commands edit tab."""
    scpi_commands = form_data["scpi_commands"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("SCPI Commands").classes("font-semibold")
                    ui.badge(f"{len(scpi_commands)} commands").props("outline")

                ui.button(
                    "Add Command",
                    icon="add",
                    on_click=lambda: _show_add_scpi_dialog(form_data, scpi_container),
                ).props("flat color=primary dense")

        scpi_container = ui.column().classes("w-full gap-2")

        def refresh_scpi():
            scpi_container.clear()
            with scpi_container:
                if scpi_commands:
                    for cmd_name, cmd_value in scpi_commands.items():
                        _render_scpi_row(cmd_name, cmd_value, form_data, refresh_scpi)
                else:
                    ui.label(
                        "No SCPI commands defined. Click 'Add Command' to create one."
                    ).classes("text-slate-500 italic")

        refresh_scpi()


def _render_scpi_row(cmd_name: str, cmd_value: str, form_data: dict, refresh_callback):
    """Render a SCPI command row."""
    with ui.row().classes(
        "items-center justify-between w-full py-2 px-3 bg-slate-50 rounded"
    ):
        with ui.row().classes("items-center gap-4 flex-1"):
            ui.label(cmd_name).classes("font-semibold font-mono w-40")
            ui.input(
                value=cmd_value,
                on_change=lambda e, n=cmd_name: form_data["scpi_commands"].update(
                    {n: e.value}
                ),
            ).props("outlined dense").classes("flex-1")

        def delete_cmd(name=cmd_name):
            del form_data["scpi_commands"][name]
            refresh_callback()

        ui.button(icon="delete", on_click=delete_cmd).props("flat dense round color=red")


def _render_simulation_tab(form_data: dict):
    """Render the simulation edit tab."""
    simulation = form_data.setdefault("simulation", {})

    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Simulation Configuration").classes("font-semibold mb-4")

            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input(
                    "IDN Response",
                    simulation.get("idn", ""),
                    placeholder="e.g., Litmus,SimDMM,SN001,1.0",
                    on_change=lambda e: simulation.update({"idn": e.value}),
                )

    with ui.card().classes("w-full mt-4"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Default Values").classes("font-semibold")

                def add_default():
                    defaults = simulation.setdefault("defaults", {})
                    defaults[f"value_{len(defaults)}"] = 0.0
                    defaults_container.update()

                ui.button("Add Default", icon="add", on_click=add_default).props(
                    "flat color=primary dense"
                )

        defaults_container = ui.column().classes("w-full gap-2")

        defaults = simulation.get("defaults", {})
        if defaults:
            for key, value in defaults.items():
                with ui.row().classes("items-center gap-4 w-full"):
                    ui.input(
                        value=key,
                        on_change=lambda e, k=key: _rename_default(
                            simulation, k, e.value
                        ),
                    ).props("outlined dense").classes("w-40")
                    ui.number(
                        value=value,
                        on_change=lambda e, k=key: simulation["defaults"].update(
                            {k: e.value}
                        ),
                    ).props("outlined dense").classes("flex-1")
        else:
            with defaults_container:
                ui.label("No default values. Click 'Add Default' to create one.").classes(
                    "text-slate-500 italic"
                )


def _rename_default(simulation: dict, old_key: str, new_key: str):
    """Rename a default value key."""
    if old_key != new_key and old_key in simulation.get("defaults", {}):
        value = simulation["defaults"].pop(old_key)
        simulation["defaults"][new_key] = value


def _delete_capability(capabilities: list, index: int, refresh_callback):
    """Delete a capability."""
    if 0 <= index < len(capabilities):
        del capabilities[index]
        refresh_callback()


def _show_add_capability_dialog(
    form_data: dict,
    direction_options: list,
    function_options: list,
    container,
):
    """Show dialog to add a new capability."""
    cap_form = {
        "name": "",
        "description": "",
        "function": "dc_voltage",
        "direction": "input",
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Capability").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input(
                "Name",
                on_change=lambda e: cap_form.update({"name": e.value}),
            )
            _labeled_input(
                "Description",
                on_change=lambda e: cap_form.update({"description": e.value}),
            )
            with ui.column().classes("gap-1"):
                ui.label("Function").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options=function_options,
                    value="dc_voltage",
                    on_change=lambda e: cap_form.update({"function": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Direction").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options=direction_options,
                    value="input",
                    on_change=lambda e: cap_form.update({"direction": e.value}),
                ).props("outlined dense").classes("w-full")
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not cap_form["name"]:
                    ui.notify("Name is required", type="warning")
                    return

                new_cap = {
                    "name": cap_form["name"],
                    "function": cap_form["function"],
                    "direction": cap_form["direction"],
                }
                if cap_form["description"]:
                    new_cap["description"] = cap_form["description"]

                form_data["capabilities"].append(new_cap)
                dialog.close()
                ui.notify(f"Added capability: {cap_form['name']}", type="positive")

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


def _show_add_scpi_dialog(form_data: dict, container):
    """Show dialog to add a new SCPI command."""
    cmd_form = {"name": "", "command": ""}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add SCPI Command").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input(
                "Command Name",
                placeholder="e.g., measure_voltage_dc",
                on_change=lambda e: cmd_form.update({"name": e.value}),
            )
            _labeled_input(
                "SCPI Command",
                placeholder="e.g., MEAS:VOLT:DC?",
                on_change=lambda e: cmd_form.update({"command": e.value}),
            )
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not cmd_form["name"]:
                    ui.notify("Command name is required", type="warning")
                    return
                if not cmd_form["command"]:
                    ui.notify("SCPI command is required", type="warning")
                    return

                form_data["scpi_commands"][cmd_form["name"]] = cmd_form["command"]
                dialog.close()
                ui.notify(f"Added command: {cmd_form['name']}", type="positive")

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


# -----------------------------------------------------------------------------
# Form Components
# -----------------------------------------------------------------------------


def _labeled_input(
    label: str,
    value: str = "",
    placeholder: str = "",
    readonly: bool = False,
    on_change=None,
):
    """Create a labeled input field."""
    with ui.column().classes("gap-1 flex-1"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        ui.input(value=value, placeholder=placeholder, on_change=on_change).props(
            props
        ).classes("w-full")


def _labeled_textarea(label: str, value: str = "", on_change=None):
    """Create a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.textarea(value=value, on_change=on_change).props("outlined dense").classes(
            "w-full"
        )
