"""Tests detail page — one ``test_*.py`` file.

Route uses FastAPI's ``path:path`` converter so the file path
(including ``.py``) survives the routing — e.g. ``/tests/tests/test_rail.py``.
"""

from pathlib import Path

from nicegui import ui

from testerkit.ui.shared.components import data_table, format_datetime, page_layout
from testerkit.ui.shared.layout import create_layout
from testerkit.ui.shared.services import step_path_stats, walk_test_module


@ui.page("/tests/{path:path}")
def test_detail_page(path: str):
    """One test module — Tests panel + Code + Sidecar tabs + Launch shortcut."""
    create_layout(f"Tests · {path}")

    abs_path = (Path.cwd() / path).resolve()
    cwd = Path.cwd().resolve()
    safe = abs_path.is_relative_to(cwd)

    with page_layout():
        if not (safe and abs_path.exists() and abs_path.is_file() and abs_path.suffix == ".py"):
            with ui.card().classes("w-full p-6 text-center"):
                ui.label(f"Test file '{path}' not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Tests", "/tests").classes("text-blue-600 hover:underline")
            return

        module = walk_test_module(abs_path)
        yaml_path = abs_path.with_suffix(".yaml")

        # Header — path, badge, Launch
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("description").classes("text-slate-600")
                ui.label(module.path).classes("text-lg font-mono font-semibold text-slate-700")
                ui.badge(f"{len(module.tests)} tests").props("outline")
                if module.has_sidecar:
                    ui.badge("sidecar", color="primary").props("outline")
            ui.button(
                "Launch Test",
                icon="play_arrow",
                on_click=lambda p=module.path: ui.navigate.to(f"/launch?test={p}"),
            ).props("color=primary")

        with ui.row().classes("items-center gap-1 text-sm text-slate-600"):
            ui.label(
                "What actually runs depends on the active profile — sidecar < profile "
                "(last-wins). See"
            )
            ui.link("Profiles", "/profiles").classes("text-blue-600")
            ui.label(".")

        if module.parse_error:
            with ui.card().classes("w-full p-4 border-l-4 border-red-500"):
                ui.label("Could not parse this file:").classes("font-semibold text-red-700")
                ui.label(module.parse_error).classes("text-xs font-mono mt-1")

        # Tests panel — one row per test function (with class qualifier)
        if module.tests and not module.parse_error:
            stats_by_path = step_path_stats()
            _render_tests_panel(module, stats_by_path)

        # Tabs: Code + Sidecar (if present)
        with ui.tabs().props("inline-label no-caps dense").classes("w-full") as tabs:
            code_tab = ui.tab("Code", icon="code")
            sidecar_tab = ui.tab("Sidecar YAML", icon="settings") if module.has_sidecar else None

        with ui.tab_panels(tabs, value=code_tab).classes("w-full"):
            with ui.tab_panel(code_tab):
                _render_code(abs_path)
            if module.has_sidecar and sidecar_tab is not None:
                with ui.tab_panel(sidecar_tab):
                    _render_code(yaml_path)

        with ui.row().classes("mt-2"):
            ui.link("← Back to Tests", "/tests").classes("text-blue-600 hover:underline")


def _render_tests_panel(module, stats_by_path: dict) -> None:
    """Table of test functions in the file with per-test run history."""
    columns = [
        {"name": "name", "label": "Test", "field": "name", "align": "left", "sortable": True},
        {
            "name": "class_name",
            "label": "Class",
            "field": "class_name",
            "align": "left",
            "sortable": True,
        },
        {"name": "markers", "label": "Markers", "field": "markers", "align": "left"},
        {
            "name": "parametrize_count",
            "label": "Vectors",
            "field": "parametrize_count",
            "align": "right",
            "sortable": True,
        },
        {
            "name": "sidecar",
            "label": "Sidecar",
            "field": "sidecar",
            "align": "center",
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
    for t in module.tests:
        key = f"{t.class_name}/{t.name}" if t.class_name else t.name
        s = stats_by_path.get(key)
        rows.append(
            {
                "name": t.name,
                "class_name": t.class_name or "—",
                "markers": ", ".join(t.markers) if t.markers else "—",
                "parametrize_count": t.parametrize_count or "—",
                "sidecar": "✓" if t.has_sidecar_entry else "—",
                "runs": s.runs if s else 0,
                "passed": s.passed if s else 0,
                "failed": s.failed if s else 0,
                "last_run": format_datetime(s.last_run) if s and s.last_run else "—",
            }
        )
    data_table(
        columns=columns,
        rows=rows,
        row_key="name",
        time_columns=["last_run"],
    ).props('data-testid="test-functions-table"')


def _render_code(path: Path) -> None:
    """Render a file as a code block in its language."""
    language = "python" if path.suffix == ".py" else "yaml"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        ui.label(f"Could not read {path}: {exc}").classes("text-red-600 italic")
        return
    ui.code(content, language=language).classes("w-full")
