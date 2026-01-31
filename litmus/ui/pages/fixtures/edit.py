"""Fixture edit page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    discover_stations,
    load_fixture_config,
    load_station_config,
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
        fixture = config.get("fixture", {})
        create_layout(f"Edit {fixture.get('name', fixture_id)}")
        fixture_data = {
            "id": fixture.get("id", fixture_id),
            "name": fixture.get("name", ""),
            "description": fixture.get("description", ""),
            "product_id": fixture.get("product_id") or fixture.get("product_family", ""),
            "product_revision": fixture.get("product_revision", ""),
        }
        points_data = config.get("points", {})
    else:
        create_layout("Fixture Not Found")
        with ui.column().classes("w-full p-6"):
            ui.label("Fixture not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Fixtures", "/fixtures").classes(
                "text-blue-600 hover:underline"
            )
        return

    # Get available products and stations for dropdowns
    products = discover_products()
    product_options = {p["id"]: p.get("name", p["id"]) for p in products}

    stations = discover_stations()
    # Collect all instrument names across all stations
    all_instruments = set()
    for station in stations:
        station_config = load_station_config(station["id"])
        if station_config:
            all_instruments.update(station_config.get("instruments", {}).keys())
    default_instruments = ["dmm", "psu", "eload", "scope"]
    instrument_options = sorted(all_instruments) if all_instruments else default_instruments

    # Reactive state
    form_data = {"fixture": dict(fixture_data), "points": dict(points_data)}

    with ui.column().classes("w-full p-6 gap-6"):
        # Fixture info card
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Fixture Information").classes("text-lg font-semibold")

            with ui.card_section().classes("flex flex-col gap-4"):
                with ui.row().classes("gap-4"):
                    _labeled_input(
                        "Fixture ID",
                        form_data["fixture"]["id"],
                        readonly=not is_new,
                        on_change=lambda e: form_data["fixture"].update({"id": e.value}),
                    )
                    _labeled_input(
                        "Name",
                        form_data["fixture"]["name"],
                        on_change=lambda e: form_data["fixture"].update({"name": e.value}),
                    )

                with ui.row().classes("gap-4 w-full"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Product").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options=product_options,
                            value=form_data["fixture"]["product_id"],
                            on_change=lambda e: form_data["fixture"].update(
                                {"product_id": e.value}
                            ),
                        ).props("outlined dense").classes("w-full")
                    _labeled_input(
                        "Revision (optional)",
                        form_data["fixture"]["product_revision"],
                        on_change=lambda e: form_data["fixture"].update(
                            {"product_revision": e.value}
                        ),
                    )

                _labeled_textarea(
                    "Description",
                    form_data["fixture"]["description"],
                    on_change=lambda e: form_data["fixture"].update(
                        {"description": e.value}
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
                            lambda name, data: _add_point(form_data, name, data, points_container),
                        ),
                    ).props("flat color=primary")

            with ui.card_section() as points_container:
                _render_points_list(form_data["points"], instrument_options, points_container)

        # Actions
        with ui.row().classes("gap-2"):

            def handle_save():
                fid = form_data["fixture"]["id"]
                if not fid:
                    ui.notify("Fixture ID is required", type="warning")
                    return
                if save_fixture(fid, form_data["fixture"], form_data["points"]):
                    ui.notify("Fixture saved", type="positive")
                    ui.navigate.to(f"/fixtures/{fid}")
                else:
                    ui.notify("Failed to save fixture", type="negative")

            ui.button("Save", icon="save", on_click=handle_save).props("color=primary")
            ui.button(
                "Cancel",
                icon="close",
                on_click=lambda: ui.navigate.to(
                    f"/fixtures/{fixture_id}" if not is_new else "/fixtures"
                ),
            ).props("flat")


def _render_points_list(points: dict, instrument_options: list, container):
    """Render the list of fixture points."""
    container.clear()
    with container:
        if not points:
            ui.label("No pin mappings defined. Click 'Add Point' to create one.").classes(
                "text-slate-500 italic"
            )
            return

        for point_name, point_data in points.items():
            _render_point_row(point_name, point_data, points, instrument_options, container)


def _render_point_row(
    point_name: str, point_data: dict, all_points: dict, instrument_options: list, container
):
    """Render a single point row with inline editing."""
    with ui.card().classes("w-full mb-2"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                ui.label(point_name).classes("font-semibold font-mono")

                def delete_handler(pn=point_name):
                    _delete_point(all_points, pn, container, instrument_options)

                ui.button(icon="delete", on_click=delete_handler).props(
                    "flat dense color=red"
                )

        with ui.card_section():
            with ui.grid(columns=4).classes("gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("DUT Pin").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("dut_pin", ""),
                        on_change=lambda e, pd=point_data: pd.update({"dut_pin": e.value}),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Net").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("net", ""),
                        on_change=lambda e, pd=point_data: pd.update({"net": e.value}),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Instrument").classes("text-xs text-slate-500")
                    ui.select(
                        options=instrument_options,
                        value=point_data.get("instrument", ""),
                        on_change=lambda e, pd=point_data: pd.update({"instrument": e.value}),
                    ).props("outlined dense").classes("w-full")

                with ui.column().classes("gap-1"):
                    ui.label("Channel").classes("text-xs text-slate-500")
                    ui.input(
                        value=point_data.get("instrument_channel", ""),
                        on_change=lambda e, pd=point_data: pd.update(
                            {"instrument_channel": e.value}
                        ),
                    ).props("outlined dense").classes("w-full")


def _add_point(form_data: dict, name: str, data: dict, container):
    """Add a new point to the fixture."""
    form_data["points"][name] = data
    _render_points_list(form_data["points"], list(data.keys()), container)
    container.update()


def _delete_point(points: dict, name: str, container, instrument_options: list):
    """Delete a point from the fixture."""
    if name in points:
        del points[name]
    _render_points_list(points, instrument_options, container)
    container.update()


def _labeled_input(label: str, value: str = "", readonly: bool = False, on_change=None):
    """Create a labeled input field."""
    with ui.column().classes("gap-1 flex-1"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        ui.input(value=value, on_change=on_change).props(props).classes("w-full")


def _labeled_textarea(label: str, value: str = "", on_change=None):
    """Create a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.textarea(value=value, on_change=on_change).props("outlined dense").classes(
            "w-full"
        )


def _show_add_point_dialog(instrument_options: list, on_add: callable):
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
