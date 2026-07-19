"""Part station compatibility page."""

from nicegui import ui

from testerkit.ui.shared.layout import create_layout
from testerkit.ui.shared.services import (
    discover_parts,
    get_all_station_matches_for_part,
    load_part_model,
)


@ui.page("/parts/{part_id}/stations")
def part_stations_page(part_id: str):
    """View station compatibility for this part.

    Shows which stations can fully or partially test this part.
    Helps with procurement planning and test execution decisions.
    """
    parts = discover_parts()
    part = next((p for p in parts if p["id"] == part_id), None)

    if part:
        create_layout(f"{part['name']} - Station Compatibility")
    else:
        create_layout("Part Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if part:
            _render_station_compatibility(part_id, part)
        else:
            _render_not_found()


def _render_station_compatibility(part_id: str, part: dict):
    """Render the station compatibility view."""
    part_model = load_part_model(part_id)
    station_matches = get_all_station_matches_for_part(part_id)

    compatible = station_matches.get("compatible", [])
    partial = station_matches.get("partial", [])
    incompatible = station_matches.get("incompatible", [])

    # Header with back link
    with ui.row().classes("items-center gap-4 mb-4"):
        ui.link(f"← {part['name']}", f"/parts/{part_id}").classes("text-blue-600 hover:underline")

    # Summary card
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label(f"Station Compatibility for {part['name']}").classes("text-lg font-semibold")
            if part_model:
                char_count = len(part_model.characteristics)
                ui.label(f"Matching stations against {char_count} characteristic(s)").classes(
                    "text-sm text-slate-500"
                )

        with ui.card_section():
            with ui.row().classes("gap-6"):
                with ui.column().classes("items-center"):
                    ui.label(str(len(compatible))).classes("text-2xl font-bold text-emerald-600")
                    ui.label("Ready").classes("text-xs text-slate-500")
                with ui.column().classes("items-center"):
                    ui.label(str(len(partial))).classes("text-2xl font-bold text-amber-600")
                    ui.label("Partial").classes("text-xs text-slate-500")
                with ui.column().classes("items-center"):
                    ui.label(str(len(incompatible))).classes("text-2xl font-bold text-slate-400")
                    ui.label("None").classes("text-xs text-slate-500")

    # Fully compatible stations
    if compatible:
        with ui.card().classes("w-full mt-4"):
            with ui.card_section():
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle").classes("text-emerald-500")
                    ui.label("Ready to Test").classes("text-lg font-semibold text-emerald-600")
                ui.label("These stations have all required instruments.").classes(
                    "text-sm text-slate-500"
                )

            with ui.card_section():
                for station in compatible:
                    _render_compatible_station(part_id, station)

    # Partially compatible stations
    if partial:
        with ui.card().classes("w-full mt-4"):
            with ui.card_section():
                with ui.row().classes("items-center gap-2"):
                    ui.icon("warning").classes("text-amber-500")
                    ui.label("Needs Additional Instruments").classes(
                        "text-lg font-semibold text-amber-600"
                    )
                ui.label(
                    "Add missing instruments to enable full testing, or use simulation."
                ).classes("text-sm text-slate-500")

            with ui.card_section():
                for station in partial:
                    _render_partial_station(part_id, station)

    # Incompatible stations (if any exist)
    if incompatible:
        with ui.expansion("Incompatible Stations", icon="block").classes("w-full mt-4"):
            ui.label("These stations have none of the required instruments.").classes(
                "text-sm text-slate-500 mb-2"
            )
            for station in incompatible:
                with ui.row().classes("items-center gap-3 py-2 border-b border-slate-100"):
                    ui.icon("block").classes("text-slate-300")
                    ui.label(station["name"]).classes("text-slate-500")
                    if station.get("location"):
                        ui.label(f"({station['location']})").classes("text-xs text-slate-400")

    # No stations at all
    if not compatible and not partial and not incompatible:
        with ui.card().classes("w-full mt-4 p-6 text-center"):
            ui.icon("search_off").classes("text-4xl text-slate-300")
            ui.label("No stations found").classes("text-lg text-slate-500 mt-2")
            ui.label("Create a station configuration to enable testing.").classes(
                "text-sm text-slate-400"
            )
            ui.button(
                "Create Station",
                icon="add",
                on_click=lambda: ui.navigate.to("/stations/new"),
            ).classes("mt-4")

    # Action buttons
    with ui.row().classes("mt-4 gap-3"):
        ui.button(
            "View Requirements",
            icon="list_alt",
            on_click=lambda: ui.navigate.to(f"/parts/{part_id}/requirements"),
        ).props("outline")
        if compatible:
            ui.button(
                "Launch Tests",
                icon="play_arrow",
                on_click=lambda: ui.navigate.to(f"/launch?part={part_id}"),
            ).props("color=primary")
        else:
            ui.button(
                "Launch with Mocks",
                icon="play_arrow",
                on_click=lambda: ui.navigate.to(f"/launch?part={part_id}&mock=1"),
            ).props("color=primary")


def _render_compatible_station(part_id: str, station: dict):
    """Render a fully compatible station row."""
    with ui.row().classes("items-center justify-between py-3 border-b border-slate-100 w-full"):
        with ui.column().classes("flex-1"):
            ui.label(station["name"]).classes("font-semibold")
            if station.get("location"):
                ui.label(station["location"]).classes("text-sm text-slate-500")
        with ui.row().classes("items-center gap-3"):
            ui.badge("100%", color="green").props("dense")
            ui.button(
                "Launch",
                icon="play_arrow",
                on_click=lambda _, s=station: ui.navigate.to(
                    f"/launch?part={part_id}&station={s['id']}"
                ),
            ).props("flat dense")


def _render_partial_station(part_id: str, station: dict):
    """Render a partially compatible station row."""
    with ui.row().classes("items-center justify-between py-3 border-b border-slate-100 w-full"):
        with ui.column().classes("flex-1"):
            ui.label(station["name"]).classes("font-semibold")
            if station.get("location"):
                ui.label(station["location"]).classes("text-sm text-slate-500")
            # Show missing capabilities
            with ui.row().classes("gap-1 flex-wrap mt-1"):
                ui.label("Missing:").classes("text-xs text-slate-500")
                for cap in station.get("missing", [])[:5]:  # Limit to 5
                    ui.badge(cap, color="amber").props("outline dense")
                if len(station.get("missing", [])) > 5:
                    ui.badge(f"+{len(station['missing']) - 5} more", color="grey").props(
                        "outline dense"
                    )
        with ui.row().classes("items-center gap-3"):
            ui.badge(f"{station['coverage']}%", color="amber").props("dense")
            ui.button(
                "Mock",
                icon="play_arrow",
                on_click=lambda _, s=station: ui.navigate.to(
                    f"/launch?part={part_id}&station={s['id']}&mock=1"
                ),
            ).props("flat dense outline")


def _render_not_found():
    """Render part not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Part not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Parts", "/parts").classes("text-blue-600 hover:underline")
