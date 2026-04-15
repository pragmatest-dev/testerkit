"""Test directories list page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_tests


@ui.page("/tests")
def tests_page():
    """Tests listing page."""
    create_layout("Tests")

    tests = discover_tests()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("science").classes("text-slate-600")
            ui.label("Test Directories").classes("text-lg font-semibold text-slate-700")

        # Info card
        with ui.card().classes("w-full bg-blue-50 border-blue-200"):
            with ui.card_section():
                with ui.row().classes("items-start gap-3"):
                    ui.icon("info", color="blue").classes("mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label("Test Configuration").classes("font-semibold text-blue-900")
                        ui.label(
                            "Test config (vectors, limits, mocks) is defined in sequence steps. "
                            "Use the Sequence Editor to configure tests."
                        ).classes("text-sm text-blue-800")
                        ui.link("Go to Sequences →", "/sequences").classes(
                            "text-sm text-blue-600 hover:underline"
                        )

        if tests:
            with ui.row().classes("gap-4 flex-wrap"):
                for test in tests:
                    _test_card(test)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("science").classes("text-4xl text-slate-300")
                ui.label("No test directories found.").classes("text-slate-500 mt-2")
                ui.label("Add test_*.py files to a tests/ directory.").classes(
                    "text-sm text-slate-400"
                )


def _test_card(test: dict):
    """Render a test directory card."""
    test_path = test["path"]

    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center gap-2"):
                ui.icon("science").classes("text-slate-600")
                ui.label(test["name"]).classes("text-lg font-semibold")

        with ui.card_section():
            ui.label(test_path).classes("text-sm text-slate-500 font-mono")

        with ui.card_actions():
            ui.button(
                "Sequences",
                icon="format_list_numbered",
                on_click=lambda: ui.navigate.to("/sequences"),
            ).props("flat")
