"""Fixtures listing page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_fixtures, discover_products


@ui.page("/fixtures")
def fixtures_page():
    """Fixtures listing page showing all test fixtures."""
    create_layout("Fixtures")

    fixtures = discover_fixtures()
    products = {p["id"]: p for p in discover_products()}

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("hub").classes("text-slate-600")
                ui.label("Test Fixtures").classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(fixtures)} fixtures").props("outline")
            ui.button(
                "New Fixture",
                icon="add",
                on_click=lambda: ui.navigate.to("/fixtures/new"),
            ).props("color=primary")

        # Info card
        with ui.card().classes("w-full bg-blue-50 border-blue-200"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("info", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("What are Fixtures?").classes("font-semibold text-blue-900")
                        ui.label(
                            "Fixtures define how DUT pins connect to station instruments. "
                            "Each fixture is tied to a product family and maps pin names to "
                            "instrument names (which must exist at the test station)."
                        ).classes("text-sm text-blue-800")

        # Fixtures grid
        if fixtures:
            with ui.row().classes("gap-4 flex-wrap"):
                for fixture in fixtures:
                    _fixture_card(fixture, products.get(fixture.product_family))
        else:
            _render_empty_state()


def _fixture_card(fixture, product: dict | None):
    """Render a fixture card for a FixtureConfig model."""
    points = fixture.points or {}

    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("hub").classes("text-slate-600")
                    ui.label(fixture.name or fixture.id).classes(
                        "text-lg font-semibold"
                    )
                ui.badge(f"{len(points)} points").props("outline")

        with ui.card_section():
            # Product link
            product_id = fixture.product_id or fixture.product_family or ""
            product_revision = fixture.product_revision
            if product_id:
                with ui.row().classes("items-center gap-2 mb-2"):
                    ui.icon("memory", size="xs").classes("text-slate-400")
                    ui.label("Product:").classes("text-sm text-slate-500")
                    if product:
                        label = product.get("name", product_id)
                        if product_revision:
                            label += f" (Rev {product_revision})"
                        ui.link(
                            label,
                            f"/products/{product_id}",
                        ).classes("text-sm text-blue-600 hover:underline")
                    else:
                        label = product_id
                        if product_revision:
                            label += f" (Rev {product_revision})"
                        ui.label(label).classes("text-sm font-mono")

            # Description
            if fixture.description:
                ui.label(fixture.description).classes("text-sm text-slate-600")

            # Points preview
            if points:
                ui.label("Pin Mappings").classes("text-xs text-slate-500 uppercase mt-3")
                with ui.column().classes("gap-1 mt-1"):
                    for point_name, point in list(points.items())[:3]:
                        _point_row(point_name, point)
                    if len(points) > 3:
                        ui.label(f"... and {len(points) - 3} more").classes(
                            "text-xs text-slate-400 italic"
                        )

        with ui.card_actions():
            ui.button(
                "View",
                icon="visibility",
                on_click=lambda _, f=fixture: ui.navigate.to(f"/fixtures/{f.id}"),
            ).props("flat")
            ui.button(
                "Edit",
                icon="edit",
                on_click=lambda _, f=fixture: ui.navigate.to(f"/fixtures/{f.id}/edit"),
            ).props("flat")


def _point_row(point_name: str, point):
    """Render a fixture point row."""
    with ui.row().classes("items-center gap-2 text-sm"):
        # DUT pin
        dut_pin = point.dut_pin or point_name
        ui.label(dut_pin).classes("font-mono text-green-700 bg-green-50 px-1 rounded")
        ui.icon("arrow_forward", size="xs").classes("text-slate-400")
        # Instrument
        instrument = point.instrument or "?"
        channel = point.instrument_channel
        channel_str = f":{channel}" if channel else ""
        ui.label(f"{instrument}{channel_str}").classes(
            "font-mono text-blue-700 bg-blue-50 px-1 rounded"
        )


def _render_empty_state():
    """Render empty state when no fixtures exist."""
    with ui.card().classes("w-full p-8 text-center"):
        ui.icon("hub", size="xl").classes("text-slate-300")
        ui.label("No fixtures configured").classes("text-xl text-slate-600 mt-4")
        ui.label(
            "Create a fixture to define how DUT pins connect to instruments."
        ).classes("text-slate-500 mt-2")
        with ui.row().classes("justify-center mt-4"):
            ui.button(
                "Create Fixture",
                icon="add",
                on_click=lambda: ui.navigate.to("/fixtures/new"),
            ).props("color=primary")
