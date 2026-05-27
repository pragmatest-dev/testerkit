"""Test directories list page — table view."""

from nicegui import ui

from litmus.ui.shared.components import data_table, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_tests


@ui.page("/tests")
def tests_page():
    """Tests listing — one row per test directory."""
    create_layout("Tests")

    tests = discover_tests()

    with page_layout():
        with ui.row().classes("items-center gap-2"):
            ui.icon("science").classes("text-slate-600")
            ui.label("Test Directories").classes("text-lg font-semibold text-slate-700")
            ui.badge(f"{len(tests)} dirs").props("outline")

        if not tests:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("science").classes("text-4xl text-slate-300")
                ui.label("No test directories found.").classes("text-slate-500 mt-2")
                ui.label("Add test_*.py files to a tests/ directory.").classes(
                    "text-sm text-slate-400"
                )
            return

        columns = [
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {"name": "path", "label": "Path", "field": "path", "align": "left", "sortable": True},
        ]
        rows = [{"name": t["name"], "path": t["path"]} for t in tests]
        data_table(
            columns=columns,
            rows=rows,
            row_key="path",
            on_row_click=lambda r: ui.navigate.to(f"/tests/{r['path']}"),
        ).props('data-testid="tests-table"')
