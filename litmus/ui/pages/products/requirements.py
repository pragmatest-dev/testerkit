"""Product requirements page for procurement planning."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    get_all_station_matches_for_product,
    get_required_capabilities,
    load_product_model,
)


@ui.page("/products/{product_id}/requirements")
def requirements_page(product_id: str):
    """View required instruments for testing this product.

    This helps with procurement - know what to order early,
    simulate tests while waiting for hardware.
    """
    products = discover_products()
    product = next((p for p in products if p["id"] == product_id), None)

    if product:
        create_layout(f"{product['name']} - Requirements")
    else:
        create_layout("Product Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if product:
            _render_requirements(product_id, product)
        else:
            _render_not_found()


def _render_requirements(product_id: str, product: dict):
    """Render the requirements view."""
    product_model = load_product_model(product_id)

    # Header with back link
    with ui.row().classes("items-center gap-4 mb-4"):
        ui.link(f"← {product['name']}", f"/products/{product_id}").classes(
            "text-blue-600 hover:underline"
        )

    # Required capabilities card
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Required Instrument Capabilities").classes("text-lg font-semibold")
            ui.label(
                "These capabilities are needed to test this product. "
                "Order missing instruments early - you can simulate in the meantime."
            ).classes("text-sm text-slate-500")

        with ui.card_section():
            if product_model:
                capabilities = get_required_capabilities(product_model)
                if capabilities:
                    columns = [
                        {
                            "name": "characteristic",
                            "label": "For Characteristic",
                            "field": "characteristic",
                            "align": "left",
                        },
                        {"name": "function", "label": "Function", "field": "function"},
                        {
                            "name": "direction",
                            "label": "Instrument Direction",
                            "field": "direction",
                        },
                        {"name": "parameters", "label": "Parameters", "field": "parameters"},
                    ]
                    rows = [
                        {
                            "characteristic": cap["characteristic"],
                            "function": cap["function"],
                            "direction": cap["direction"],
                            "parameters": cap.get("parameters", ""),
                        }
                        for cap in capabilities
                    ]
                    ui.table(columns=columns, rows=rows, row_key="characteristic").classes(
                        "w-full"
                    )
                else:
                    ui.label(
                        "No characteristics defined - add characteristics to generate requirements."
                    ).classes("text-slate-500 italic")
            else:
                ui.label("Could not load product model.").classes("text-amber-600")

    # Station coverage card
    station_matches = get_all_station_matches_for_product(product_id)
    compatible = station_matches.get("compatible", [])
    partial = station_matches.get("partial", [])

    with ui.card().classes("w-full mt-4"):
        with ui.card_section():
            ui.label("Station Coverage").classes("text-lg font-semibold")

        with ui.card_section():
            if compatible:
                with ui.row().classes("items-center gap-2 mb-3"):
                    ui.icon("check_circle").classes("text-emerald-500")
                    ui.label(f"{len(compatible)} station(s) fully compatible").classes(
                        "text-emerald-600 font-medium"
                    )

            if partial:
                ui.label("Partially Compatible Stations").classes(
                    "font-medium text-amber-600 mt-2 mb-2"
                )
                ui.label(
                    "These stations have some capabilities. "
                    "Add missing instruments to enable full testing."
                ).classes("text-xs text-slate-500 mb-2")

                for station in partial:
                    with ui.row().classes("items-center gap-3 py-2 border-b border-slate-100"):
                        ui.icon("warning").classes("text-amber-500")
                        with ui.column().classes("flex-1"):
                            ui.label(station["name"]).classes("font-medium")
                            with ui.row().classes("gap-1 flex-wrap"):
                                ui.label("Missing:").classes("text-xs text-slate-500")
                                for cap in station.get("missing", []):
                                    ui.badge(cap, color="amber").props("outline dense")
                        ui.badge(f"{station['coverage']}%", color="amber")

            if not compatible and not partial:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("info").classes("text-slate-400")
                    ui.label("No stations have any required instruments.").classes(
                        "text-slate-500"
                    )
                ui.label(
                    "Consider creating a station or ordering instruments."
                ).classes("text-sm text-slate-400 mt-1")

    # Action buttons
    with ui.row().classes("mt-4 gap-3"):
        ui.button(
            "View Compatible Stations",
            icon="memory",
            on_click=lambda: ui.navigate.to(f"/products/{product_id}/stations"),
        ).props("outline")
        ui.button(
            "Launch with Simulation",
            icon="play_arrow",
            on_click=lambda: ui.navigate.to(f"/launch?product={product_id}&simulate=1"),
        ).props("color=primary")


def _render_not_found():
    """Render product not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Product not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Products", "/products").classes("text-blue-600 hover:underline")
