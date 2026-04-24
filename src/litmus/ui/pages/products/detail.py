"""Product detail page."""

from nicegui import ui

from litmus.models.config import TestSequenceConfig
from litmus.ui.shared.components import (
    info_field,
    render_capability_detail,
    setup_hash_sync_for_tabs,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    discover_sequences,
    get_compatible_stations_for_product,
    get_required_capabilities,
    load_product_model,
)


@ui.page("/products/{product_id}")
def product_detail_page(product_id: str):
    """Product detail page with tabbed interface."""
    products = discover_products()
    product = next((p for p in products if p["id"] == product_id), None)

    if product:
        create_layout(product["name"])
    else:
        create_layout("Product Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if product:
            _render_product_detail(product_id, product)
        else:
            _render_not_found()


def _render_product_detail(product_id: str, product: dict):
    """Render the product detail view."""
    # Product info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Product Information").classes("text-lg font-semibold")
                    if product.get("revision"):
                        ui.badge(f"Rev {product['revision']}").props("outline")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/products"),
                    ).props("flat")
                    ui.button(
                        "Edit",
                        icon="edit",
                        on_click=lambda: ui.navigate.to(f"/products/{product_id}/edit"),
                    ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=2).classes("gap-6"):
                info_field("Product ID", product["id"])
                info_field("Name", product["name"])
                with ui.column().classes("gap-1 col-span-2"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(product.get("description", "")).classes("font-semibold")

    # Tabbed content
    characteristics = product.get("characteristics", {}) or {}
    pins = product.get("pins", []) or []

    with ui.tabs().classes("w-full") as tabs:
        pins_tab = ui.tab("Pins", icon="memory")
        char_tab = ui.tab("Characteristics", icon="tune")
        seq_tab = ui.tab("Sequences", icon="list_alt")

    setup_hash_sync_for_tabs(tabs, ["Pins", "Characteristics", "Sequences"])

    with ui.tab_panels(tabs, value=pins_tab).classes("w-full"):
        with ui.tab_panel(pins_tab):
            _render_pins_tab(pins)

        with ui.tab_panel(char_tab):
            _render_characteristics_tab(characteristics)

        with ui.tab_panel(seq_tab):
            _render_sequences_tab(product_id)

    ui.link("← Back to Products", "/products").classes("text-blue-600 hover:underline mt-4")


def _render_pins_tab(pins: list):
    """Render the pins tab."""
    if pins:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "name", "label": "Name", "field": "name", "align": "left"},
                {"name": "net", "label": "Net", "field": "net"},
                {"name": "description", "label": "Description", "field": "description"},
            ]
            rows = [
                {
                    "name": pin.get("name", ""),
                    "net": pin.get("net", ""),
                    "description": pin.get("description", ""),
                }
                for pin in pins
            ]
            ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
    else:
        ui.label("No pins defined.").classes("text-slate-500 italic")


def _render_characteristics_tab(characteristics: dict):
    """Render the characteristics tab."""
    if characteristics:
        with ui.card().classes("w-full"):
            for name, char in characteristics.items():
                with ui.expansion(
                    f"{name}  —  {char.get('function', '')} ({char.get('direction', '')})",
                    icon="tune",
                ).classes("w-full"):
                    with ui.column().classes("gap-1 p-2"):
                        with ui.row().classes("gap-4"):
                            with ui.column().classes("gap-0"):
                                ui.label("Units").classes("text-xs text-slate-500 uppercase")
                                ui.label(char.get("units", "—")).classes("font-mono")
                            with ui.column().classes("gap-0"):
                                ui.label("SpecBands").classes("text-xs text-slate-500 uppercase")
                                ui.label(str(len(char.get("specs", [])))).classes("font-mono")
                        render_capability_detail(char)
    else:
        ui.label("No characteristics defined.").classes("text-slate-500 italic")


def _render_sequences_tab(product_id: str):
    """Render the sequences tab with capabilities and compatible stations."""
    # Required capabilities
    product_model = load_product_model(product_id)
    if product_model:
        required_caps = get_required_capabilities(product_model)
        if required_caps:
            with ui.card().classes("w-full mb-4"):
                with ui.card_section():
                    ui.label("Required Instrument Capabilities").classes("font-semibold")
                    ui.label("Instruments needed to test this product").classes(
                        "text-xs text-slate-500"
                    )
                with ui.card_section():
                    columns = [
                        {
                            "name": "char",
                            "label": "Characteristic",
                            "field": "char",
                            "align": "left",
                        },
                        {"name": "function", "label": "Function", "field": "function"},
                        {"name": "direction", "label": "Inst. Direction", "field": "direction"},
                        {"name": "signals", "label": "Signals", "field": "signals"},
                    ]
                    rows = [
                        {
                            "char": cap["characteristic"],
                            "function": cap["function"],
                            "direction": cap["direction"],
                            "signals": cap.get("signals", ""),
                        }
                        for cap in required_caps
                    ]
                    ui.table(columns=columns, rows=rows, row_key="char").classes("w-full")

        # Compatible stations
        compatible_stations = get_compatible_stations_for_product(product_id)
        with ui.row().classes("items-center gap-2 mt-4 mb-2"):
            ui.icon("memory").classes("text-slate-600")
            ui.label("Compatible Stations").classes("font-semibold text-slate-700")
            ui.badge(f"{len(compatible_stations)} found").props("outline")

        if compatible_stations:
            with ui.row().classes("gap-4 flex-wrap mb-4"):
                for station in compatible_stations:
                    with ui.card().classes("w-64"):
                        with ui.card_section():
                            ui.label(station["name"]).classes("font-semibold")
                            ui.label(station.get("location", "")).classes("text-xs text-slate-500")
                        with ui.card_actions():

                            def go_to_station(_, s=station):
                                ui.navigate.to(f"/stations/{s['id']}")

                            ui.button(
                                "View",
                                icon="visibility",
                                on_click=go_to_station,
                            ).props("flat dense")
        else:
            ui.label("No compatible stations found.").classes("text-slate-500 italic mb-4")

    # Sequences for this product
    with ui.row().classes("items-center gap-2 mt-4 mb-2"):
        ui.icon("list_alt").classes("text-slate-600")
        ui.label("Test Sequences").classes("font-semibold text-slate-700")

    sequences = discover_sequences()
    product_sequences = [s for s in sequences if s.product_family == product_id]

    if product_sequences:
        with ui.row().classes("gap-4 flex-wrap"):
            for seq in product_sequences:
                _sequence_card(seq)
    else:
        ui.label("No sequences defined for this product.").classes("text-slate-500 italic")


def _sequence_card(seq: TestSequenceConfig):
    """Render a sequence card."""
    with ui.card().classes("w-72"):
        with ui.card_section():
            ui.label(seq.name or seq.id).classes("font-semibold")
            if seq.test_phase:
                phase_colors = {
                    "validation": "blue",
                    "characterization": "purple",
                    "production": "green",
                }
                color = phase_colors.get(seq.test_phase, "gray")
                ui.badge(seq.test_phase, color=color).props("outline")
            ui.label((seq.description or "")[:60]).classes("text-sm text-slate-500 mt-1")
        with ui.card_actions():
            ui.button(
                "View",
                icon="visibility",
                on_click=lambda _, s=seq: ui.navigate.to(f"/sequences/{s.id}"),
            ).props("flat dense")
            ui.button(
                "Run",
                icon="play_arrow",
                on_click=lambda _, s=seq: ui.navigate.to(f"/launch?sequence={s.id}"),
            ).props("flat dense color=primary")


def _render_not_found():
    """Render product not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Product not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Products", "/products").classes("text-blue-600 hover:underline")
