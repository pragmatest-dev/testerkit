"""Tests list page — one row per ``test_*.py`` file, grouped by directory.

AST-walks every test module under ``tests/`` and renders a row per file
with: test count, class count, markers summary, sidecar presence.
Files in the same directory render under a directory header so the
project's test layout is visible at a glance.
"""

from collections import defaultdict

from nicegui import ui

from litmus.ui.shared.components import page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import TestModuleRow, walk_test_files


@ui.page("/tests")
def tests_page():
    """Flat list of test modules grouped by directory."""
    create_layout("Tests")

    modules = walk_test_files()

    with page_layout():
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("science").classes("text-slate-600")
                ui.label("Tests").classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(modules)} files").props("outline")

        if not modules:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("science").classes("text-4xl text-slate-300")
                ui.label("No test files found.").classes("text-slate-500 mt-2")
                ui.label("Add test_*.py files under tests/.").classes("text-sm text-slate-400")
            return

        # Group by directory, preserve sorted order
        groups: dict[str, list[TestModuleRow]] = defaultdict(list)
        for m in modules:
            groups[m.directory].append(m)

        with ui.element("div").classes("w-full").props('data-testid="tests-table"'):
            for directory in sorted(groups.keys()):
                _render_directory_header(directory)
                for module in groups[directory]:
                    _render_module_row(module)


def _render_directory_header(directory: str) -> None:
    with ui.row().classes("items-center gap-2 mt-4 mb-1 text-slate-600"):
        ui.icon("folder").classes("text-slate-400 text-base")
        ui.label(f"{directory}/").classes("text-sm font-mono font-semibold")


def _render_module_row(module: TestModuleRow) -> None:
    """One file row under its directory header.

    Clickable — navigates to the per-file detail page.
    """
    if module.parse_error:
        with (
            ui.row()
            .classes("items-center gap-3 ml-6 py-1 px-2 text-red-700 cursor-pointer")
            .on("click", lambda *_a, m=module: ui.navigate.to(f"/tests/{m.path}"))
        ):
            ui.icon("warning", size="sm").classes("text-red-600")
            ui.label(module.name).classes("font-mono text-sm")
            ui.label(f"parse error: {module.parse_error[:60]}").classes("text-xs italic")
        return

    test_count = len(module.tests)
    class_count = len(module.classes)
    parametrize_total = sum(t.parametrize_count for t in module.tests)
    markers_summary = sorted({m for t in module.tests for m in t.markers})

    with (
        ui.row()
        .classes("items-center gap-4 ml-6 py-1 px-2 rounded hover:bg-slate-50 cursor-pointer")
        .on("click", lambda *_a, m=module: ui.navigate.to(f"/tests/{m.path}"))
    ):
        ui.icon("description", size="sm").classes("text-slate-500")
        ui.label(module.name).classes("font-mono text-sm flex-1")

        # Test count + class count
        with ui.row().classes("items-center gap-1 text-xs text-slate-600 min-w-24"):
            ui.label(f"{test_count} tests")
            if class_count:
                ui.label(f"· {class_count} class{'es' if class_count > 1 else ''}").classes(
                    "text-slate-500"
                )

        # Parametrize total (rough vector count)
        if parametrize_total:
            ui.badge(f"~{parametrize_total} vectors", color="grey-3").props(
                "outline text-color=grey-7"
            )

        # Markers chips
        with ui.row().classes("items-center gap-1"):
            for marker in markers_summary[:4]:
                ui.badge(marker, color="grey-3").props("text-color=grey-8 dense")
            if len(markers_summary) > 4:
                ui.label(f"+{len(markers_summary) - 4}").classes("text-xs text-slate-500")

        # Sidecar indicator
        if module.has_sidecar:
            ui.badge("sidecar", color="primary").props("outline dense")
        else:
            ui.icon("remove", size="sm").classes("text-slate-300")
