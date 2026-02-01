"""Fixture detail page."""

from nicegui import ui

from litmus.ui.shared.components import setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    get_compatible_stations_for_fixture,
    load_fixture_config,
    load_station_config,
)


@ui.page("/fixtures/{fixture_id}")
def fixture_detail_page(fixture_id: str):
    """Fixture detail page showing pin mappings and compatible stations."""
    config = load_fixture_config(fixture_id)
    products = {p["id"]: p for p in discover_products()}

    if config:
        fixture = config.get("fixture", {})
        create_layout(fixture.get("name", fixture_id))
    else:
        create_layout("Fixture Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if config:
            _render_fixture_detail(fixture_id, config, products)
        else:
            _render_not_found()


def _render_fixture_detail(fixture_id: str, config: dict, products: dict):
    """Render the fixture detail view."""
    fixture = config.get("fixture", {})
    points = config.get("points", {})

    # Fixture info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.icon("hub").classes("text-2xl text-slate-600")
                    ui.label(fixture.get("name", fixture_id)).classes(
                        "text-xl font-semibold"
                    )
                ui.button(
                    "Edit",
                    icon="edit",
                    on_click=lambda: ui.navigate.to(f"/fixtures/{fixture_id}/edit"),
                ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                _info_field("Fixture ID", fixture.get("id", fixture_id))
                _info_field("Product Family", fixture.get("product_family", ""))
                _info_field("Points", str(len(points)))

            if fixture.get("description"):
                with ui.column().classes("gap-1 mt-4"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(fixture["description"]).classes("text-slate-700")

            # Product link
            product_family = fixture.get("product_family")
            if product_family:
                product = products.get(product_family)
                with ui.row().classes("items-center gap-2 mt-4"):
                    ui.icon("memory").classes("text-slate-500")
                    ui.label("Product:").classes("text-slate-500")
                    if product:
                        ui.link(
                            product.get("name", product_family),
                            f"/products/{product_family}",
                        ).classes("text-blue-600 hover:underline font-semibold")
                    else:
                        ui.label(product_family).classes("font-mono")

    # Tabbed content
    with ui.tabs().classes("w-full") as tabs:
        mappings_tab = ui.tab("Pin Mappings", icon="cable")
        stations_tab = ui.tab("Compatible Stations", icon="dns")
        diagram_tab = ui.tab("Diagram", icon="account_tree")

    setup_hash_sync_for_tabs(tabs, ["Pin Mappings", "Compatible Stations", "Diagram"])

    with ui.tab_panels(tabs, value=mappings_tab).classes("w-full"):
        with ui.tab_panel(mappings_tab):
            _render_mappings_tab(points)

        with ui.tab_panel(stations_tab):
            _render_stations_tab(fixture_id, points)

        with ui.tab_panel(diagram_tab):
            _render_diagram_tab(fixture, points)

    # Actions
    with ui.row().classes("mt-6 gap-2"):
        ui.link("← Back to Fixtures", "/fixtures").classes(
            "text-blue-600 hover:underline self-center"
        )


def _info_field(label: str, value: str):
    """Render an info field."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("font-semibold")


def _render_mappings_tab(points: dict):
    """Render the pin mappings table."""
    if not points:
        ui.label("No pin mappings defined.").classes("text-slate-500 italic")
        return

    with ui.card().classes("w-full"):
        columns = [
            {"name": "point", "label": "Point Name", "field": "point", "align": "left"},
            {"name": "dut_pin", "label": "DUT Pin", "field": "dut_pin", "align": "left"},
            {"name": "net", "label": "Net", "field": "net", "align": "left"},
            {
                "name": "instrument",
                "label": "Instrument",
                "field": "instrument",
                "align": "left",
            },
            {
                "name": "channel",
                "label": "Channel",
                "field": "channel",
                "align": "left",
            },
            {
                "name": "description",
                "label": "Description",
                "field": "description",
                "align": "left",
            },
        ]
        rows = [
            {
                "point": name,
                "dut_pin": data.get("dut_pin", ""),
                "net": data.get("net", ""),
                "instrument": data.get("instrument", ""),
                "channel": data.get("instrument_channel", ""),
                "description": data.get("description", ""),
            }
            for name, data in points.items()
        ]
        ui.table(columns=columns, rows=rows, row_key="point").classes("w-full")


def _render_stations_tab(fixture_id: str, points: dict):
    """Render compatible stations."""
    # Get required instruments
    required_instruments = sorted(
        {p.get("instrument") for p in points.values() if p.get("instrument")}
    )

    if required_instruments:
        with ui.row().classes("items-center gap-2 mb-4"):
            ui.label("Required instruments:").classes("text-sm text-slate-500")
            for inst in required_instruments:
                ui.badge(inst).props("outline")

    compatible = get_compatible_stations_for_fixture(fixture_id)

    if compatible:
        with ui.row().classes("items-center gap-2 mb-4"):
            ui.icon("check_circle", color="green")
            ui.label(f"{len(compatible)} compatible station(s)").classes(
                "text-green-700 font-semibold"
            )

        with ui.row().classes("gap-4 flex-wrap"):
            for station in compatible:
                _station_card(station, required_instruments)
    else:
        with ui.card().classes("w-full p-6 text-center bg-amber-50"):
            ui.icon("warning", color="amber").classes("text-2xl")
            ui.label("No compatible stations found").classes(
                "text-amber-800 font-semibold mt-2"
            )
            ui.label(
                "Create a station with the required instruments, "
                "or check existing station configurations."
            ).classes("text-amber-700 text-sm mt-1")


def _station_card(station: dict, required_instruments: list[str]):
    """Render a compatible station card."""
    station_config = load_station_config(station["id"])
    station_instruments = (
        set(station_config.get("instruments", {}).keys()) if station_config else set()
    )

    with ui.card().classes("w-72"):
        with ui.card_section():
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns").classes("text-slate-600")
                ui.label(station.get("name", station["id"])).classes("font-semibold")

        with ui.card_section():
            ui.label(station.get("location", "")).classes("text-sm text-slate-500")

            # Show which instruments are present
            ui.label("Instruments").classes("text-xs text-slate-500 uppercase mt-3")
            with ui.column().classes("gap-1 mt-1"):
                for inst in required_instruments:
                    present = inst in station_instruments
                    with ui.row().classes("items-center gap-2"):
                        ui.icon(
                            "check_circle" if present else "cancel",
                            size="xs",
                            color="green" if present else "red",
                        )
                        ui.label(inst).classes("text-sm font-mono")

        with ui.card_actions():
            ui.button(
                "View Station",
                icon="visibility",
                on_click=lambda s=station: ui.navigate.to(f"/stations/{s['id']}"),
            ).props("flat dense")


def _render_diagram_tab(fixture: dict, points: dict):
    """Render a Mermaid diagram of the fixture connections."""
    # Group points by instrument
    by_instrument: dict[str, list] = {}
    for name, data in points.items():
        inst = data.get("instrument", "unknown")
        if inst not in by_instrument:
            by_instrument[inst] = []
        by_instrument[inst].append((name, data))

    product_family = fixture.get("product_family", "DUT")

    # Build Mermaid diagram
    lines = [
        "%%{init: {'flowchart': {'curve': 'stepBefore'}}}%%",
        "flowchart LR",
        f"    subgraph Product[{product_family}]",
    ]

    # Add DUT pins
    pin_ids = []
    for name, data in points.items():
        dut_pin = data.get("dut_pin", name)
        pin_id = f"pin_{name.replace('-', '_')}"
        pin_ids.append((pin_id, name, data))
        lines.append(f"        {pin_id}[{dut_pin}]")
    lines.append("    end")

    # Add fixture subgraph
    lines.append("    subgraph Fixture")
    fixture_ids = []
    for pin_id, name, data in pin_ids:
        fix_id = f"fix_{name.replace('-', '_')}"
        inst = data.get("instrument", "?")
        channel = data.get("instrument_channel", "")
        channel_str = f".{channel}" if channel else ""
        fixture_ids.append((fix_id, pin_id, inst))
        lines.append(f"        {fix_id}[{name} → {inst}{channel_str}]")
    lines.append("    end")

    # Add instruments subgraph
    instruments = sorted(by_instrument.keys())
    lines.append("    subgraph Station[Instruments]")
    for inst in instruments:
        inst_id = f"inst_{inst.replace('-', '_')}"
        lines.append(f"        {inst_id}[{inst}]")
    lines.append("    end")

    # Add connections
    for fix_id, pin_id, inst in fixture_ids:
        lines.append(f"    {pin_id} --- {fix_id}")
        inst_id = f"inst_{inst.replace('-', '_')}"
        lines.append(f"    {fix_id} --- {inst_id}")

    mermaid_code = "\n".join(lines)

    with ui.card().classes("w-full p-4"):
        ui.mermaid(mermaid_code).classes("w-full")


def _render_not_found():
    """Render fixture not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Fixture not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Fixtures", "/fixtures").classes(
            "text-blue-600 hover:underline"
        )
