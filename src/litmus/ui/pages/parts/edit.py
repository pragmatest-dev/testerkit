"""Part edit page."""

from collections.abc import Callable

from nicegui import ui

from litmus.ui.shared.components import (
    AutoSaver,
    data_table,
    labeled_input,
    labeled_textarea,
    setup_hash_sync_for_tabs,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_parts, save_part


@ui.page("/parts/{part_id}/edit")
def part_edit_page(part_id: str):
    """Part edit page with form interface."""
    parts = discover_parts()
    part = next((p for p in parts if p["id"] == part_id), None)

    create_layout(f"Edit {part['name']}" if part else "Edit Part")

    if not part:
        with ui.column().classes("w-full p-6"):
            ui.label("Part not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline")
        return

    # Mutable form data
    form_data = {
        "name": part.get("name", ""),
        "description": part.get("description", ""),
        "revision": part.get("revision", ""),
        "pins": list(part.get("pins") or []),
        "characteristics": dict(part.get("characteristics") or {}),
    }

    # Auto-save
    def do_save():
        updated = {
            "id": part_id,
            "name": form_data["name"],
            "description": form_data["description"],
            "revision": form_data["revision"],
            "characteristics": form_data["characteristics"],
            "pins": form_data["pins"],
        }
        save_part(part_id, updated)

    saver = AutoSaver(do_save, delay=1.0)

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("edit").classes("text-slate-600")
                ui.label(f"Edit Part: {part['name']}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2 items-center"):
                ui.label("Changes auto-saved").classes("text-sm text-slate-400 italic")
                ui.button(
                    "Back",
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to(f"/parts/{part_id}"),
                ).props("flat")

        # Tabs
        with ui.tabs().classes("w-full") as tabs:
            info_tab = ui.tab("Info", icon="info")
            pins_tab = ui.tab("Pins", icon="memory")
            chars_tab = ui.tab("Characteristics", icon="tune")

        setup_hash_sync_for_tabs(tabs, ["Info", "Pins", "Characteristics"])

        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
            with ui.tab_panel(info_tab):
                _render_info_tab(part_id, form_data, saver)

            with ui.tab_panel(pins_tab):
                _render_pins_tab(form_data, saver)

            with ui.tab_panel(chars_tab):
                _render_characteristics_tab(form_data, saver)

        ui.link("← Back to Part", f"/parts/{part_id}").classes("text-blue-600 hover:underline mt-4")


def _render_info_tab(part_id: str, form_data: dict, saver: AutoSaver):
    """Render the info edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Basic Information").classes("font-semibold mb-4")
            with ui.column().classes("gap-4 w-full max-w-xl"):
                labeled_input("Part ID", part_id, readonly=True)
                labeled_input(
                    "Name",
                    form_data["name"],
                    on_change=lambda e: (
                        form_data.update({"name": e.value}),
                        saver.trigger(),
                    ),
                )
                labeled_input(
                    "Revision",
                    form_data["revision"],
                    on_change=lambda e: (
                        form_data.update({"revision": e.value}),
                        saver.trigger(),
                    ),
                )
                labeled_textarea(
                    "Description",
                    form_data["description"],
                    on_change=lambda e: (
                        form_data.update({"description": e.value}),
                        saver.trigger(),
                    ),
                )


def _render_pins_tab(form_data: dict, saver: AutoSaver):
    """Render the pins edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Pin Definitions").classes("font-semibold")

                def on_add_pin(pin):
                    form_data["pins"].append(pin)
                    saver.trigger()
                    ui.notify(f"Added pin: {pin['name']}", type="positive")

                ui.button(
                    "Add Pin",
                    icon="add",
                    on_click=lambda: _show_add_pin_dialog(on_add_pin),
                ).props("flat color=primary dense")

            if form_data["pins"]:
                columns = [
                    {"name": "name", "label": "Name", "field": "name", "align": "left"},
                    {"name": "type", "label": "Type", "field": "type"},
                    {"name": "net", "label": "Net", "field": "net"},
                ]
                rows = [
                    {"name": p.get("name", ""), "type": p.get("type", ""), "net": p.get("net", "")}
                    for p in form_data["pins"]
                ]
                data_table(columns=columns, rows=rows, row_key="name")
            else:
                ui.label("No pins defined. Click 'Add Pin' to add one.").classes(
                    "text-slate-500 italic"
                )


def _render_characteristics_tab(form_data: dict, saver: AutoSaver):
    """Render the characteristics edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Characteristics").classes("font-semibold")

                def on_add_char(name, data):
                    form_data["characteristics"][name] = data
                    saver.trigger()
                    ui.notify(f"Added characteristic: {name}", type="positive")

                ui.button(
                    "Add Characteristic",
                    icon="add",
                    on_click=lambda: _show_add_char_dialog(on_add_char),
                ).props("flat color=primary dense")

            characteristics = form_data["characteristics"]
            if characteristics:
                for char_name, char_data in characteristics.items():
                    with ui.expansion(char_name, icon="tune").classes("w-full"):
                        with ui.grid(columns=3).classes("gap-4 p-2"):
                            direction = char_data.get("direction", "")
                            labeled_input("Function", char_data.get("function", ""), readonly=True)
                            labeled_input("Direction", direction, readonly=True)
                            labeled_input("Units", char_data.get("units", ""), readonly=True)

                        conditions = char_data.get("conditions", [])
                        if conditions:
                            ui.label("Conditions").classes("font-semibold text-sm mt-4 px-2")
                            for i, cond in enumerate(conditions):
                                _render_condition(i, cond)
            else:
                ui.label(
                    "No characteristics defined. Click 'Add Characteristic' to add one."
                ).classes("text-slate-500 italic")


def _render_condition(index: int, cond: dict):
    """Render a condition card (key → range/value dict)."""
    with ui.card().classes("w-full mt-2"):
        with ui.card_section():
            ui.label(f"Condition {index + 1}").classes("text-xs text-slate-500 font-semibold")
            with ui.row().classes("gap-4 mt-2 flex-wrap"):
                if isinstance(cond, dict):
                    for key, spec in cond.items():
                        if isinstance(spec, dict):
                            parts = [key]
                            if "min" in spec and "max" in spec:
                                parts.append(f"{spec['min']}–{spec['max']}")
                            elif "min" in spec:
                                parts.append(f"≥ {spec['min']}")
                            elif "max" in spec:
                                parts.append(f"≤ {spec['max']}")
                            elif "value" in spec:
                                parts.append(str(spec["value"]))
                            elif "values" in spec:
                                parts.append(str(spec["values"]))
                            if spec.get("units"):
                                parts.append(spec["units"])
                            ui.chip(" ".join(parts)).props("outline")
                        else:
                            ui.chip(f"{key}: {spec}").props("outline")


def _labeled_select(label: str, options, value=None, on_change=None):
    """Create a labeled select."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.select(options=options, value=value, on_change=on_change).props(
            "outlined dense"
        ).classes("w-full")


