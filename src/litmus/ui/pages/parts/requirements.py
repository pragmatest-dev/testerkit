"""Part requirements page for procurement planning."""

from nicegui import ui

from litmus.ui.shared.components import data_table
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_parts,
    get_all_station_matches_for_part,
    get_required_capabilities,
    load_part_model,
)


@ui.page("/parts/{part_id}/requirements")
def requirements_page(part_id: str):
    """View required instruments for testing this part.

    This helps with procurement - know what to order early,
    mock tests while waiting for hardware.
    """
    parts = discover_parts()
    part = next((p for p in parts if p["id"] == part_id), None)

    if part:
        create_layout(f"{part['name']} - Requirements")
    else:
        create_layout("Part Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if part:
            _render_requirements(part_id, part)
        else:
            _render_not_found()


def _render_requirements(part_id: str, part: dict):
    """Render the requirements view."""
    part_model = load_part_model(part_id)

    # Header with back link
    with ui.row().classes("items-center gap-4 mb-4"):
        ui.link(f"← {part['name']}", f"/parts/{part_id}").classes("text-blue-600 hover:underline")

    # Required capabilities card
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Required Instrument Capabilities").classes("text-lg font-semibold")
            ui.label(
                "These capabilities are needed to test this part. "
                "Order missing instruments early - you can mock in the meantime."
            ).classes("text-sm text-slate-500")

        with ui.card_section():
            if part_model:
                capabilities = get_required_capabilities(part_model)
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
                        {"name": "signals", "label": "Signals", "field": "signals"},
                    ]
                    rows = [
                        {
                            "characteristic": cap["characteristic"],
                            "function": cap["function"],
                            "direction": cap["direction"],
                            "signals": cap.get("signals", ""),
                        }
                        for cap in capabilities
                    ]
                    data_table(columns=columns, rows=rows, row_key="characteristic")
                else:
                    ui.label(
                        "No characteristics defined - add characteristics to generate requirements."
                    ).classes("text-slate-500 italic")
            else:
                ui.label("Could not load part model.").classes("text-amber-600")

    # Station coverage card
    station_matches = get_all_station_matches_for_part(part_id)
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
                    ui.label("No stations have any required instruments.").classes("text-slate-500")
                ui.label("Consider creating a station or ordering instruments.").classes(
                    "text-sm text-slate-400 mt-1"
                )

    # Action buttons
    with ui.row().classes("mt-4 gap-3"):
        ui.button(
            "View Compatible Stations",
            icon="memory",
            on_click=lambda: ui.navigate.to(f"/parts/{part_id}/stations"),
        ).props("outline")
        ui.button(
            "Launch with Mocks",
            icon="play_arrow",
            on_click=lambda: ui.navigate.to(f"/launch?part={part_id}&mock=1"),
        ).props("color=primary")


def _render_not_found():
    """Render part not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Part not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline")
