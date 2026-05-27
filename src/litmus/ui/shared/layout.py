"""Shared layout components - sidebar and page layout."""

from nicegui import ui

from litmus.api.dialogs import get_dialog_manager
from litmus.api.runner import get_runner


def _get_pending_dialogs() -> list[dict]:
    """Get all pending dialogs across all test runs."""
    manager = get_dialog_manager()
    dialogs = manager.get_pending_dialogs()
    return [
        {
            "id": str(d.id),
            "run_id": d.run_id,
            "title": d.title,
            "message": d.message,
            "type": d.type.value,
            "step_name": d.step_name,
        }
        for d in dialogs
    ]


def _get_active_runs() -> list[dict]:
    """Get active test runs (running or with pending dialogs)."""
    runner = get_runner()
    active = []
    seen_run_ids = set()

    dialogs = _get_pending_dialogs()
    dialog_count_by_run: dict[str, int] = {}
    for d in dialogs:
        rid = d.get("run_id")
        if rid:
            dialog_count_by_run[rid] = dialog_count_by_run.get(rid, 0) + 1

    for run_id, run_info in list(runner.runs.items()):
        if run_info.status in ("pending", "running"):
            dialog_count = dialog_count_by_run.get(run_id, 0)
            active.append(
                {
                    "run_id": run_id,
                    "status": "dialog" if dialog_count > 0 else "running",
                    "dialog_count": dialog_count,
                }
            )
            seen_run_ids.add(run_id)

    for run_id, dialog_count in dialog_count_by_run.items():
        if run_id not in seen_run_ids:
            active.append(
                {
                    "run_id": run_id,
                    "status": "dialog",
                    "dialog_count": dialog_count,
                }
            )
            seen_run_ids.add(run_id)

    return active


def create_sidebar():
    """Create the left-hand navigation sidebar."""
    with ui.left_drawer(value=True).classes("bg-slate-900 text-white") as drawer:
        drawer.props("width=240 behavior=desktop overlay=false bordered")

        # Logo
        with ui.column().classes("p-4"):
            ui.label("Litmus").classes("text-2xl font-bold")
            ui.label("Hardware Test Platform").classes("text-xs text-slate-400")

        ui.separator().classes("bg-slate-700")

        # Active Tests section (dynamic)
        active_tests_container = ui.column().classes("p-2 gap-1 hidden")

        def update_active_tests():
            active_runs = _get_active_runs()
            if active_runs:
                active_tests_container.classes(remove="hidden")
                active_tests_container.clear()
                with active_tests_container:
                    ui.label("ACTIVE TESTS").classes("text-xs text-slate-500 px-3 pt-2")
                    for run in active_runs:
                        run_id_short = run["run_id"][:8] if run["run_id"] else "unknown"
                        with ui.link(target=f"/live/{run['run_id']}").classes("no-underline"):
                            row_classes = (
                                "w-full px-3 py-2 rounded hover:bg-slate-800 "
                                "items-center gap-3 cursor-pointer"
                            )
                            if run["status"] == "dialog":
                                row_classes += " bg-amber-900/30 border border-amber-600/50"
                            with ui.row().classes(row_classes):
                                if run["status"] == "dialog":
                                    ui.icon("notification_important").classes("text-amber-400")
                                else:
                                    ui.icon("autorenew").classes("text-blue-400 animate-spin")
                                with ui.column().classes("gap-0 flex-1"):
                                    label = f"Run {run_id_short}"
                                    ui.label(label).classes("text-sm text-slate-200")
                                    if run["status"] == "dialog":
                                        count = run["dialog_count"]
                                        ui.label(f"{count} dialog(s) waiting").classes(
                                            "text-xs text-amber-400"
                                        )
                                    else:
                                        ui.label("Running...").classes("text-xs text-blue-400")
                    ui.separator().classes("bg-slate-700 mt-2")
            else:
                active_tests_container.classes(add="hidden")

        ui.timer(1.0, update_active_tests)

        # Navigation
        with ui.column().classes("p-2 gap-1"):
            ui.label("NAVIGATION").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/", "dashboard", "Dashboard")
            _nav_item("/launch", "play_arrow", "Launch Test")
            _nav_item("/results", "history", "Results")
            _nav_item("/metrics", "analytics", "Metrics")
            _nav_item("/explore", "scatter_plot", "Measurements")

            ui.separator().classes("bg-slate-700 my-2")
            ui.label("DATA STORES").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/events", "notifications", "Events")
            _nav_item("/channels", "signal_cellular_alt", "Channels")

            ui.separator().classes("bg-slate-700 my-2")
            ui.label("CONFIGURATION").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/designer", "design_services", "System Designer")
            _nav_item("/stations", "settings_input_hdmi", "Stations")
            _nav_item("/products", "inventory_2", "Products")
            _nav_item("/fixtures", "hub", "Fixtures")
            _nav_item("/instruments", "precision_manufacturing", "Instruments")
            _nav_item("/duts", "memory", "DUTs")
            _nav_item("/tests", "science", "Tests")
            _nav_item("/profiles", "layers", "Profiles")

            ui.separator().classes("bg-slate-700 my-2")
            ui.label("DOCUMENTATION").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/docs", "menu_book", "Documentation")

    return drawer


def _nav_item(target: str, icon: str, label: str):
    """Create a navigation item."""
    with ui.link(target=target).classes("no-underline"):
        with ui.row().classes(
            "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
        ):
            ui.icon(icon).classes("text-slate-400")
            ui.label(label).classes("text-slate-200")


def create_layout(title: str = "Litmus"):
    """Create the standard page layout with sidebar."""
    # Cache-bust the design-system stylesheet against the file's
    # mtime so edits land in the browser without a hard reload.
    # Cheap (one stat per page render) and only relevant in
    # development; production deploys hash-stable mtimes.
    from pathlib import Path

    from litmus.ui.shared.components import local_time_init_script

    css_path = Path(__file__).parent.parent / "static" / "global.css"
    try:
        version = int(css_path.stat().st_mtime)
    except OSError:
        version = 0
    ui.add_head_html(f'<link rel="stylesheet" href="/static/global.css?v={version}">')
    # Browser-local-time formatter: every ``.litmus-time`` span on the
    # page (rendered via :func:`format_datetime`) gets rewritten from
    # UTC ISO to the browser's locale on load. UTC stored, local
    # displayed — the design-system convention.
    ui.add_head_html(
        "<script>document.addEventListener('DOMContentLoaded', () => {"
        f" {local_time_init_script()} "
        "});</script>"
    )
    ui.query("body").classes("bg-slate-50")

    create_sidebar()

    # Top header — stable branding, NOT the page name. Each page
    # renders its own ``page_header(title, icon=..., actions=...)``
    # inside the content area; duplicating the title in the chrome
    # bar above ate ~15% of the vertical space saying the same word
    # twice. ``title`` is kept for the document title (browser tab)
    # only.
    _ = title  # used by ``ui.run`` / page metadata; not rendered here
    with ui.header().classes("bg-white border-b border-slate-200 shadow-sm"):
        with ui.row().classes("items-center gap-2"):
            ui.label("⚡").classes("text-lg")  # ⚡ favicon-style branding
            ui.label("Litmus").classes("text-lg font-semibold text-slate-800")
