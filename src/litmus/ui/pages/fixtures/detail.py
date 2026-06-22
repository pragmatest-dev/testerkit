"""Fixture detail page."""

from nicegui import ui

from litmus.ui.shared.components import data_table, info_field, setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_parts,
    get_compatible_stations_for_fixture,
    load_fixture_config,
)


@ui.page("/fixtures/{fixture_id}")
def fixture_detail_page(fixture_id: str):
    """Fixture detail page showing pin mappings and compatible stations."""
    config = load_fixture_config(fixture_id)
    parts = {p["id"]: p for p in discover_parts()}

    if config:
        create_layout(config.name or fixture_id)
    else:
        create_layout("Fixture Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if config:
            _render_fixture_detail(fixture_id, config, parts)
        else:
            _render_not_found()


def _render_fixture_detail(fixture_id: str, config, parts: dict):
    """Render the fixture detail view."""
    connections = config.connections or {}

    # Fixture info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.icon("hub").classes("text-2xl text-slate-600")
                    ui.label(config.name or fixture_id).classes("text-xl font-semibold")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/fixtures"),
                    ).props("flat")
                    ui.button(
                        "Edit",
                        icon="edit",
                        on_click=lambda: ui.navigate.to(f"/fixtures/{fixture_id}/edit"),
                    ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                info_field("Fixture ID", config.id or fixture_id)
                info_field("Part Family", config.part_family or "")
                info_field("Connections", str(len(connections)))

            if config.description:
                with ui.column().classes("gap-1 mt-4"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(config.description).classes("text-slate-700")

            # Part link
            part_family = config.part_family
            if part_family:
                part = parts.get(part_family)
                with ui.row().classes("items-center gap-2 mt-4"):
                    ui.icon("memory").classes("text-slate-500")
                    ui.label("Part:").classes("text-slate-500")
                    if part:
                        ui.link(
                            part.get("name", part_family),
                            f"/parts/{part_family}",
                        ).classes("text-blue-600 hover:underline font-semibold")
                    else:
                        ui.label(part_family).classes("font-mono")

    # Tabbed content
    with ui.tabs().classes("w-full") as tabs:
        mappings_tab = ui.tab("Pin Mappings", icon="cable")
        stations_tab = ui.tab("Compatible Stations", icon="dns")
        diagram_tab = ui.tab("Diagram", icon="account_tree")

    setup_hash_sync_for_tabs(tabs, ["Pin Mappings", "Compatible Stations", "Diagram"])

    with ui.tab_panels(tabs, value=mappings_tab).classes("w-full"):
        with ui.tab_panel(mappings_tab):
            _render_mappings_tab(connections)

        with ui.tab_panel(stations_tab):
            _render_stations_tab(fixture_id, connections)

        with ui.tab_panel(diagram_tab):
            _render_diagram_tab(config, connections)

    # Actions
    with ui.row().classes("mt-6 gap-2"):
        ui.link("← Back to Fixtures", "/fixtures").classes(
            "text-blue-600 hover:underline self-center"
        )


def _render_mappings_tab(connections: dict):
    """Render the pin mappings table."""
    if not connections:
        ui.label("No pin mappings defined.").classes("text-slate-500 italic")
        return

    columns = [
        {"name": "connection", "label": "Connection", "field": "connection", "align": "left"},
        {"name": "uut_pin", "label": "UUT Pin", "field": "uut_pin", "align": "left"},
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
            "connection": name,
            "uut_pin": fc.uut_pin or "",
            "net": fc.net or "",
            "instrument": fc.instrument or "",
            "channel": fc.instrument_channel or "",
            "description": fc.description or "",
        }
        for name, fc in connections.items()
    ]
    data_table(columns=columns, rows=rows, row_key="connection")


def _render_stations_tab(fixture_id: str, connections: dict):
    """Render compatible stations."""
    # Get required instruments
    required_instruments = sorted({c.instrument for c in connections.values() if c.instrument})

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
            ui.label("No compatible stations found").classes("text-amber-800 font-semibold mt-2")
            ui.label(
                "Create a station with the required instruments, "
                "or check existing station configurations."
            ).classes("text-amber-700 text-sm mt-1")


def _station_card(station, required_instruments: list[str]):
    """Render a compatible station card."""
    station_instruments = set(station.instruments.keys()) if station.instruments else set()

    with ui.card().classes("w-72"):
        with ui.card_section():
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns").classes("text-slate-600")
                ui.label(station.name or station.id).classes("font-semibold")

        with ui.card_section():
            ui.label(station.location or "").classes("text-sm text-slate-500")

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
                on_click=lambda _, s=station: ui.navigate.to(f"/stations/{s.id}"),
            ).props("flat dense")


def _render_diagram_tab(fixture, connections: dict):
    """Render a Mermaid diagram of the fixture connections."""
    # Group connections by instrument
    by_instrument: dict[str, list] = {}
    for name, fc in connections.items():
        inst = fc.instrument or "unknown"
        if inst not in by_instrument:
            by_instrument[inst] = []
        by_instrument[inst].append((name, fc))

    part_family = fixture.part_family or "UUT"

    # Build Mermaid diagram
    lines = [
        "%%{init: {'flowchart': {'curve': 'stepBefore'}}}%%",
        "flowchart LR",
        f"    subgraph Part[{part_family}]",
    ]

    # Add UUT pins
    pin_ids = []
    for name, fc in connections.items():
        uut_pin = fc.uut_pin or name
        pin_id = f"pin_{name.replace('-', '_')}"
        pin_ids.append((pin_id, name, fc))
        lines.append(f"        {pin_id}[{uut_pin}]")
    lines.append("    end")

    # Add fixture subgraph
    lines.append("    subgraph Fixture")
    fixture_ids = []
    for pin_id, name, fc in pin_ids:
        fix_id = f"fix_{name.replace('-', '_')}"
        inst = fc.instrument or "?"
        channel = fc.instrument_channel or ""
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
        ui.link("← Back to Fixtures", "/fixtures").classes("text-blue-600 hover:underline")
