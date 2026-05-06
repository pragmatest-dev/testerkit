"""Product list page — table view with run-usage stats."""

from nicegui import ui

from litmus.ui.shared.components import data_table, format_datetime, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_products, usage_stats_by


@ui.page("/products")
def products_page():
    """Products listing — one row per product + usage stats from runs."""
    create_layout("Products")

    products = discover_products()
    usage = usage_stats_by("product_id")

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("inventory_2").classes("text-slate-600")
                ui.label("Product Specifications").classes("text-lg font-semibold text-slate-700")
            ui.button(
                "New Product",
                icon="add",
                on_click=lambda: ui.navigate.to("/products/new"),
            ).props("color=primary")

        if not products:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No product specifications found.").classes("text-slate-500")
                ui.label("Create product folders in products/ directory.").classes(
                    "text-sm text-slate-400"
                )
            return

        columns = [
            {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {"name": "revision", "label": "Rev", "field": "revision", "align": "left"},
            {
                "name": "characteristics",
                "label": "Chars",
                "field": "characteristics",
                "align": "right",
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
        for product in products:
            stats = usage.get(product["id"], {})
            rows.append(
                {
                    "id": product["id"],
                    "name": product.get("name", ""),
                    "revision": product.get("revision", "") or "",
                    "characteristics": len(product.get("characteristics", {}) or {}),
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
            on_row_click=lambda r: ui.navigate.to(f"/products/{r['id']}"),
            time_columns=["last_run"],
        )
