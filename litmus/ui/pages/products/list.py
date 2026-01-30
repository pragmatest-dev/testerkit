"""Product list page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_products


@ui.page("/products")
def products_page():
    """Products listing page."""
    create_layout("Products")

    products = discover_products()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("inventory_2").classes("text-slate-600")
            ui.label("Product Specifications").classes("text-lg font-semibold text-slate-700")

        if products:
            with ui.row().classes("gap-4 flex-wrap"):
                for product in products:
                    _product_card(product)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No product specifications found.").classes("text-slate-500")
                ui.label("Add YAML files to the specs/ directory.").classes(
                    "text-sm text-slate-400"
                )


def _product_card(product: dict):
    """Render a product card."""
    char_count = len(product.get("characteristics", {}) or {})
    req_count = len(product.get("test_requirements", {}) or {})

    with ui.card().classes("w-96"):
        with ui.card_section():
            with ui.row().classes("items-start justify-between"):
                ui.label(product["name"]).classes("text-lg font-semibold")
                if product.get("revision"):
                    ui.badge(f"Rev {product['revision']}").props("outline")

        with ui.card_section():
            ui.label(product.get("description", "")).classes("text-sm text-slate-600")
            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                with ui.row().classes("items-center gap-1"):
                    ui.icon("tag", size="xs")
                    ui.label(product["id"])

            with ui.row().classes("gap-4 mt-3"):
                with ui.row().classes("items-center gap-1"):
                    ui.icon("tune", size="xs").classes("text-slate-400")
                    ui.label(f"{char_count} characteristics").classes("text-sm text-slate-600")
                with ui.row().classes("items-center gap-1"):
                    ui.icon("checklist", size="xs").classes("text-slate-400")
                    ui.label(f"{req_count} test requirements").classes("text-sm text-slate-600")

        with ui.card_actions():
            ui.button(
                "View Details",
                icon="visibility",
                on_click=lambda p=product: ui.navigate.to(f"/products/{p['id']}"),
            ).props("flat")
