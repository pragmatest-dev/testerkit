"""Part detail page."""

from nicegui import ui

from testerkit.ui.shared.components import (
    data_table,
    info_field,
    render_capability_detail,
    setup_hash_sync_for_tabs,
)
from testerkit.ui.shared.layout import create_layout
from testerkit.ui.shared.services import (
    discover_parts,
    get_compatible_stations_for_part,
    get_required_capabilities,
    load_part_model,
)


@ui.page("/parts/{part_id}")
def part_detail_page(part_id: str):
    """Part detail page with tabbed interface."""
    parts = discover_parts()
    part = next((p for p in parts if p["id"] == part_id), None)

    if part:
        create_layout(part["name"])
    else:
        create_layout("Part Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if part:
            _render_part_detail(part_id, part)
        else:
            _render_not_found()


def _render_part_detail(part_id: str, part: dict):
    """Render the part detail view."""
    # Part info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Part Information").classes("text-lg font-semibold")
                    if part.get("revision"):
                        ui.badge(f"Rev {part['revision']}").props("outline")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/parts"),
                    ).props("flat")
                    ui.button(
                        "Edit",
                        icon="edit",
                        on_click=lambda: ui.navigate.to(f"/parts/{part_id}/edit"),
                    ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=2).classes("gap-6"):
                info_field("Part ID", part["id"])
                info_field("Name", part["name"])
                with ui.column().classes("gap-1 col-span-2"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(part.get("description", "")).classes("font-semibold")

    # Tabbed content
    characteristics = part.get("characteristics", {}) or {}
    pins = part.get("pins", []) or []

    with ui.tabs().classes("w-full") as tabs:
        pins_tab = ui.tab("Pins", icon="memory")
        char_tab = ui.tab("Characteristics", icon="tune")
        stations_tab = ui.tab("Stations", icon="memory")

    setup_hash_sync_for_tabs(tabs, ["Pins", "Characteristics", "Stations"])

    with ui.tab_panels(tabs, value=pins_tab).classes("w-full"):
        with ui.tab_panel(pins_tab):
            _render_pins_tab(pins)

        with ui.tab_panel(char_tab):
            _render_characteristics_tab(characteristics)

        with ui.tab_panel(stations_tab):
            _render_stations_tab(part_id)

    ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline mt-4")


def _render_pins_tab(pins: list):
    """Render the pins tab."""
    if pins:
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
        data_table(columns=columns, rows=rows, row_key="name")
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
                                ui.label(char.get("unit", "—")).classes("font-mono")
                            with ui.column().classes("gap-0"):
                                ui.label("SpecBands").classes("text-xs text-slate-500 uppercase")
                                ui.label(str(len(char.get("specs", [])))).classes("font-mono")
                        render_capability_detail(char)
    else:
        ui.label("No characteristics defined.").classes("text-slate-500 italic")


def _render_stations_tab(part_id: str):
    """Render the stations tab with required capabilities and compatible stations."""
    part_model = load_part_model(part_id)
    if not part_model:
        ui.label("Part details unavailable.").classes("text-slate-500 italic")
        return

    required_caps = get_required_capabilities(part_model)
    if required_caps:
        with ui.card().classes("w-full mb-4"):
            with ui.card_section():
                ui.label("Required Instrument Capabilities").classes("font-semibold")
                ui.label("Instruments needed to test this part").classes("text-xs text-slate-500")
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
                data_table(columns=columns, rows=rows, row_key="char")

    compatible_stations = get_compatible_stations_for_part(part_id)
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


def _render_not_found():
    """Render part not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Part not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline")
