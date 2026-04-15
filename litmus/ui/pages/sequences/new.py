"""New sequence creation page."""

import re
from typing import Literal, cast

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    create_sequence,
    discover_products,
    discover_sequences,
)


@ui.page("/sequences/new")
def new_sequence_page():
    """Create a new test sequence."""
    create_layout("New Sequence")

    # Get existing sequence IDs to check for duplicates
    existing_ids = {s.id for s in discover_sequences()}

    # Get available products for product family dropdown
    products = discover_products()
    product_options = {"": "-- Select Product Family --"}
    product_options.update({p["id"]: p.get("name", p["id"]) for p in products})

    # Test phase options
    phase_options = {
        "validation": "Validation - Initial design verification",
        "characterization": "Characterization - Performance analysis",
        "production": "Production - Manufacturing test",
    }

    # Form state
    form = {
        "sequence_id": "",
        "name": "",
        "product_family": "",
        "test_phase": "validation",
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
            ui.label("Create New Test Sequence").classes("text-lg font-semibold text-slate-700")

        # Form card
        with ui.card().classes("w-full max-w-xl"):
            with ui.card_section():
                ui.label("Sequence Information").classes("font-semibold mb-4")

                with ui.column().classes("gap-4 w-full"):
                    # Sequence ID
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Sequence ID").classes("text-sm font-medium text-slate-700")
                        ui.label(
                            "Unique identifier (lowercase, letters/numbers/hyphens only)"
                        ).classes("text-xs text-slate-400")
                        id_input = (
                            ui.input(
                                placeholder="e.g., tps54302-validation",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                        ui.label("").classes("text-xs text-red-500").bind_text_from(
                            validation, "id_error"
                        )

                        def validate_id(e):
                            value = e.value.lower().strip()
                            form["sequence_id"] = value
                            id_input.value = value

                            if not value:
                                validation["id_error"] = "Sequence ID is required"
                            elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", value):
                                validation["id_error"] = (
                                    "Must start/end with letter or number, "
                                    "only contain letters, numbers, hyphens"
                                )
                            elif value in existing_ids:
                                validation["id_error"] = "Sequence ID already exists"
                            else:
                                validation["id_error"] = ""

                        id_input.on("change", validate_id)

                    # Name
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Name").classes("text-sm font-medium text-slate-700")
                        name_input = (
                            ui.input(
                                placeholder="e.g., TPS54302 Validation Suite",
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

                    # Product family
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Product Family").classes("text-sm font-medium text-slate-700")
                        ui.label("Associate this sequence with a product (optional)").classes(
                            "text-xs text-slate-400"
                        )
                        ui.select(
                            options=product_options,
                            value="",
                            on_change=lambda e: form.update({"product_family": e.value}),
                        ).props("outlined dense").classes("w-full")

                    # Test phase
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Test Phase").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options=phase_options,
                            value="validation",
                            on_change=lambda e: form.update({"test_phase": e.value}),
                        ).props("outlined dense").classes("w-full")

                    # Description
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Description").classes("text-sm font-medium text-slate-700")
                        ui.textarea(
                            placeholder="Brief description of what this sequence tests...",
                            on_change=lambda e: form.update({"description": e.value.strip()}),
                        ).props("outlined dense").classes("w-full")

            with ui.card_actions().classes("justify-end"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/sequences"),
                ).props("flat")

                def create():
                    # Validate
                    if not form["sequence_id"]:
                        validation["id_error"] = "Sequence ID is required"
                        return
                    if not form["name"]:
                        validation["name_error"] = "Name is required"
                        return
                    if validation["id_error"] or validation["name_error"]:
                        ui.notify("Please fix validation errors", type="warning")
                        return

                    # Create sequence
                    test_phase = cast(
                        Literal["validation", "characterization", "production"],
                        form["test_phase"],
                    )
                    result = create_sequence(
                        sequence_id=form["sequence_id"],
                        name=form["name"],
                        product_family=form["product_family"],
                        test_phase=test_phase,
                        description=form["description"],
                    )

                    if result:
                        ui.notify(
                            f"Sequence '{result.name}' created successfully",
                            type="positive",
                        )
                        ui.navigate.to(f"/sequences/{result.id}")
                    else:
                        ui.notify(
                            "Sequence ID already exists",
                            type="negative",
                        )

                ui.button(
                    "Create Sequence",
                    icon="add",
                    on_click=create,
                ).props("color=primary")

        # Help text
        with ui.card().classes("w-full max-w-xl bg-blue-50"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("lightbulb").classes("text-blue-500 mt-0.5")
                    with ui.column().classes("gap-1"):
                        ui.label("What is a Sequence?").classes("font-medium text-blue-700")
                        ui.label(
                            "A test sequence defines the order and configuration of "
                            "tests to run. Each step can reference a test file, "
                            "specify limits, and configure retry behavior."
                        ).classes("text-sm text-blue-600")
                        ui.label(
                            "After creating the sequence, you can edit it to add steps."
                        ).classes("text-sm text-blue-600")

        ui.link("← Back to Sequences", "/sequences").classes("text-blue-600 hover:underline")
