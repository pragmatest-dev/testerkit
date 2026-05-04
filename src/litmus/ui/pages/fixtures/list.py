"""Fixtures listing page — table view."""

from nicegui import ui

from litmus.ui.shared.components import data_table, format_datetime, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_fixtures, discover_products, usage_stats_by


@ui.page("/fixtures")
def fixtures_page():
    """Fixtures listing — one row per fixture, dense table."""
    create_layout("Fixtures")

    fixtures = discover_fixtures()
    products = {p["id"]: p for p in discover_products()}
    usage = usage_stats_by("fixture_id")

    with page_layout():
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

        if not fixtures:
            with ui.card().classes("w-full p-8 text-center"):
                ui.icon("hub", size="xl").classes("text-slate-300")
                ui.label("No fixtures configured").classes("text-xl text-slate-600 mt-4")
                ui.label(
                    "Fixtures define how DUT pins connect to station instruments. "
                    "Each fixture is tied to a product family and maps pin names to "
                    "instrument names."
                ).classes("text-slate-500 mt-2")
                ui.button(
                    "Create Fixture",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/fixtures/new"),
                ).props("color=primary").classes("mt-4")
            return

        columns = [
            {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {
                "name": "product",
                "label": "Product",
                "field": "product",
                "align": "left",
                "sortable": True,
            },
            {"name": "revision", "label": "Rev", "field": "revision", "align": "left"},
            {
                "name": "connections",
                "label": "Connections",
                "field": "connections",
                "align": "right",
                "sortable": True,
            },
            {"name": "runs", "label": "Runs", "field": "runs", "align": "right", "sortable": True},
            {"name": "passed", "label": "Passed", "field": "passed", "align": "right"},
            {"name": "failed", "label": "Failed", "field": "failed", "align": "right"},
            {
                "name": "last_run",
                "label": "Last Run",
                "field": "last_run",
                "align": "left",
                "sortable": True,
            },
        ]
        rows = []
        for f in fixtures:
            product_id = f.product_id or f.product_family or ""
            product = products.get(product_id, {})
            stats = usage.get(f.id, {})
            rows.append(
                {
                    "id": f.id,
                    "name": f.name or "",
                    "product": product.get("name") or product_id,
                    "product_id": product_id,
                    "revision": f.product_revision or "",
                    "connections": len(f.connections or {}),
                    "runs": stats.get("runs", 0),
                    "passed": stats.get("passed", 0),
                    "failed": stats.get("failed", 0),
                    "last_run": format_datetime(stats.get("last_run"))
                    if stats.get("last_run")
                    else "—",
                }
            )

        data_table(
            columns=columns,
            rows=rows,
            row_key="id",
            on_row_click=lambda r: ui.navigate.to(f"/fixtures/{r['id']}"),
            time_columns=["last_run"],
        )
