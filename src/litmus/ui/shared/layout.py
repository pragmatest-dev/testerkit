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


def get_dialog_counts_by_run() -> dict[str, int]:
    """Map ``run_id`` → number of pending dialogs.

    Shared by the sidebar's Active Tests indicator, the header bell,
    and per-row badges on ``/results`` + ``/dashboard``. Single source
    of truth so the counts can't drift between surfaces. Cheap — reads
    the in-process DialogManager's pending dict.
    """
    counts: dict[str, int] = {}
    for d in _get_pending_dialogs():
        rid = d.get("run_id")
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def _get_active_runs() -> list[dict]:
    """Get active test runs (running or with pending dialogs)."""
    runner = get_runner()
    active = []
    seen_run_ids = set()

    dialog_count_by_run = get_dialog_counts_by_run()

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
            _nav_item("/files", "folder", "Files")

            ui.separator().classes("bg-slate-700 my-2")
            ui.label("CONFIGURATION").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/designer", "design_services", "System Designer", experimental=True)
            _nav_item("/stations", "settings_input_hdmi", "Stations")
            _nav_item("/parts", "inventory_2", "Parts")
            _nav_item("/fixtures", "hub", "Fixtures")
            _nav_item("/instruments", "precision_manufacturing", "Instruments")
            _nav_item("/uuts", "memory", "UUTs")
            _nav_item("/tests", "science", "Tests")
            _nav_item("/profiles", "layers", "Profiles")

            ui.separator().classes("bg-slate-700 my-2")
            ui.label("DOCUMENTATION").classes("text-xs text-slate-500 px-3 pt-2")

            _nav_item("/docs", "menu_book", "Documentation")

    return drawer


def _nav_item(target: str, icon: str, label: str, *, experimental: bool = False):
    """Create a navigation item.

    ``experimental=True`` appends a small beaker marker so operators know the
    area is less mature than the rest of the platform.
    """
    with ui.link(target=target).classes("no-underline"):
        with ui.row().classes(
            "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
        ):
            ui.icon(icon).classes("text-slate-400")
            ui.label(label).classes("text-slate-200")
            if experimental:
                ui.icon("science").classes("text-amber-400 text-sm").tooltip("Experimental")


def create_layout(title: str = "Litmus"):
    """Create the standard page layout with sidebar."""
    # Cache-bust the design-system stylesheet against the file's
    # mtime so edits land in the browser without a hard reload.
    # Cheap (one stat per page render) and only relevant in
    # development; production deploys hash-stable mtimes.
    from pathlib import Path

    from litmus.ui.shared.components import local_date_input_init_script, local_time_init_script

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
    # Date/datetime conversion helpers for the INPUT edge.  All four
    # functions (litmusLocalToUtcDate, litmusUtcToLocalDate,
    # litmusLocalToUtcDateTime, litmusUtcToLocalDateTime) live entirely in
    # the browser JS layer — called directly from the js_handler of
    # utc_date_input widgets.  Python performs no timezone math.
    # Also installs the MutationObserver that localizes the initial
    # displayed value of .litmus-date-utc inputs on page load.
    ui.add_head_html(f"<script>{local_date_input_init_script()}</script>")
    ui.query("body").classes("bg-slate-50")

    create_sidebar()

    # Top header — stable branding on the left, dialogs bell on the
    # right. Each page renders its own ``page_header(title, icon=...,
    # actions=...)`` inside the content area; the chrome bar stays
    # universal.
    _ = title  # used by ``ui.run`` / page metadata; not rendered here
    with ui.header().classes("bg-white border-b border-slate-200 shadow-sm"):
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("⚡").classes("text-lg")  # ⚡ favicon-style branding
            ui.label("Litmus").classes("text-lg font-semibold text-slate-800")
            ui.element("div").classes("flex-1")  # spacer pushes the bell right
            _create_dialogs_bell()


def _create_dialogs_bell() -> None:
    """Render the global "pending operator dialogs" indicator in the header.

    Two states for the icon:

    * No pending dialogs → ``notifications`` icon, slate-400, no badge.
    * 1+ pending → ``notifications_active``, amber-600, count badge.

    Clicking opens a NiceGUI ``ui.menu()`` listing each pending dialog
    with the dialog title, the operator-readable run identifier
    (``<uut_serial_number> · <YYYY-MM-DD HH:MM:SS>`` via
    :func:`lookup_run_label`), and a ``Go →`` link straight to
    ``/live/{run_id}`` — bypasses the run detail page so the operator
    can answer in one click. Refreshed by a 1 s timer matching the
    sidebar's Active Tests indicator.
    """
    from litmus.ui.shared.components import lookup_run_label

    bell_button = (
        ui.button(icon="notifications").props("flat round dense").classes("text-slate-400")
    )

    with bell_button:
        with ui.menu().props("anchor='bottom right' self='top right'"):
            panel = ui.column().classes(
                "p-2 min-w-[320px] max-w-[400px] max-h-[400px] overflow-y-auto"
            )

    def update_bell() -> None:
        dialogs = _get_pending_dialogs()
        count = len(dialogs)
        if count > 0:
            bell_button.props(remove="icon")
            bell_button.props("icon=notifications_active")
            bell_button.classes(replace="text-amber-600")
        else:
            bell_button.props(remove="icon")
            bell_button.props("icon=notifications")
            bell_button.classes(replace="text-slate-400")

        panel.clear()
        with panel:
            if not dialogs:
                ui.label("No dialogs waiting").classes("text-slate-500 italic text-sm px-2 py-3")
                return
            ui.label("Pending operator dialogs").classes(
                "text-xs text-slate-500 uppercase font-medium px-2 pb-1"
            )
            for d in dialogs:
                _render_bell_row(d, lookup_run_label)

    ui.timer(1.0, update_bell)
    update_bell()


def _render_bell_row(dialog: dict, lookup_run_label) -> None:
    """Render one entry in the header bell's pending-dialog list.

    Layout: amber bell glyph, title (top), step + UUT (mid),
    operator-readable run label (bottom), right-aligned ``Go →`` link
    that navigates straight to ``/live/{run_id}``.
    """
    run_id = dialog.get("run_id") or ""
    run_label, _found = lookup_run_label(run_id) if run_id else ("(no run)", False)
    step_name = dialog.get("step_name") or ""
    title = dialog.get("title") or "Dialog"

    with ui.row().classes("w-full items-start gap-2 px-2 py-2 rounded hover:bg-slate-100"):
        ui.icon("notification_important").classes("text-amber-600 mt-1")
        with ui.column().classes("flex-1 gap-0"):
            ui.label(title).classes("text-sm font-medium text-slate-800")
            if step_name:
                ui.label(f"Step: {step_name}").classes("text-xs text-slate-500")
            ui.label(run_label).classes("text-xs text-slate-400")
        if run_id:
            ui.button("Go →", on_click=lambda rid=run_id: ui.navigate.to(f"/live/{rid}")).props(
                "flat dense color=primary"
            ).classes("text-xs")