# -----------------------------------------------------------------------------
# Dialogs (local to this page)
# -----------------------------------------------------------------------------


def _show_add_pin_dialog(on_add: Callable):
    """Show dialog to add a new pin."""
    pin_form = {"name": "", "type": "signal", "net": ""}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Pin").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            labeled_input("Name", on_change=lambda e: pin_form.update({"name": e.value}))
            _labeled_select(
                "Type",
                options=["signal", "power", "ground", "nc"],
                value="signal",
                on_change=lambda e: pin_form.update({"type": e.value}),
            )
            labeled_input("Net", on_change=lambda e: pin_form.update({"net": e.value}))
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not pin_form["name"]:
                    ui.notify("Pin name is required", type="warning")
                    return
                on_add(dict(pin_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


def _show_add_char_dialog(on_add: Callable):
    """Show dialog to add a new characteristic."""
    from litmus.models.enums import Direction, MeasurementFunction

    function_options = [f.value for f in MeasurementFunction]
    char_form = {"name": "", "function": "dc_voltage", "direction": "output", "units": "V"}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Characteristic").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            labeled_input("Name", on_change=lambda e: char_form.update({"name": e.value}))
            _labeled_select(
                "Function",
                options=function_options,
                value="dc_voltage",
                on_change=lambda e: char_form.update({"function": e.value}),
            )
            _labeled_select(
                "Direction",
                options=[d.value for d in Direction],
                value="output",
                on_change=lambda e: char_form.update({"direction": e.value}),
            )
            labeled_input(
                "Units", value="V", on_change=lambda e: char_form.update({"units": e.value})
            )
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not char_form["name"]:
                    ui.notify("Characteristic name is required", type="warning")
                    return
                name = char_form.pop("name")
                on_add(name, dict(char_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()
