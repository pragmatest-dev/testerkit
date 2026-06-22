"""New part creation page."""

from nicegui import ui

from litmus.ui.shared.components import validate_resource_id
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import create_part, discover_parts


@ui.page("/parts/new")
def new_part_page():
    """Create a new part."""
    create_layout("New Part")

    # Get existing part IDs to check for duplicates
    existing_ids = {p["id"] for p in discover_parts()}

    # Form state
    form = {
        "part_id": "",
        "name": "",
        "description": "",
    }
    validation = {
        "id_error": "",
        "name_error": "",
    }

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("items-center gap-2"):
            ui.icon("add_circle").classes("text-slate-600")
            ui.label("Create New Part").classes("text-lg font-semibold text-slate-700")

        # Form card
        with ui.card().classes("w-full max-w-xl"):
            with ui.card_section():
                ui.label("Part Information").classes("font-semibold mb-4")

                with ui.column().classes("gap-4 w-full"):
                    # Part ID
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Part ID").classes("text-sm font-medium text-slate-700")
                        ui.label(
                            "Unique identifier (lowercase, letters/numbers/hyphens only)"
                        ).classes("text-xs text-slate-400")
                        id_input = (
                            ui.input(
                                placeholder="e.g., tps54302",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                        ui.label("").classes("text-xs text-red-500").bind_text_from(
                            validation, "id_error"
                        )

                        def validate_id(e):
                            value = e.value.lower().strip()
                            form["part_id"] = value
                            id_input.value = value
                            validation["id_error"] = validate_resource_id(
                                value, existing_ids, "Part ID"
                            )

                        id_input.on("change", validate_id)

                    # Name
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Name").classes("text-sm font-medium text-slate-700")
                        name_input = (
                            ui.input(
                                placeholder="e.g., TPS54302 Buck Converter",
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

                    # Description
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Description").classes("text-sm font-medium text-slate-700")
                        ui.label("Optional").classes("text-xs text-slate-400")
                        desc_input = (
                            ui.textarea(
                                placeholder="Brief description of the part...",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )

                        def update_description(e):
                            value = e.value or ""
                            form["description"] = value.strip()

                        desc_input.on("change", update_description)

            with ui.card_actions().classes("justify-end"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/parts"),
                ).props("flat")

                def create():
                    # Validate
                    if not form["part_id"]:
                        validation["id_error"] = "Part ID is required"
                        return
                    if not form["name"]:
                        validation["name_error"] = "Name is required"
                        return
                    if validation["id_error"] or validation["name_error"]:
                        ui.notify("Please fix validation errors", type="warning")
                        return

                    # Create part
                    result = create_part(
                        part_id=form["part_id"],
                        name=form["name"],
                        description=form["description"],
                    )

                    if result:
                        ui.notify(
                            f"Part '{result['name']}' created successfully",
                            type="positive",
                        )
                        ui.navigate.to(f"/parts/{result['id']}/edit")
                    else:
                        ui.notify(
                            "Part ID already exists",
                            type="negative",
                        )

                ui.button(
                    "Create Part",
                    icon="add",
                    on_click=create,
                ).props("color=primary")

        # Help text
        with ui.card().classes("w-full max-w-xl bg-blue-50"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("lightbulb").classes("text-blue-500 mt-0.5")
                    with ui.column().classes("gap-1"):
                        ui.label("What happens next?").classes("font-medium text-blue-700")
                        ui.label(
                            "After creating the part, you'll be taken to the edit page "
                            "where you can add characteristics, test requirements, and more."
                        ).classes("text-sm text-blue-600")
                        ui.label(
                            "You can also upload a datasheet to help extract specifications."
                        ).classes("text-sm text-blue-600")

        ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline")
