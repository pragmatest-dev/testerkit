"""New fixture creation page."""

import re

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    create_fixture,
    discover_fixtures,
    discover_products,
)


@ui.page("/fixtures/new")
def new_fixture_page():
    """Create a new fixture."""
    create_layout("New Fixture")

    # Get existing fixture IDs to check for duplicates
    existing_ids = {f.id for f in discover_fixtures()}

    # Get available products
    products = discover_products()
    product_options = {"": "-- Select Product --"}
    product_options.update({p["id"]: p.get("name", p["id"]) for p in products})

    # Form state
    form = {
        "fixture_id": "",
        "name": "",
        "product_id": "",
        "product_revision": "",
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
            ui.label("Create New Fixture").classes("text-lg font-semibold text-slate-700")

        # Form card
        with ui.card().classes("w-full max-w-xl"):
            with ui.card_section():
                ui.label("Fixture Information").classes("font-semibold mb-4")

                with ui.column().classes("gap-4 w-full"):
                    # Fixture ID
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Fixture ID").classes("text-sm font-medium text-slate-700")
                        ui.label(
                            "Unique identifier (lowercase, letters/numbers/hyphens only)"
                        ).classes("text-xs text-slate-400")
                        id_input = (
                            ui.input(
                                placeholder="e.g., tps54302-fixture-a",
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                        ui.label("").classes("text-xs text-red-500").bind_text_from(
                            validation, "id_error"
                        )

                        def validate_id(e):
                            value = e.value.lower().strip()
                            form["fixture_id"] = value
                            id_input.value = value

                            if not value:
                                validation["id_error"] = "Fixture ID is required"
                            elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", value):
                                validation["id_error"] = (
                                    "Must start/end with letter or number, "
                                    "only contain letters, numbers, hyphens"
                                )
                            elif value in existing_ids:
                                validation["id_error"] = "Fixture ID already exists"
                            else:
                                validation["id_error"] = ""

                        id_input.on("change", validate_id)

                    # Name
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Name").classes("text-sm font-medium text-slate-700")
                        name_input = (
                            ui.input(
                                placeholder="e.g., TPS54302 Test Fixture A",
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

                    # Product selection
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Product").classes("text-sm font-medium text-slate-700")
                        ui.label("Associate this fixture with a product (optional)").classes(
                            "text-xs text-slate-400"
                        )
                        ui.select(
                            options=product_options,
                            value="",
                            on_change=lambda e: form.update({"product_id": e.value}),
                        ).props("outlined dense").classes("w-full")

                    # Product revision
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Product Revision").classes("text-sm font-medium text-slate-700")
                        ui.label("Optional").classes("text-xs text-slate-400")
                        ui.input(
                            placeholder="e.g., Rev A, v1.0",
                            on_change=lambda e: form.update({"product_revision": e.value.strip()}),
                        ).props("outlined dense").classes("w-full")

                    # Description
                    with ui.column().classes("gap-1 w-full"):
                        ui.label("Description").classes("text-sm font-medium text-slate-700")
                        ui.textarea(
                            placeholder="Brief description of the fixture...",
                            on_change=lambda e: form.update({"description": e.value.strip()}),
                        ).props("outlined dense").classes("w-full")

            with ui.card_actions().classes("justify-end"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/fixtures"),
                ).props("flat")

                def create():
                    # Validate
                    if not form["fixture_id"]:
                        validation["id_error"] = "Fixture ID is required"
                        return
                    if not form["name"]:
                        validation["name_error"] = "Name is required"
                        return
                    if validation["id_error"] or validation["name_error"]:
                        ui.notify("Please fix validation errors", type="warning")
                        return

                    # Create fixture
                    result = create_fixture(
                        fixture_id=form["fixture_id"],
                        name=form["name"],
                        product_id=form["product_id"],
                        product_revision=form["product_revision"],
                        description=form["description"],
                    )

                    if result:
                        ui.notify(
                            f"Fixture '{result.name}' created successfully",
                            type="positive",
                        )
                        # Redirect to edit page to add pin mappings
                        ui.navigate.to(f"/fixtures/{result.id}/edit")
                    else:
                        ui.notify(
                            "Fixture ID already exists",
                            type="negative",
                        )

                ui.button(
                    "Create Fixture",
                    icon="add",
                    on_click=create,
                ).props("color=primary")

        # Help text
        with ui.card().classes("w-full max-w-xl bg-blue-50"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("lightbulb").classes("text-blue-500 mt-0.5")
                    with ui.column().classes("gap-1"):
                        ui.label("What is a Fixture?").classes("font-medium text-blue-700")
                        ui.label(
                            "A fixture maps DUT (Device Under Test) pins to instrument "
                            "channels. This lets the test code reference logical pins "
                            "instead of specific hardware connections."
                        ).classes("text-sm text-blue-600")
                        ui.label("After creating the fixture, you can add pin mappings.").classes(
                            "text-sm text-blue-600"
                        )

        ui.link("← Back to Fixtures", "/fixtures").classes("text-blue-600 hover:underline")
