"""New catalog entry creation page."""

import re

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    create_catalog_entry,
    discover_instrument_types,
)


@ui.page("/instruments/new")
def new_instrument_page():
    """Create a new instrument type definition."""
    create_layout("New Instrument")

    # Get existing instrument types to check for duplicates
    existing_types = {i.type for i in discover_instrument_types()}

    # Common Material icons for instruments
    icon_options = {
        "device_unknown": "device_unknown - Generic",
        "speed": "speed - Measurement",
        "power": "power - Power Supply",
        "memory": "memory - Electronics",
        "cable": "cable - Connectivity",
        "precision_manufacturing": "precision_manufacturing - Equipment",
        "settings_input_hdmi": "settings_input_hdmi - IO",
        "trending_up": "trending_up - Analysis",
        "waves": "waves - Signal",
    }

    # Form state
    form = {
        "type": "",
        "name": "",
        "description": "",
        "icon": "device_unknown",
    }
    validation = {
        "type_error": "",
        "name_error": "",
    }

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("items-center gap-2"):
            ui.icon("add_circle").classes("text-slate-600")
            ui.label("Create New Instrument Type").classes(
                "text-lg font-semibold text-slate-700"
            )

        # Form card
        with ui.card().classes("w-full max-w-xl"):
            with ui.card_section():
                ui.label("Instrument Information").classes("font-semibold mb-4")

                with ui.column().classes("gap-4 w-full"):
                    # Type
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Type").classes("text-sm font-medium text-slate-700")
                        ui.label(
                            "Unique identifier (lowercase, letters/numbers/underscores)"
                        ).classes("text-xs text-slate-400")
                        type_input = ui.input(
                            placeholder="e.g., dmm, psu, scope",
                        ).props("outlined dense").classes("w-full")
                        ui.label("").classes(
                            "text-xs text-red-500"
                        ).bind_text_from(validation, "type_error")

                        def validate_type(e):
                            value = e.value.lower().strip()
                            form["type"] = value
                            type_input.value = value

                            if not value:
                                validation["type_error"] = "Type is required"
                            elif not re.match(r"^[a-z][a-z0-9_]*$", value):
                                validation["type_error"] = (
                                    "Must start with letter, only contain "
                                    "letters, numbers, underscores"
                                )
                            elif value in existing_types:
                                validation["type_error"] = "Type already exists"
                            else:
                                validation["type_error"] = ""

                        type_input.on("change", validate_type)

                    # Name
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Name").classes("text-sm font-medium text-slate-700")
                        name_input = ui.input(
                            placeholder="e.g., Digital Multimeter",
                        ).props("outlined dense").classes("w-full")
                        ui.label("").classes(
                            "text-xs text-red-500"
                        ).bind_text_from(validation, "name_error")

                        def validate_name(e):
                            value = e.value.strip()
                            form["name"] = value
                            if not value:
                                validation["name_error"] = "Name is required"
                            else:
                                validation["name_error"] = ""

                        name_input.on("change", validate_name)

                    # Icon
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Icon").classes("text-sm font-medium text-slate-700")
                        with ui.row().classes("items-center gap-2"):
                            icon_preview = ui.icon(form["icon"]).classes(
                                "text-2xl text-slate-600"
                            )
                            ui.select(
                                options=icon_options,
                                value="device_unknown",
                                on_change=lambda e: (
                                    form.update({"icon": e.value}),
                                    icon_preview.set_name(e.value),
                                ),
                            ).props("outlined dense").classes("flex-1")

                    # Description
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Description").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.textarea(
                            placeholder="Brief description of the instrument type...",
                            on_change=lambda e: form.update(
                                {"description": e.value.strip()}
                            ),
                        ).props("outlined dense").classes("w-full")

            with ui.card_actions().classes("justify-end"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/instruments"),
                ).props("flat")

                def create():
                    # Validate
                    if not form["type"]:
                        validation["type_error"] = "Type is required"
                        return
                    if not form["name"]:
                        validation["name_error"] = "Name is required"
                        return
                    if validation["type_error"] or validation["name_error"]:
                        ui.notify("Please fix validation errors", type="warning")
                        return

                    # Create instrument
                    result = create_catalog_entry(
                        instrument_type=form["type"],
                        name=form["name"],
                        description=form["description"],
                        icon=form["icon"],
                    )

                    if result:
                        ui.notify(
                            f"Instrument '{result.name}' created successfully",
                            type="positive",
                        )
                        ui.navigate.to(f"/instruments/{result.type}/edit")
                    else:
                        ui.notify(
                            "Instrument type already exists",
                            type="negative",
                        )

                ui.button(
                    "Create Instrument",
                    icon="add",
                    on_click=create,
                ).props("color=primary")

        # Help text
        with ui.card().classes("w-full max-w-xl bg-blue-50"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("lightbulb").classes("text-blue-500 mt-0.5")
                    with ui.column().classes("gap-1"):
                        ui.label("About Catalog Entries").classes(
                            "font-medium text-blue-700"
                        )
                        ui.label(
                            "Catalog entries describe the capabilities of an instrument model. "
                            "Station configs reference these via catalog_ref."
                        ).classes("text-sm text-blue-600")
                        ui.label(
                            "After creating, add capabilities and SCPI commands in the editor."
                        ).classes("text-sm text-blue-600")

        ui.link("← Back to Instruments", "/instruments").classes(
            "text-blue-600 hover:underline"
        )
