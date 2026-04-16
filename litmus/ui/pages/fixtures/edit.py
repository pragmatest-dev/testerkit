"""Fixture edit page."""

from collections.abc import Callable

from nicegui import ui

from litmus.ui.shared.components import AutoSaver, labeled_input, labeled_textarea
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    discover_stations,
    load_fixture_config,
    save_fixture,
)


@ui.page("/fixtures/{fixture_id}/edit")
def fixture_edit_page(fixture_id: str):
    """Fixture edit/create page."""
    is_new = fixture_id == "new"
    config = None if is_new else load_fixture_config(fixture_id)

    if is_new:
        create_layout("New Fixture")
        fixture_data = {
            "id": "",
            "name": "",
            "description": "",
            "product_id": "",
            "product_revision": "",
        }
        points_data = {}
    elif config:
        create_layout(f"Edit {config.name or fixture_id}")
        fixture_data = {
            "id": config.id or fixture_id,
            "name": config.name or "",
            "description": config.description or "",
            "product_id": config.product_id or config.product_family or "",
            "product_revision": config.product_revision or "",
        }
        points_data = {k: v.model_dump() for k, v in config.points.items()} if config.points else {}
    else:
        create_layout("Fixture Not Found")
        with ui.column().classes("w-full p-6"):
            ui.label("Fixture not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Fixtures", "/fixtures").classes("text-blue-600 hover:underline")
        return

    # Get available products and stations for dropdowns
    products = discover_products()
    product_options = {p["id"]: p.get("name", p["id"]) for p in products}

    stations = discover_stations()
    # Collect all instrument names across all stations
    all_instruments = set()
    for station in stations:
        if station.instruments:
            all_instruments.update(station.instruments.keys())
    default_instruments = ["dmm", "psu", "eload", "scope"]
    instrument_options = sorted(all_instruments) if all_instruments else default_instruments

    # Reactive state
    form_data = {"fixture": dict(fixture_data), "points": dict(points_data)}

    # Auto-save for existing fixtures
    def do_save():
        fid = form_data["fixture"]["id"]
        if fid:
            save_fixture(fid, form_data["fixture"], form_data["points"])

    saver = AutoSaver(do_save, delay=1.0) if not is_new else None

    with ui.column().classes("w-full p-6 gap-6"):
        # Fixture info card
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Fixture Information").classes("text-lg font-semibold")

            with ui.card_section().classes("flex flex-col gap-4"):
                with ui.row().classes("gap-4"):
                    labeled_input(
                        "Fixture ID",
                        form_data["fixture"]["id"],
                        readonly=not is_new,
                        on_change=lambda e: (
                            form_data["fixture"].update({"id": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    )
                    labeled_input(
                        "Name",
                        form_data["fixture"]["name"],
                        on_change=lambda e: (
                            form_data["fixture"].update({"name": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    )

                with ui.row().classes("gap-4 w-full"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Product").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options=product_options,
                            value=form_data["fixture"]["product_id"],
                            on_change=lambda e: (
                                form_data["fixture"].update({"product_id": e.value}),
                                saver.trigger() if saver else None,
                            ),
                        ).props("outlined dense").classes("w-full")
                    labeled_input(
                        "Revision (optional)",
                        form_data["fixture"]["product_revision"],
                        on_change=lambda e: (
                            form_data["fixture"].update({"product_revision": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    )

                labeled_textarea(
                    "Description",
                    form_data["fixture"]["description"],
                    on_change=lambda e: (
                        form_data["fixture"].update({"description": e.value}),
                        saver.trigger() if saver else None,
                    ),
                )

        # Points card
        with ui.card().classes("w-full"):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Pin Mappings").classes("text-lg font-semibold")
                    ui.button(
                        "Add Point",
                        icon="add",
                        on_click=lambda: _show_add_point_dialog(
                            instrument_options,
                            lambda name, data: _add_point(
                                form_data,
                                name,
                                data,
                                instrument_options,
                                points_container,
                                saver,
                            ),
                        ),
                    ).props("flat color=primary")

            with ui.card_section() as points_container:
                _render_points_list(
                    form_data["points"], instrument_options, points_container, saver
                )

        # Actions
        with ui.row().classes("gap-2"):
            if is_new:

                def handle_create():
                    fid = form_data["fixture"]["id"]
                    if not fid:
                        ui.notify("Fixture ID is required", type="warning")
                        return
                    if save_fixture(fid, form_data["fixture"], form_data["points"]):
                        ui.notify("Fixture created", type="positive")
                        ui.navigate.to(f"/fixtures/{fid}")
                    else:
                        ui.notify("Failed to create fixture", type="negative")

                ui.button("Create", icon="add", on_click=handle_create).props("color=primary")
            else:
                # Auto-save indicator
                ui.label("Changes auto-saved").classes("text-sm text-slate-400 italic")

            ui.button(
                "Back",
                icon="arrow_back",
                on_click=lambda: ui.navigate.to(
                    f"/fixtures/{fixture_id}" if not is_new else "/fixtures"
                ),
            ).props("flat")


def _render_points_list(points: dict, instrument_options: list, container, saver=None):
    """Render the list of fixture points."""
    container.clear()
    with container:
        if not points:
            ui.label("No pin mappings defined. Click 'Add Point' to create one.").classes(
                "text-slate-500 italic"
            )
            return

        for point_name, point_data in points.items():
            _render_point_row(point_name, point_data, points, instrument_options, container, saver)


def _render_point_row(
    point_name: str,
    point_data: dict,
    all_points: dict,
    instrument_options: list,
    container,
    saver=None,
):
    """Render a single point row with inline editing."""
    with ui.card().classes("w-full mb-2"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                ui.label(point_name).classes("font-semibold font-mono")

                def delete_handler(pn=point_name):
                    _delete_point(all_points, pn, container, instrument_options, saver)

                ui.button(icon="delete", on_click=delete_handler).props("flat dense color=red")

        with ui.card_section():
            with ui.grid(columns=4).classes("gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("DUT Pin").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("dut_pin", ""),
                        on_change=lambda e, pd=point_data: (
                            pd.update({"dut_pin": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Net").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("net", ""),
                        on_change=lambda e, pd=point_data: (
                            pd.update({"net": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Instrument").classes("text-xs text-slate-500")
                    ui.select(
                        options=instrument_options,
                        value=point_data.get("instrument", ""),
                        on_change=lambda e, pd=point_data: (
                            pd.update({"instrument": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Channel").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("instrument_channel", ""),
                        on_change=lambda e, pd=point_data: (
                            pd.update({"instrument_channel": e.value}),
                            saver.trigger() if saver else None,
                        ),
                    ).props("outlined dense").classes("w-full")


def _add_point(
    form_data: dict,
    name: str,
    data: dict,
    instrument_options: list,
    container,
    saver=None,
):
    """Add a new point to the fixture."""
    form_data["points"][name] = data
    _render_points_list(form_data["points"], instrument_options, container, saver)
    container.update()
    if saver:
        saver.trigger()


def _delete_point(points: dict, name: str, container, instrument_options: list, saver=None):
    """Delete a point from the fixture."""
    if name in points:
        del points[name]
    _render_points_list(points, instrument_options, container, saver)
    container.update()
    if saver:
        saver.trigger()


def _show_add_point_dialog(instrument_options: list, on_add: Callable):
    """Show dialog to add a new fixture point."""
    point_form = {
        "name": "",
        "dut_pin": "",
        "net": "",
        "instrument": instrument_options[0] if instrument_options else "",
        "instrument_channel": "",
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Pin Mapping").classes("text-lg font-semibold")

        with ui.card_section().classes("flex flex-col gap-4"):
            with ui.column().classes("gap-1"):
                ui.label("Point Name").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., vout_measure",
                    on_change=lambda e: point_form.update({"name": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("DUT Pin").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., VOUT, J1.3",
                    on_change=lambda e: point_form.update({"dut_pin": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("Net (optional)").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., VOUT_3V3",
                    on_change=lambda e: point_form.update({"net": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("Instrument").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options=instrument_options,
                    value=point_form["instrument"],
                    on_change=lambda e: point_form.update({"instrument": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("Instrument Channel").classes("text-sm font-medium text-slate-700")
                ui.input(
                    placeholder="e.g., 1, CH1",
                    on_change=lambda e: point_form.update({"instrument_channel": e.value}),
                ).props("outlined dense").classes("w-full")

        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not point_form["name"]:
                    ui.notify("Point name is required", type="warning")
                    return
                name = point_form.pop("name")
                # Only include non-empty values
                data = {k: v for k, v in point_form.items() if v}
                on_add(name, data)
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")

    dialog.open()
