"""Tests detail page — show source + sidecar YAML for a tests/ directory.

The route accepts a relative path under the project's tests/ tree
(e.g. ``/tests/tests`` or ``/tests/tests/subgroup``) using FastAPI's
``path:path`` converter so the slash inside the path survives the
routing.

Read-only for v1: code and sidecar render in monospace blocks. Editing
lives in the file system (or the future :mod:`profiles` overlay,
already documented on the page).
"""

from pathlib import Path

from nicegui import ui

from litmus.ui.shared.components import page_layout
from litmus.ui.shared.layout import create_layout


@ui.page("/tests/{path:path}")
def test_detail_page(path: str):
    """One test directory — code + sidecar per ``test_*.py`` file."""
    create_layout(f"Tests · {path}")

    abs_dir = (Path.cwd() / path).resolve()
    cwd = Path.cwd().resolve()
    safe = abs_dir.is_relative_to(cwd)

    with page_layout():
        if not safe or not abs_dir.exists() or not abs_dir.is_dir():
            with ui.card().classes("w-full p-6 text-center"):
                ui.label(f"Test directory '{path}' not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Tests", "/tests").classes("text-blue-600 hover:underline")
            return

        py_files = sorted(abs_dir.glob("test_*.py"))

        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("science").classes("text-slate-600")
                ui.label(path).classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(py_files)} file(s)").props("outline")

        ui.label(
            "Read-only view of the test source + colocated sidecar YAML. "
            "What actually runs is the cascade of inline markers, this sidecar, "
            "and any active profile — see "
        ).classes("text-sm text-slate-600").style("display: inline").tooltip(
            "Sidecar < profile (last-wins). The active profile can override "
            "any sidecar field for the run."
        )

        ui.link("Profiles", "/profiles").classes("text-blue-600 inline-block text-sm")

        if not py_files:
            with ui.card().classes("w-full p-4"):
                ui.label("No test_*.py files found in this directory.").classes(
                    "text-slate-500 italic"
                )
            return

        for py_file in py_files:
            yaml_file = py_file.with_suffix(".yaml")
            _render_test_file_card(py_file, yaml_file if yaml_file.exists() else None)

        with ui.row().classes("mt-2"):
            ui.link("← Back to Tests", "/tests").classes("text-blue-600 hover:underline")


def _render_test_file_card(py_file: Path, yaml_file: Path | None) -> None:
    """Render one collapsible card per test file with code + sidecar tabs."""
    rel_py = py_file.relative_to(Path.cwd()) if py_file.is_relative_to(Path.cwd()) else py_file

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("description").classes("text-slate-600")
                ui.label(py_file.name).classes("font-semibold")
                if yaml_file is None:
                    ui.badge("no sidecar", color="grey-5").props("outline")
                else:
                    ui.badge("sidecar", color="primary").props("outline")
            ui.button(
                "Launch Test",
                icon="play_arrow",
                on_click=lambda f=str(rel_py): ui.navigate.to(f"/launch?test={f}"),
            ).props("color=primary dense")

        with ui.tabs().props("inline-label no-caps dense").classes("w-full") as tabs:
            code_tab = ui.tab("Code", icon="code")
            sidecar_tab = ui.tab("Sidecar YAML", icon="settings") if yaml_file else None

        with ui.tab_panels(tabs, value=code_tab).classes("w-full"):
            with ui.tab_panel(code_tab):
                try:
                    code = py_file.read_text(encoding="utf-8")
                except OSError as exc:
                    ui.label(f"Could not read {py_file}: {exc}").classes("text-red-600 italic")
                else:
                    ui.code(code, language="python").classes("w-full")

            if yaml_file is not None and sidecar_tab is not None:
                with ui.tab_panel(sidecar_tab):
                    try:
                        sidecar = yaml_file.read_text(encoding="utf-8")
                    except OSError as exc:
                        ui.label(f"Could not read {yaml_file}: {exc}").classes(
                            "text-red-600 italic"
                        )
                    else:
                        ui.code(sidecar, language="yaml").classes("w-full")
