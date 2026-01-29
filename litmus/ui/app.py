"""NiceGUI-based operator UI."""

from datetime import datetime
from pathlib import Path

from nicegui import app, ui

from litmus.data.backends.parquet import ParquetBackend

# Serve static files
_static_dir = Path(__file__).parent / "static"
app.add_static_files("/static", _static_dir)


# -----------------------------------------------------------------------------
# Capability Matching Helpers
# -----------------------------------------------------------------------------


def _load_product_model(product_id: str):
    """Load a Product model from specs directory.

    Returns the Product Pydantic model, or None if not found.
    """
    from litmus.products.loader import load_product

    search_paths = [
        Path.cwd() / "specs",
        Path.cwd() / "demo" / "specs",
    ]

    for specs_dir in search_paths:
        if not specs_dir.exists():
            continue
        for yaml_file in specs_dir.glob("*.yaml"):
            try:
                product = load_product(yaml_file)
                if product.id == product_id:
                    return product
            except Exception:
                continue
    return None


def _load_instrument_library(instrument_type: str) -> dict | None:
    """Load instrument capabilities from library YAML."""
    import yaml

    library_dir = Path(__file__).parent.parent / "instruments" / "library"
    yaml_file = library_dir / f"{instrument_type}.yaml"

    if not yaml_file.exists():
        return None

    with open(yaml_file) as f:
        return yaml.safe_load(f)


def _get_station_capabilities(station_config: dict) -> list[dict]:
    """Extract all capabilities from a station's instruments.

    Returns a list of capability dicts with direction, domain, signal_types.
    """
    capabilities = []
    instruments = station_config.get("instruments", {})

    for _name, inst in instruments.items():
        inst_type = inst.get("type")
        if not inst_type:
            continue

        library = _load_instrument_library(inst_type)
        if library and "capabilities" in library:
            for cap in library["capabilities"]:
                capabilities.append({
                    "direction": cap.get("direction"),
                    "domain": cap.get("domain"),
                    "signal_types": cap.get("signal_types", []),
                    "name": cap.get("name", ""),
                    "instrument": inst_type,
                })

    return capabilities


def _get_required_capabilities(product) -> list[dict]:
    """Derive required instrument capabilities from product characteristics.

    Uses Characteristic.to_capability_requirement() for direction flipping.
    """
    capabilities = []

    for char_name, char in product.characteristics.items():
        cap = char.to_capability_requirement()
        capabilities.append({
            "direction": cap.direction.value,
            "domain": cap.domain.value,
            "signal_types": [st.value for st in cap.signal_types],
            "characteristic": char_name,
        })

    return capabilities


def _capability_satisfies(station_cap: dict, required_cap: dict) -> bool:
    """Check if a station capability satisfies a requirement.

    Match criteria:
    - direction matches (or station is bidir)
    - domain matches
    - signal_types overlap (at least one common type)
    """
    # Direction must match (or station cap is bidir)
    if station_cap["direction"] != required_cap["direction"]:
        if station_cap["direction"] != "bidir":
            return False

    # Domain must match
    if station_cap["domain"] != required_cap["domain"]:
        return False

    # Signal types must overlap (if specified)
    station_signals = set(station_cap.get("signal_types", []))
    required_signals = set(required_cap.get("signal_types", []))

    if required_signals and station_signals:
        if not station_signals.intersection(required_signals):
            return False

    return True


def _station_compatible_with_product(station_config: dict, product) -> bool:
    """Check if station has instruments satisfying all product requirements."""
    required = _get_required_capabilities(product)
    available = _get_station_capabilities(station_config)

    # Every required capability must be satisfied by at least one station capability
    for req in required:
        if not any(_capability_satisfies(avail, req) for avail in available):
            return False

    return True


def _get_compatible_stations_for_product(product_id: str) -> list[dict]:
    """Get stations that have instruments satisfying product requirements."""
    product = _load_product_model(product_id)
    if not product:
        return []

    compatible = []
    stations = _discover_stations()

    for station in stations:
        station_config = _load_station_config(station["id"])
        if station_config and _station_compatible_with_product(station_config, product):
            compatible.append(station)

    return compatible


def _discover_stations() -> list[dict]:
    """Discover station configurations from YAML files."""
    import yaml

    stations = []
    search_paths = [
        Path.cwd() / "stations",
        Path.cwd() / "demo" / "stations",
    ]

    for stations_dir in search_paths:
        if not stations_dir.exists():
            continue
        for yaml_file in stations_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "station" in data:
                    station_info = data["station"]
                    stations.append(
                        {
                            "id": station_info.get("id", yaml_file.stem),
                            "name": station_info.get("name", yaml_file.stem),
                            "location": station_info.get("location", "Unknown"),
                            "description": station_info.get("description", ""),
                        }
                    )
    return stations


def _discover_tests() -> list[dict]:
    """Discover available test directories."""
    tests = []
    search_paths = [
        Path.cwd() / "tests",
        Path.cwd() / "demo" / "tests",
    ]

    for tests_dir in search_paths:
        if not tests_dir.exists():
            continue
        for test_file in tests_dir.rglob("test_*.py"):
            test_dir = test_file.parent
            cwd = Path.cwd()
            relative = test_dir.relative_to(cwd) if test_dir.is_relative_to(cwd) else test_dir
            test_entry = {"path": str(relative), "name": test_dir.name}
            if test_entry not in tests:
                tests.append(test_entry)
    return tests


def _get_pending_dialogs() -> list[dict]:
    """Get all pending dialogs across all test runs."""
    from litmus.dialogs import get_dialog_manager

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
    from litmus.execution.runner import get_runner

    runner = get_runner()
    active = []
    seen_run_ids = set()

    # Get runs with pending dialogs
    dialogs = _get_pending_dialogs()

    # Get running processes from runner
    for run_id, run_info in list(runner.runs.items()):
        if run_info.status in ("pending", "running"):
            dialog_count = len([d for d in dialogs if d.get("run_id") == run_id])
            active.append({
                "run_id": run_id,
                "status": "dialog" if dialog_count > 0 else "running",
                "dialog_count": dialog_count,
            })
            seen_run_ids.add(run_id)

    # Also show dialogs from runs not in runner (e.g., CLI-started tests)
    for dialog in dialogs:
        run_id = dialog.get("run_id")
        if run_id and run_id not in seen_run_ids:
            dialog_count = len([d for d in dialogs if d.get("run_id") == run_id])
            active.append({
                "run_id": run_id,
                "status": "dialog",
                "dialog_count": dialog_count,
            })
            seen_run_ids.add(run_id)

    return active


def create_sidebar():
    """Create the left-hand navigation sidebar."""
    with ui.left_drawer(value=True).classes("bg-slate-900 text-white") as drawer:
        drawer.props("width=240 behavior=desktop overlay=false bordered")

        # Logo
        with ui.column().classes("p-4"):
            ui.label("⚡ Litmus").classes("text-2xl font-bold")
            ui.label("Hardware Test Platform").classes("text-xs text-slate-400")

        ui.separator().classes("bg-slate-700")

        # Active Tests section (dynamic - updates via timer)
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
                                # Highlight runs needing attention
                                row_classes += " bg-amber-900/30 border border-amber-600/50"
                            with ui.row().classes(row_classes):
                                if run["status"] == "dialog":
                                    ui.icon("notification_important").classes("text-amber-400")
                                else:
                                    ui.icon("autorenew").classes("text-blue-400 animate-spin")
                                with ui.column().classes("gap-0 flex-1"):
                                    ui.label(f"Run {run_id_short}").classes("text-sm text-slate-200")
                                    if run["status"] == "dialog":
                                        ui.label(f"{run['dialog_count']} dialog(s) waiting").classes(
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

            with ui.link(target="/").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("dashboard").classes("text-slate-400")
                    ui.label("Dashboard").classes("text-slate-200")

            with ui.link(target="/launch").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("play_arrow").classes("text-slate-400")
                    ui.label("Launch Test").classes("text-slate-200")

            with ui.link(target="/sequences").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("list_alt").classes("text-slate-400")
                    ui.label("Sequences").classes("text-slate-200")

            with ui.link(target="/results").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("history").classes("text-slate-400")
                    ui.label("Results").classes("text-slate-200")

            with ui.link(target="/stations").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("settings_input_hdmi").classes("text-slate-400")
                    ui.label("Stations").classes("text-slate-200")

            with ui.link(target="/products").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("inventory_2").classes("text-slate-400")
                    ui.label("Products").classes("text-slate-200")

            with ui.link(target="/instruments").classes("no-underline"):
                with ui.row().classes(
                    "w-full px-3 py-2 rounded hover:bg-slate-800 items-center gap-3 cursor-pointer"
                ):
                    ui.icon("precision_manufacturing").classes("text-slate-400")
                    ui.label("Instruments").classes("text-slate-200")

        ui.separator().classes("bg-slate-700")

        # Stations section
        with ui.column().classes("p-2 gap-1"):
            ui.label("STATIONS").classes("text-xs text-slate-500 px-3 pt-2")
            stations = _discover_stations()
            for station in stations:
                with ui.link(target=f"/stations/{station['id']}").classes("no-underline"):
                    row_classes = (
                        "w-full px-3 py-2 rounded hover:bg-slate-800 "
                        "items-center gap-3 cursor-pointer"
                    )
                    with ui.row().classes(row_classes):
                        ui.icon("memory").classes("text-slate-400")
                        with ui.column().classes("gap-0"):
                            ui.label(station["name"]).classes("text-sm text-slate-200")
                            ui.label(station["location"]).classes("text-xs text-slate-500")

    return drawer


def create_layout(title: str):
    """Create the standard page layout with sidebar."""
    # Include Tailwind and global styles
    ui.add_head_html("""
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="/static/global.css">
    """)
    create_sidebar()

    # Header bar
    with ui.header().classes("bg-white border-b border-slate-200"):
        with ui.row().classes("w-full items-center justify-between px-4"):
            ui.label(title).classes("text-xl font-semibold text-slate-800")

            # Dialog notification indicator in header
            notification_container = ui.row().classes("items-center gap-2 hidden")

            def update_header_notifications():
                dialogs = _get_pending_dialogs()
                if dialogs:
                    notification_container.classes(remove="hidden")
                    notification_container.clear()
                    with notification_container:
                        # Group by run_id
                        runs_with_dialogs = {}
                        for d in dialogs:
                            run_id = d["run_id"] or "unknown"
                            if run_id not in runs_with_dialogs:
                                runs_with_dialogs[run_id] = []
                            runs_with_dialogs[run_id].append(d)

                        # Show first dialog's run as a button
                        first_run = list(runs_with_dialogs.keys())[0]
                        dialog_count = sum(len(v) for v in runs_with_dialogs.values())

                        def go_to_dialog():
                            ui.navigate.to(f"/live/{first_run}")

                        with ui.button(on_click=go_to_dialog).props(
                            "flat dense color=amber"
                        ).classes("animate-pulse"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("notification_important").classes("text-amber-600")
                                ui.label(f"{dialog_count} dialog(s) waiting").classes(
                                    "text-amber-700 text-sm font-medium"
                                )
                else:
                    notification_container.classes(add="hidden")

            ui.timer(1.0, update_header_notifications)


def create_dialog_container(run_id: str | None = None):
    """Create a container for operator dialogs.

    This sets up a timer that polls for pending dialogs and displays them.
    Uses in-process DialogManager directly (same process as server).
    """
    from uuid import UUID

    from litmus.dialogs import DialogResponse, DialogType, get_dialog_manager

    dialog_container = ui.column().classes("hidden")
    state = {"current_dialog_id": None, "choice": 0, "input": ""}

    def check_dialogs():
        manager = get_dialog_manager()
        dialog = manager.get_pending_dialog(run_id)

        if dialog and str(dialog.id) != state["current_dialog_id"]:
            state["current_dialog_id"] = str(dialog.id)
            state["choice"] = getattr(dialog, "default_choice", 0) or 0
            state["input"] = getattr(dialog, "default_value", "") or ""
            dialog_container.classes(remove="hidden")
            dialog_container.clear()

            with dialog_container:
                with ui.dialog(value=True) as modal:
                    with ui.card().classes("w-96"):
                        with ui.card_section():
                            ui.label(dialog.title).classes("text-lg font-semibold")

                        with ui.card_section():
                            ui.label(dialog.message).classes("text-slate-600 mb-4")

                            # Dialog-specific content
                            if dialog.type == DialogType.CONFIRM:
                                pass  # Just buttons

                            elif dialog.type == DialogType.CHOICE:
                                choices = getattr(dialog, "choices", [])
                                options = {i: choice for i, choice in enumerate(choices)}
                                ui.radio(
                                    options=options,
                                    value=state["choice"],
                                    on_change=lambda e: state.update({"choice": e.value}),
                                ).classes("w-full")

                            elif dialog.type == DialogType.INPUT:
                                ui.input(
                                    placeholder=getattr(dialog, "placeholder", ""),
                                    value=state["input"],
                                    on_change=lambda e: state.update({"input": e.value}),
                                ).classes("w-full")

                            elif dialog.type == DialogType.IMAGE:
                                if getattr(dialog, "image_url", None):
                                    ui.image(dialog.image_url).classes("w-full rounded")
                                elif getattr(dialog, "image_path", None):
                                    ui.image(dialog.image_path).classes("w-full rounded")

                        with ui.card_actions().classes("justify-end gap-2"):
                            # Capture dialog in closure
                            captured_dialog = dialog

                            def respond_cancel():
                                manager.respond(
                                    captured_dialog.id,
                                    DialogResponse(dialog_id=captured_dialog.id, cancelled=True),
                                )
                                modal.close()
                                state["current_dialog_id"] = None
                                dialog_container.classes(add="hidden")

                            def respond_confirm():
                                response = DialogResponse(
                                    dialog_id=captured_dialog.id,
                                    confirmed=True,
                                )
                                if captured_dialog.type == DialogType.CHOICE:
                                    response.choice = state["choice"]
                                elif captured_dialog.type == DialogType.INPUT:
                                    response.value = state["input"]

                                manager.respond(captured_dialog.id, response)
                                modal.close()
                                state["current_dialog_id"] = None
                                dialog_container.classes(add="hidden")

                            ui.button("Cancel", on_click=respond_cancel).props("flat")
                            ui.button(
                                getattr(dialog, "confirm_label", "OK"),
                                on_click=respond_confirm,
                            ).props("color=primary")

        elif not dialog and state["current_dialog_id"]:
            state["current_dialog_id"] = None
            dialog_container.classes(add="hidden")

    ui.timer(0.5, check_dialogs)
    return dialog_container


def format_datetime(dt) -> str:
    """Format datetime for display."""
    if dt is None:
        return "-"
    if isinstance(dt, str):
        return dt[:19].replace("T", " ")
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def outcome_badge(outcome: str):
    """Create a styled badge for test outcome."""
    colors = {
        "pass": "bg-emerald-100 text-emerald-800",
        "fail": "bg-red-100 text-red-800",
        "error": "bg-orange-100 text-orange-800",
        "running": "bg-blue-100 text-blue-800",
        "pending": "bg-slate-100 text-slate-800",
    }
    color = colors.get(outcome, colors["pending"])
    return ui.label(outcome.upper()).classes(f"px-2 py-1 rounded text-xs font-medium {color}")


@ui.page("/")
def dashboard_page():
    """Main dashboard page."""
    create_layout("Dashboard")

    with ui.column().classes("w-full p-6 gap-6"):
        # Stations section
        with ui.row().classes("items-center gap-2"):
            ui.icon("memory").classes("text-slate-600")
            ui.label("Stations").classes("text-lg font-semibold text-slate-700")

        stations = _discover_stations()

        if stations:
            with ui.row().classes("gap-4 flex-wrap"):
                for station in stations:
                    with ui.card().classes("w-80"):
                        with ui.row().classes("items-start justify-between"):
                            ui.label(station["name"]).classes("text-lg font-semibold")
                            ui.badge("Ready", color="green").props("outline")
                        ui.label(station["description"]).classes("text-sm text-slate-600 mt-1")
                        with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                            with ui.row().classes("items-center gap-1"):
                                ui.icon("tag", size="xs")
                                ui.label(station["id"])
                            with ui.row().classes("items-center gap-1"):
                                ui.icon("location_on", size="xs")
                                ui.label(station["location"])
                        ui.button(
                            "Start Test",
                            icon="play_arrow",
                            on_click=lambda s=station: ui.navigate.to(f"/launch?station={s['id']}"),
                        ).classes("mt-4 w-full").props("outline")
        else:
            ui.label("No stations configured.").classes("text-slate-500 italic")

        # Recent runs section
        with ui.row().classes("items-center gap-2 mt-4"):
            ui.icon("history").classes("text-slate-600")
            ui.label("Recent Runs").classes("text-lg font-semibold text-slate-700")

        backend = ParquetBackend(results_dir="results")
        runs = backend.list_runs(limit=10)

        if runs:
            with ui.card().classes("w-full"):
                columns = [
                    {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                    {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
                    {"name": "station", "label": "Station", "field": "station_id", "align": "left"},
                    {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
                    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
                ]
                rows = [
                    {
                        "run_id": r.get("test_run_id", "")[:8],
                        "full_run_id": r.get("test_run_id", ""),
                        "dut_serial": r.get("dut_serial", ""),
                        "station_id": r.get("station_id", ""),
                        "started_at": format_datetime(r.get("started_at")),
                        "outcome": r.get("outcome", ""),
                    }
                    for r in runs
                ]
                table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
                table.on(
                    "row-click", lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}")
                )
        else:
            ui.label("No test runs yet.").classes("text-slate-500 italic")


@ui.page("/launch")
def _discover_sequences() -> list[dict]:
    """Discover available test sequences from configuration files."""
    from litmus.execution.runner import get_runner

    runner = get_runner()
    return runner.get_available_sequences()


@ui.page("/launch")
def launch_page(station: str = "", sequence: str = ""):
    """Test launch page.

    Args:
        station: Pre-fill station ID from query param
        sequence: Pre-fill sequence ID from query param
    """
    create_layout("Launch Test")

    stations = _discover_stations()
    tests = _discover_tests()
    sequences = _discover_sequences()

    # Form state - use dict for NiceGUI binding
    # Pre-fill from query params if provided
    form = {
        "dut_serial": "",
        "station_id": station,
        "sequence_id": sequence,
        "test_path": "",
        "operator": "",
    }

    async def submit_launch():
        if not form["dut_serial"] or not form["station_id"]:
            ui.notify("Please fill in required fields", type="warning")
            return
        if not form["sequence_id"] and not form["test_path"]:
            ui.notify("Select a test sequence or test suite", type="warning")
            return

        from litmus.api.models import LaunchRequest
        from litmus.execution.runner import get_runner

        request = LaunchRequest(
            dut_serial=form["dut_serial"],
            station_id=form["station_id"],
            sequence_id=form["sequence_id"] or None,
            test_path=form["test_path"] or "tests",
            operator=form["operator"] or None,
        )
        runner = get_runner()
        run_id = await runner.start(request)
        ui.navigate.to(f"/live/{run_id}")

    with ui.column().classes("w-full max-w-xl p-6 gap-6"):
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Test Configuration").classes("text-lg font-semibold")

            with ui.card_section().classes("flex flex-col gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("DUT Serial Number").classes("text-sm font-medium text-slate-700")
                    ui.input(
                        placeholder="e.g., DPB001-0001",
                    ).bind_value(form, "dut_serial").classes("w-full").props("outlined dense")

                with ui.column().classes("gap-1"):
                    ui.label("Station").classes("text-sm font-medium text-slate-700")
                    ui.select(
                        options={s["id"]: f"{s['name']} ({s['id']})" for s in stations},
                    ).bind_value(form, "station_id").classes("w-full").props("outlined dense")

                # Test sequence selection (primary method)
                if sequences:
                    ui.separator().classes("my-2")
                    with ui.column().classes("gap-1"):
                        ui.label("Test Sequence").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options={
                                s["id"]: f"{s['name']} ({s['test_phase']})"
                                for s in sequences
                            },
                        ).bind_value(form, "sequence_id").classes("w-full").props(
                            "outlined dense clearable"
                        )

                    with ui.expansion("Advanced: Run by test path", icon="code").classes(
                        "w-full text-slate-500"
                    ):
                        ui.label(
                            "Run tests by pytest discovery instead of a defined sequence."
                        ).classes("text-xs text-slate-400 mb-2")
                        with ui.column().classes("gap-1"):
                            ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                            ui.select(
                                options={t["path"]: f"{t['name']} ({t['path']})" for t in tests},
                            ).bind_value(form, "test_path").classes("w-full").props(
                                "outlined dense clearable"
                            )
                else:
                    # No sequences defined, show test path as primary
                    with ui.column().classes("gap-1"):
                        ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options={t["path"]: f"{t['name']} ({t['path']})" for t in tests},
                        ).bind_value(form, "test_path").classes("w-full").props(
                            "outlined dense clearable"
                        )

                with ui.column().classes("gap-1"):
                    ui.label("Operator (optional)").classes("text-sm font-medium text-slate-700")
                    ui.input(
                        placeholder="Your name",
                    ).bind_value(form, "operator").classes("w-full").props("outlined dense")

            with ui.card_actions().classes("justify-end"):
                ui.button("Start Test", icon="play_arrow", on_click=submit_launch).props(
                    "color=primary"
                )


@ui.page("/live/{run_id}")
async def live_page(run_id: str):
    """Live test progress page."""
    create_layout(f"Test Run: {run_id}")

    from litmus.execution.runner import get_runner

    runner = get_runner()

    # Dialog container for operator prompts during test
    create_dialog_container(run_id)

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.card().classes("w-full"):
            with ui.card_section():
                with ui.row().classes("items-center gap-4"):
                    ui.label("Status:").classes("font-semibold")
                    status_label = ui.label("Starting...").classes(
                        "px-3 py-1 rounded bg-blue-100 text-blue-800 text-sm font-medium"
                    )
                with ui.row().classes("items-center gap-4 mt-2"):
                    ui.label("Run ID:").classes("text-sm text-slate-500")
                    ui.label(run_id).classes("text-sm font-mono text-slate-600")

            with ui.card_section():
                progress = ui.linear_progress(value=0).classes("w-full")
                step_label = ui.label("").classes("text-sm text-slate-600 mt-2")

        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Output").classes("font-semibold")
            log = ui.log(max_lines=100).classes(
                "w-full h-80 bg-slate-900 text-slate-100 font-mono text-sm"
            )

        results_link = ui.link("View Full Results →", f"/results/{run_id}").classes("hidden")

        async def update_progress():
            async for event in runner.stream(run_id):
                if event["type"] == "output":
                    log.push(event["data"])
                elif event["type"] == "progress":
                    progress.set_value(event["progress_pct"] / 100)
                    step_label.set_text(event.get("current_step") or "")
                    status_label.set_text(event["status"].upper())
                elif event["type"] == "complete":
                    progress.set_value(1.0)
                    if event["returncode"] == 0:
                        status_label.set_text("PASSED")
                        status_label.classes(remove="bg-blue-100 text-blue-800")
                        status_label.classes(add="bg-emerald-100 text-emerald-800")
                    else:
                        status_label.set_text("FAILED")
                        status_label.classes(remove="bg-blue-100 text-blue-800")
                        status_label.classes(add="bg-red-100 text-red-800")
                    results_link.classes(remove="hidden")
                    break

        ui.timer(0.1, update_progress, once=True)


@ui.page("/results")
def results_page():
    """Results listing page."""
    create_layout("Test Results")

    backend = ParquetBackend(results_dir="results")
    runs = backend.list_runs(limit=50)

    with ui.column().classes("w-full p-6 gap-6"):
        if runs:
            with ui.card().classes("w-full"):
                columns = [
                    {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                    {"name": "dut", "label": "DUT", "field": "dut_serial", "align": "left"},
                    {"name": "station", "label": "Station", "field": "station_id", "align": "left"},
                    {"name": "test", "label": "Test", "field": "test_sequence_id", "align": "left"},
                    {"name": "started", "label": "Started", "field": "started_at", "align": "left"},
                    {"name": "steps", "label": "Steps", "field": "steps", "align": "center"},
                    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
                ]
                rows = [
                    {
                        "run_id": r.get("test_run_id", "")[:8],
                        "full_run_id": r.get("test_run_id", ""),
                        "dut_serial": r.get("dut_serial", ""),
                        "station_id": r.get("station_id", ""),
                        "test_sequence_id": r.get("test_sequence_id", ""),
                        "started_at": format_datetime(r.get("started_at")),
                        "steps": f"{r.get('total_steps', 0)} ({r.get('failed_steps', 0)} fail)",
                        "outcome": r.get("outcome", ""),
                    }
                    for r in runs
                ]
                table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
                table.on(
                    "row-click", lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}")
                )
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No test results found.").classes("text-slate-500")
                ui.button(
                    "Launch a Test", icon="play_arrow", on_click=lambda: ui.navigate.to("/launch")
                ).classes("mt-4")


@ui.page("/results/{run_id}")
def result_detail_page(run_id: str):
    """Single result detail page with tabbed interface."""
    backend = ParquetBackend(results_dir="results")
    run = backend.get_run(run_id)
    measurements = backend.get_measurements(run_id) if run else []

    if run:
        outcome = run.get("outcome", "")
        create_layout(f"Run {run.get('test_run_id', '')[:8]}")
    else:
        create_layout("Run Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if run:
            # Summary card (above tabs)
            with ui.card().classes("w-full"):
                with ui.card_section():
                    with ui.row().classes("items-center gap-4"):
                        ui.label("Test Run Summary").classes("text-lg font-semibold")
                        colors = {
                            "pass": "bg-emerald-100 text-emerald-800",
                            "fail": "bg-red-100 text-red-800",
                        }
                        ui.label(outcome.upper()).classes(
                            f"px-3 py-1 rounded text-sm font-medium "
                            f"{colors.get(outcome, 'bg-slate-100')}"
                        )

                with ui.card_section():
                    with ui.grid(columns=3).classes("gap-6"):
                        with ui.column().classes("gap-1"):
                            ui.label("DUT Serial").classes("text-xs text-slate-500 uppercase")
                            ui.label(run.get("dut_serial", "")).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Station").classes("text-xs text-slate-500 uppercase")
                            station_id = run.get("station_id", "")
                            if station_id:
                                ui.link(station_id, f"/stations/{station_id}").classes(
                                    "font-semibold text-blue-600 hover:underline"
                                )
                            else:
                                ui.label("-").classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Test Sequence").classes("text-xs text-slate-500 uppercase")
                            seq_id = run.get("test_sequence_id", "")
                            if seq_id:
                                ui.link(seq_id, f"/sequences/{seq_id}").classes(
                                    "font-semibold text-blue-600 hover:underline"
                                )
                            else:
                                ui.label("-").classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Started").classes("text-xs text-slate-500 uppercase")
                            ui.label(format_datetime(run.get("started_at"))).classes(
                                "font-semibold"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Ended").classes("text-xs text-slate-500 uppercase")
                            ui.label(format_datetime(run.get("ended_at"))).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Results").classes("text-xs text-slate-500 uppercase")
                            total = run.get("total_steps", 0)
                            failed = run.get("failed_steps", 0)
                            ui.label(f"{total} steps, {failed} failed").classes("font-semibold")

            # Tabbed content
            with ui.tabs().classes("w-full") as tabs:
                overview_tab = ui.tab("Overview", icon="dashboard")
                measurements_tab = ui.tab("Measurements", icon="science")
                history_tab = ui.tab("DUT History", icon="history")

            with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
                # Overview tab
                with ui.tab_panel(overview_tab):
                    with ui.card().classes("w-full"):
                        with ui.card_section():
                            ui.label("Test Statistics").classes("font-semibold")
                        with ui.card_section():
                            total = run.get("total_steps", 0)
                            failed = run.get("failed_steps", 0)
                            passed = total - failed

                            with ui.row().classes("gap-8"):
                                with ui.column().classes("items-center"):
                                    ui.label(str(total)).classes("text-3xl font-bold text-slate-700")
                                    ui.label("Total Steps").classes("text-sm text-slate-500")
                                with ui.column().classes("items-center"):
                                    ui.label(str(passed)).classes(
                                        "text-3xl font-bold text-emerald-600"
                                    )
                                    ui.label("Passed").classes("text-sm text-slate-500")
                                with ui.column().classes("items-center"):
                                    ui.label(str(failed)).classes("text-3xl font-bold text-red-600")
                                    ui.label("Failed").classes("text-sm text-slate-500")
                                if total > 0:
                                    with ui.column().classes("items-center"):
                                        pct = int((passed / total) * 100)
                                        ui.label(f"{pct}%").classes(
                                            "text-3xl font-bold text-blue-600"
                                        )
                                        ui.label("Pass Rate").classes("text-sm text-slate-500")

                # Measurements tab
                with ui.tab_panel(measurements_tab):
                    if measurements:
                        with ui.card().classes("w-full"):
                            columns = [
                                {
                                    "name": "step",
                                    "label": "Step",
                                    "field": "step_name",
                                    "align": "left",
                                },
                                {
                                    "name": "name",
                                    "label": "Measurement",
                                    "field": "name",
                                    "align": "left",
                                },
                                {
                                    "name": "value",
                                    "label": "Value",
                                    "field": "value",
                                    "align": "right",
                                },
                                {
                                    "name": "limits",
                                    "label": "Limits",
                                    "field": "limits",
                                    "align": "center",
                                },
                                {
                                    "name": "outcome",
                                    "label": "Outcome",
                                    "field": "outcome",
                                    "align": "center",
                                },
                            ]
                            rows = [
                                {
                                    "step_name": m.get("step_name", ""),
                                    "name": m.get("measurement_name", ""),
                                    "value": f"{m.get('value', '-')} {m.get('units', '')}".strip(),
                                    "limits": f"{m.get('low_limit', '')} – {m.get('high_limit', '')}",
                                    "outcome": m.get("outcome", ""),
                                }
                                for m in measurements
                            ]
                            ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
                    else:
                        ui.label("No measurements recorded.").classes("text-slate-500 italic")

                # DUT History tab
                with ui.tab_panel(history_tab):
                    dut_serial = run.get("dut_serial", "")
                    all_runs = backend.list_runs(limit=100)
                    dut_runs = [
                        r
                        for r in all_runs
                        if r.get("dut_serial") == dut_serial and r.get("test_run_id") != run_id
                    ]

                    if dut_runs:
                        with ui.card().classes("w-full"):
                            ui.label(f"Other runs for DUT: {dut_serial}").classes(
                                "text-sm text-slate-500 mb-2"
                            )
                            columns = [
                                {
                                    "name": "run_id",
                                    "label": "Run ID",
                                    "field": "run_id",
                                    "align": "left",
                                },
                                {
                                    "name": "sequence",
                                    "label": "Sequence",
                                    "field": "sequence",
                                    "align": "left",
                                },
                                {
                                    "name": "started",
                                    "label": "Started",
                                    "field": "started",
                                    "align": "left",
                                },
                                {
                                    "name": "outcome",
                                    "label": "Outcome",
                                    "field": "outcome",
                                    "align": "center",
                                },
                            ]
                            rows = [
                                {
                                    "run_id": r.get("test_run_id", "")[:8],
                                    "full_run_id": r.get("test_run_id", ""),
                                    "sequence": r.get("test_sequence_id", ""),
                                    "started": format_datetime(r.get("started_at")),
                                    "outcome": r.get("outcome", ""),
                                }
                                for r in dut_runs[:10]
                            ]
                            table = ui.table(columns=columns, rows=rows, row_key="run_id").classes(
                                "w-full"
                            )
                            table.on(
                                "row-click",
                                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
                            )
                    else:
                        ui.label(f"No other runs found for DUT: {dut_serial}").classes(
                            "text-slate-500 italic"
                        )

            ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("Run not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")


def _load_station_config(station_id: str) -> dict | None:
    """Load full station configuration from YAML."""
    import yaml

    search_paths = [
        Path.cwd() / "stations",
        Path.cwd() / "demo" / "stations",
    ]

    for stations_dir in search_paths:
        if not stations_dir.exists():
            continue
        for yaml_file in stations_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "station" in data:
                    if data["station"].get("id") == station_id:
                        return data
    return None


@ui.page("/stations")
def stations_page():
    """Stations listing page."""
    create_layout("Stations")

    stations = _discover_stations()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("settings_input_hdmi").classes("text-slate-600")
            ui.label("Test Stations").classes("text-lg font-semibold text-slate-700")

        if stations:
            with ui.row().classes("gap-4 flex-wrap"):
                for station in stations:
                    config = _load_station_config(station["id"])
                    instruments = config.get("instruments", {}) if config else {}

                    with ui.card().classes("w-96"):
                        with ui.card_section():
                            with ui.row().classes("items-start justify-between"):
                                ui.label(station["name"]).classes("text-lg font-semibold")
                                ui.badge("Online", color="green").props("outline")

                        with ui.card_section():
                            ui.label(station["description"]).classes("text-sm text-slate-600")
                            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("tag", size="xs")
                                    ui.label(station["id"])
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("location_on", size="xs")
                                    ui.label(station["location"])

                            if instruments:
                                ui.label("Instruments").classes(
                                    "text-xs text-slate-500 uppercase mt-4"
                                )
                                for name, inst in instruments.items():
                                    with ui.row().classes("items-center gap-2 mt-1"):
                                        simulated = inst.get("simulated", False)
                                        ui.icon(
                                            "sim_card" if simulated else "cable", size="xs"
                                        ).classes("text-slate-400")
                                        ui.label(name).classes("text-sm")
                                        ui.label(inst.get("type", "")).classes(
                                            "text-xs text-slate-500"
                                        )

                        with ui.card_actions():
                            ui.button(
                                "View Details",
                                icon="visibility",
                                on_click=lambda s=station: ui.navigate.to(f"/stations/{s['id']}"),
                            ).props("flat")
                            ui.button(
                                "Start Test",
                                icon="play_arrow",
                                on_click=lambda s=station: ui.navigate.to(
                                    f"/launch?station={s['id']}"
                                ),
                            ).props("flat color=primary")
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No stations configured.").classes("text-slate-500")


@ui.page("/stations/{station_id}")
def station_detail_page(station_id: str):
    """Station detail page with tabbed interface."""
    config = _load_station_config(station_id)

    if config:
        station = config.get("station", {})
        create_layout(station.get("name", station_id))
    else:
        create_layout("Station Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if config:
            station = config.get("station", {})
            instruments = config.get("instruments", {})
            phases = config.get("supported_phases", [])

            # Station info card (above tabs)
            with ui.card().classes("w-full"):
                with ui.card_section():
                    with ui.row().classes("items-center gap-4"):
                        ui.label("Station Information").classes("text-lg font-semibold")
                        ui.badge("Online", color="green").props("outline")

                with ui.card_section():
                    with ui.grid(columns=3).classes("gap-6"):
                        with ui.column().classes("gap-1"):
                            ui.label("Station ID").classes("text-xs text-slate-500 uppercase")
                            ui.label(station.get("id", "")).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Name").classes("text-xs text-slate-500 uppercase")
                            ui.label(station.get("name", "")).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Location").classes("text-xs text-slate-500 uppercase")
                            ui.label(station.get("location", "")).classes("font-semibold")
                        with ui.column().classes("gap-1 col-span-3"):
                            ui.label("Description").classes("text-xs text-slate-500 uppercase")
                            ui.label(station.get("description", "")).classes("font-semibold")

                    if phases:
                        with ui.row().classes("gap-2 mt-4"):
                            ui.label("Supported Phases:").classes(
                                "text-xs text-slate-500 uppercase self-center"
                            )
                            for phase in phases:
                                ui.badge(phase).props("outline")

            # Tabbed content
            with ui.tabs().classes("w-full") as tabs:
                instruments_tab = ui.tab("Instruments", icon="cable")
                sequences_tab = ui.tab("Sequences", icon="list_alt")
                runs_tab = ui.tab("Recent Runs", icon="history")

            with ui.tab_panels(tabs, value=instruments_tab).classes("w-full"):
                # Instruments tab
                with ui.tab_panel(instruments_tab):
                    if instruments:
                        with ui.row().classes("gap-4 flex-wrap"):
                            for name, inst in instruments.items():
                                simulated = inst.get("simulated", False)
                                with ui.card().classes("w-80"):
                                    with ui.card_section():
                                        with ui.row().classes("items-center justify-between"):
                                            with ui.row().classes("items-center gap-2"):
                                                ui.icon(
                                                    "sim_card" if simulated else "cable"
                                                ).classes("text-slate-600")
                                                ui.label(name).classes("text-lg font-semibold")
                                            if simulated:
                                                ui.badge("Simulated", color="blue").props("outline")
                                            else:
                                                ui.badge("Ready", color="green").props("outline")

                                    with ui.card_section():
                                        with ui.column().classes("gap-2"):
                                            with ui.row().classes("items-center gap-2"):
                                                ui.label("Type:").classes("text-sm text-slate-500")
                                                ui.link(
                                                    inst.get("type", "unknown"),
                                                    "/instruments",
                                                ).classes(
                                                    "text-sm font-medium text-blue-600 hover:underline"
                                                )
                                            with ui.row().classes("items-center gap-2"):
                                                ui.label("Resource:").classes(
                                                    "text-sm text-slate-500"
                                                )
                                                ui.label(inst.get("resource", "N/A")).classes(
                                                    "text-sm font-mono"
                                                )
                                            if inst.get("description"):
                                                ui.label(inst["description"]).classes(
                                                    "text-sm text-slate-600 mt-2"
                                                )
                    else:
                        ui.label("No instruments configured.").classes("text-slate-500 italic")

                # Sequences tab
                with ui.tab_panel(sequences_tab):
                    # Station capabilities summary
                    station_caps = _get_station_capabilities(config)
                    if station_caps:
                        with ui.card().classes("w-full mb-4"):
                            with ui.card_section():
                                ui.label("Station Capabilities").classes("font-semibold")
                                ui.label(
                                    "What this station's instruments can measure/source"
                                ).classes("text-xs text-slate-500")
                            with ui.card_section():
                                columns = [
                                    {
                                        "name": "instrument",
                                        "label": "Instrument",
                                        "field": "instrument",
                                        "align": "left",
                                    },
                                    {
                                        "name": "capability",
                                        "label": "Capability",
                                        "field": "capability",
                                    },
                                    {
                                        "name": "direction",
                                        "label": "Direction",
                                        "field": "direction",
                                    },
                                    {"name": "domain", "label": "Domain", "field": "domain"},
                                ]
                                rows = [
                                    {
                                        "instrument": cap["instrument"],
                                        "capability": cap["name"],
                                        "direction": cap["direction"],
                                        "domain": cap["domain"],
                                    }
                                    for cap in station_caps
                                ]
                                ui.table(
                                    columns=columns, rows=rows, row_key="capability"
                                ).classes("w-full")

                    # Compatible sequences (based on capability matching)
                    sequences = _discover_sequences()
                    compatible_sequences = []
                    for seq in sequences:
                        product_family = seq.get("product_family")
                        if product_family:
                            product = _load_product_model(product_family)
                            if product and _station_compatible_with_product(config, product):
                                compatible_sequences.append(seq)
                        else:
                            # Sequences without product_family are always shown
                            compatible_sequences.append(seq)

                    with ui.row().classes("items-center gap-2 mt-4 mb-2"):
                        ui.icon("list_alt").classes("text-slate-600")
                        ui.label("Compatible Sequences").classes(
                            "font-semibold text-slate-700"
                        )
                        ui.badge(f"{len(compatible_sequences)} found").props("outline")

                    if compatible_sequences:
                        with ui.row().classes("gap-4 flex-wrap"):
                            for seq in compatible_sequences:
                                with ui.card().classes("w-72"):
                                    with ui.card_section():
                                        ui.label(seq["name"]).classes("font-semibold")
                                        if seq.get("test_phase"):
                                            phase_colors = {
                                                "validation": "blue",
                                                "characterization": "purple",
                                                "production": "green",
                                            }
                                            ui.badge(
                                                seq["test_phase"],
                                                color=phase_colors.get(seq["test_phase"], "gray"),
                                            ).props("outline")
                                        ui.label(seq.get("description", "")[:60]).classes(
                                            "text-sm text-slate-500 mt-1"
                                        )
                                        if seq.get("product_family"):
                                            ui.label(f"Product: {seq['product_family']}").classes(
                                                "text-xs text-slate-400 mt-1"
                                            )
                                    with ui.card_actions():
                                        ui.button(
                                            "Run",
                                            icon="play_arrow",
                                            on_click=lambda s=seq: ui.navigate.to(
                                                f"/launch?sequence={s['id']}&station={station_id}"
                                            ),
                                        ).props("flat dense color=primary")
                    else:
                        ui.label("No compatible sequences found.").classes(
                            "text-slate-500 italic"
                        )

                # Recent runs tab
                with ui.tab_panel(runs_tab):
                    backend = ParquetBackend(results_dir="results")
                    all_runs = backend.list_runs(limit=100)
                    station_runs = [r for r in all_runs if r.get("station_id") == station_id]

                    if station_runs:
                        with ui.card().classes("w-full"):
                            columns = [
                                {
                                    "name": "run_id",
                                    "label": "Run ID",
                                    "field": "run_id",
                                    "align": "left",
                                },
                                {
                                    "name": "dut",
                                    "label": "DUT",
                                    "field": "dut",
                                    "align": "left",
                                },
                                {
                                    "name": "sequence",
                                    "label": "Sequence",
                                    "field": "sequence",
                                    "align": "left",
                                },
                                {
                                    "name": "started",
                                    "label": "Started",
                                    "field": "started",
                                    "align": "left",
                                },
                                {
                                    "name": "outcome",
                                    "label": "Outcome",
                                    "field": "outcome",
                                    "align": "center",
                                },
                            ]
                            rows = [
                                {
                                    "run_id": r.get("test_run_id", "")[:8],
                                    "full_run_id": r.get("test_run_id", ""),
                                    "dut": r.get("dut_serial", ""),
                                    "sequence": r.get("test_sequence_id", ""),
                                    "started": format_datetime(r.get("started_at")),
                                    "outcome": r.get("outcome", ""),
                                }
                                for r in station_runs[:20]
                            ]
                            table = ui.table(
                                columns=columns, rows=rows, row_key="run_id"
                            ).classes("w-full")
                            table.on(
                                "row-click",
                                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
                            )
                    else:
                        ui.label("No runs found on this station.").classes(
                            "text-slate-500 italic"
                        )

            # Actions
            with ui.row().classes("mt-6 gap-2"):
                ui.button(
                    "Start Test",
                    icon="play_arrow",
                    on_click=lambda: ui.navigate.to(f"/launch?station={station_id}"),
                ).props("color=primary")
                ui.link("← Back to Stations", "/stations").classes(
                    "text-blue-600 hover:underline self-center"
                )
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("Station not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Stations", "/stations").classes("text-blue-600 hover:underline")


def _discover_products() -> list[dict]:
    """Discover product specifications from YAML files."""
    import yaml

    products = []
    search_paths = [
        Path.cwd() / "specs",
        Path.cwd() / "demo" / "specs",
    ]

    for specs_dir in search_paths:
        if not specs_dir.exists():
            continue
        for yaml_file in specs_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "product" in data:
                    product_info = data["product"]
                    products.append({
                        "id": product_info.get("id", yaml_file.stem),
                        "name": product_info.get("name", yaml_file.stem),
                        "description": product_info.get("description", ""),
                        "revision": product_info.get("revision", ""),
                        "characteristics": data.get("characteristics", {}),
                        "test_requirements": data.get("test_requirements", {}),
                        "file": str(yaml_file),
                    })
    return products


def _discover_instrument_types() -> list[dict]:
    """Discover available instrument types from YAML definitions."""
    import yaml

    instruments = []
    library_dir = Path(__file__).parent.parent / "instruments" / "library"

    if not library_dir.exists():
        return instruments

    for yaml_file in library_dir.glob("*.yaml"):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            if data and "instrument" in data:
                inst = data["instrument"]
                capabilities = data.get("capabilities", [])
                instruments.append({
                    "type": inst.get("type", yaml_file.stem),
                    "name": inst.get("name", yaml_file.stem),
                    "description": inst.get("description", ""),
                    "icon": inst.get("icon", "device_unknown"),
                    "capabilities": [c.get("name", "") for c in capabilities],
                    "capability_details": capabilities,
                })

    return instruments


@ui.page("/products")
def products_page():
    """Products listing page."""
    create_layout("Products")

    products = _discover_products()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("inventory_2").classes("text-slate-600")
            ui.label("Product Specifications").classes("text-lg font-semibold text-slate-700")

        if products:
            with ui.row().classes("gap-4 flex-wrap"):
                for product in products:
                    char_count = len(product.get("characteristics", {}))
                    req_count = len(product.get("test_requirements", {}))

                    with ui.card().classes("w-96"):
                        with ui.card_section():
                            with ui.row().classes("items-start justify-between"):
                                ui.label(product["name"]).classes("text-lg font-semibold")
                                if product.get("revision"):
                                    ui.badge(f"Rev {product['revision']}").props("outline")

                        with ui.card_section():
                            ui.label(product["description"]).classes("text-sm text-slate-600")
                            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("tag", size="xs")
                                    ui.label(product["id"])

                            with ui.row().classes("gap-4 mt-3"):
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("tune", size="xs").classes("text-slate-400")
                                    ui.label(f"{char_count} characteristics").classes(
                                        "text-sm text-slate-600"
                                    )
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("checklist", size="xs").classes("text-slate-400")
                                    ui.label(f"{req_count} test requirements").classes(
                                        "text-sm text-slate-600"
                                    )

                        with ui.card_actions():
                            ui.button(
                                "View Details",
                                icon="visibility",
                                on_click=lambda p=product: ui.navigate.to(
                                    f"/products/{p['id']}"
                                ),
                            ).props("flat")
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No product specifications found.").classes("text-slate-500")
                ui.label("Add YAML files to the specs/ directory.").classes(
                    "text-sm text-slate-400"
                )


@ui.page("/products/{product_id}")
def product_detail_page(product_id: str):
    """Product detail page with tabbed interface."""
    products = _discover_products()
    product = next((p for p in products if p["id"] == product_id), None)

    if product:
        create_layout(product["name"])
    else:
        create_layout("Product Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if product:
            # Product info card (above tabs)
            with ui.card().classes("w-full"):
                with ui.card_section():
                    with ui.row().classes("items-center gap-4"):
                        ui.label("Product Information").classes("text-lg font-semibold")
                        if product.get("revision"):
                            ui.badge(f"Rev {product['revision']}").props("outline")

                with ui.card_section():
                    with ui.grid(columns=2).classes("gap-6"):
                        with ui.column().classes("gap-1"):
                            ui.label("Product ID").classes("text-xs text-slate-500 uppercase")
                            ui.label(product["id"]).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Name").classes("text-xs text-slate-500 uppercase")
                            ui.label(product["name"]).classes("font-semibold")
                        with ui.column().classes("gap-1 col-span-2"):
                            ui.label("Description").classes("text-xs text-slate-500 uppercase")
                            ui.label(product["description"]).classes("font-semibold")

            # Tabbed content
            characteristics = product.get("characteristics", {})
            requirements = product.get("test_requirements", {})

            with ui.tabs().classes("w-full") as tabs:
                char_tab = ui.tab("Characteristics", icon="tune")
                req_tab = ui.tab("Requirements", icon="checklist")
                seq_tab = ui.tab("Sequences", icon="list_alt")

            with ui.tab_panels(tabs, value=char_tab).classes("w-full"):
                # Characteristics tab
                with ui.tab_panel(char_tab):
                    if characteristics:
                        with ui.card().classes("w-full"):
                            columns = [
                                {
                                    "name": "name",
                                    "label": "Name",
                                    "field": "name",
                                    "align": "left",
                                },
                                {
                                    "name": "direction",
                                    "label": "Direction",
                                    "field": "direction",
                                },
                                {"name": "domain", "label": "Domain", "field": "domain"},
                                {"name": "units", "label": "Units", "field": "units"},
                                {
                                    "name": "conditions",
                                    "label": "Conditions",
                                    "field": "conditions",
                                },
                            ]
                            rows = [
                                {
                                    "name": name,
                                    "direction": char.get("direction", ""),
                                    "domain": char.get("domain", ""),
                                    "units": char.get("units", ""),
                                    "conditions": len(char.get("conditions", [])),
                                }
                                for name, char in characteristics.items()
                            ]
                            ui.table(columns=columns, rows=rows, row_key="name").classes(
                                "w-full"
                            )
                    else:
                        ui.label("No characteristics defined.").classes("text-slate-500 italic")

                # Requirements tab
                with ui.tab_panel(req_tab):
                    if requirements:
                        with ui.card().classes("w-full"):
                            columns = [
                                {
                                    "name": "name",
                                    "label": "Name",
                                    "field": "name",
                                    "align": "left",
                                },
                                {
                                    "name": "char_ref",
                                    "label": "Characteristic",
                                    "field": "char_ref",
                                },
                                {"name": "priority", "label": "Priority", "field": "priority"},
                                {"name": "guardband", "label": "Guardband", "field": "guardband"},
                                {
                                    "name": "description",
                                    "label": "Description",
                                    "field": "description",
                                },
                            ]
                            rows = [
                                {
                                    "name": name,
                                    "char_ref": req.get("characteristic_ref", "-"),
                                    "priority": req.get("priority", "standard"),
                                    "guardband": f"{req.get('guardband_pct', 0)}%",
                                    "description": req.get("description", "")[:50],
                                }
                                for name, req in requirements.items()
                            ]
                            ui.table(columns=columns, rows=rows, row_key="name").classes(
                                "w-full"
                            )
                    else:
                        ui.label("No test requirements defined.").classes(
                            "text-slate-500 italic"
                        )

                # Sequences tab
                with ui.tab_panel(seq_tab):
                    # Required instrument capabilities
                    product_model = _load_product_model(product_id)
                    if product_model:
                        required_caps = _get_required_capabilities(product_model)
                        if required_caps:
                            with ui.card().classes("w-full mb-4"):
                                with ui.card_section():
                                    ui.label("Required Instrument Capabilities").classes(
                                        "font-semibold"
                                    )
                                    ui.label(
                                        "Instruments needed to test this product"
                                    ).classes("text-xs text-slate-500")
                                with ui.card_section():
                                    columns = [
                                        {
                                            "name": "char",
                                            "label": "Characteristic",
                                            "field": "char",
                                            "align": "left",
                                        },
                                        {
                                            "name": "direction",
                                            "label": "Inst. Direction",
                                            "field": "direction",
                                        },
                                        {"name": "domain", "label": "Domain", "field": "domain"},
                                        {"name": "signals", "label": "Signals", "field": "signals"},
                                    ]
                                    rows = [
                                        {
                                            "char": cap["characteristic"],
                                            "direction": cap["direction"],
                                            "domain": cap["domain"],
                                            "signals": ", ".join(cap["signal_types"]),
                                        }
                                        for cap in required_caps
                                    ]
                                    ui.table(columns=columns, rows=rows, row_key="char").classes(
                                        "w-full"
                                    )

                        # Compatible stations
                        compatible_stations = _get_compatible_stations_for_product(product_id)
                        with ui.row().classes("items-center gap-2 mt-4 mb-2"):
                            ui.icon("memory").classes("text-slate-600")
                            ui.label("Compatible Stations").classes(
                                "font-semibold text-slate-700"
                            )
                            ui.badge(f"{len(compatible_stations)} found").props("outline")

                        if compatible_stations:
                            with ui.row().classes("gap-4 flex-wrap mb-4"):
                                for station in compatible_stations:
                                    with ui.card().classes("w-64"):
                                        with ui.card_section():
                                            ui.label(station["name"]).classes("font-semibold")
                                            ui.label(station["location"]).classes(
                                                "text-xs text-slate-500"
                                            )
                                        with ui.card_actions():
                                            ui.button(
                                                "View",
                                                icon="visibility",
                                                on_click=lambda s=station: ui.navigate.to(
                                                    f"/stations/{s['id']}"
                                                ),
                                            ).props("flat dense")
                        else:
                            ui.label(
                                "No compatible stations found."
                            ).classes("text-slate-500 italic mb-4")

                    # Sequences for this product
                    with ui.row().classes("items-center gap-2 mt-4 mb-2"):
                        ui.icon("list_alt").classes("text-slate-600")
                        ui.label("Test Sequences").classes("font-semibold text-slate-700")

                    sequences = _discover_sequences()
                    product_sequences = [
                        s for s in sequences if s.get("product_family") == product_id
                    ]

                    if product_sequences:
                        with ui.row().classes("gap-4 flex-wrap"):
                            for seq in product_sequences:
                                with ui.card().classes("w-72"):
                                    with ui.card_section():
                                        ui.label(seq["name"]).classes("font-semibold")
                                        if seq.get("test_phase"):
                                            phase_colors = {
                                                "validation": "blue",
                                                "characterization": "purple",
                                                "production": "green",
                                            }
                                            ui.badge(
                                                seq["test_phase"],
                                                color=phase_colors.get(seq["test_phase"], "gray"),
                                            ).props("outline")
                                        ui.label(seq.get("description", "")[:60]).classes(
                                            "text-sm text-slate-500 mt-1"
                                        )
                                    with ui.card_actions():
                                        ui.button(
                                            "View",
                                            icon="visibility",
                                            on_click=lambda s=seq: ui.navigate.to(
                                                f"/sequences/{s['id']}"
                                            ),
                                        ).props("flat dense")
                                        ui.button(
                                            "Run",
                                            icon="play_arrow",
                                            on_click=lambda s=seq: ui.navigate.to(
                                                f"/launch?sequence={s['id']}"
                                            ),
                                        ).props("flat dense color=primary")
                    else:
                        ui.label("No sequences defined for this product.").classes(
                            "text-slate-500 italic"
                        )

            ui.link("← Back to Products", "/products").classes(
                "text-blue-600 hover:underline mt-4"
            )
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("Product not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Products", "/products").classes(
                    "text-blue-600 hover:underline"
                )


@ui.page("/instruments")
def instruments_page():
    """Instruments listing page."""
    create_layout("Instruments")

    instrument_types = _discover_instrument_types()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("precision_manufacturing").classes("text-slate-600")
            ui.label("Instrument Types").classes("text-lg font-semibold text-slate-700")

        with ui.row().classes("gap-4 flex-wrap"):
            for inst in instrument_types:
                with ui.card().classes("w-80"):
                    with ui.card_section():
                        with ui.row().classes("items-center gap-3"):
                            ui.icon(inst["icon"]).classes("text-2xl text-slate-600")
                            with ui.column().classes("gap-0"):
                                ui.label(inst["name"]).classes("text-lg font-semibold")
                                ui.label(inst["type"]).classes("text-xs text-slate-500 font-mono")

                    with ui.card_section():
                        ui.label(inst["description"]).classes("text-sm text-slate-600")

                        ui.label("Capabilities").classes(
                            "text-xs text-slate-500 uppercase mt-3"
                        )
                        with ui.row().classes("gap-2 flex-wrap mt-1"):
                            for cap in inst["capabilities"]:
                                ui.badge(cap).props("outline")


def _load_sequence(sequence_id: str) -> dict | None:
    """Load full sequence configuration from YAML."""
    from litmus.execution.runner import get_runner

    runner = get_runner()
    return runner._load_sequence(sequence_id)


@ui.page("/sequences")
def sequences_page():
    """Sequences listing page."""
    create_layout("Test Sequences")

    sequences = _discover_sequences()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("list_alt").classes("text-slate-600")
            ui.label("Test Sequences").classes("text-lg font-semibold text-slate-700")

        if sequences:
            with ui.row().classes("gap-4 flex-wrap"):
                for seq in sequences:
                    full_seq = _load_sequence(seq["id"])
                    step_count = len(full_seq.get("steps", [])) if full_seq else 0

                    with ui.card().classes("w-96"):
                        with ui.card_section():
                            with ui.row().classes("items-start justify-between"):
                                ui.label(seq["name"]).classes("text-lg font-semibold")
                                phase = seq.get("test_phase")
                                if phase:
                                    phase_colors = {
                                        "validation": "blue",
                                        "characterization": "purple",
                                        "production": "green",
                                    }
                                    ui.badge(phase, color=phase_colors.get(phase, "gray")).props(
                                        "outline"
                                    )

                        with ui.card_section():
                            ui.label(seq["description"]).classes("text-sm text-slate-600")
                            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("tag", size="xs")
                                    ui.label(seq["id"])
                                if seq.get("product_family"):
                                    with ui.row().classes("items-center gap-1"):
                                        ui.icon("inventory_2", size="xs")
                                        ui.link(
                                            seq["product_family"],
                                            f"/products/{seq['product_family']}",
                                        ).classes("text-blue-600 hover:underline")

                            with ui.row().classes("items-center gap-1 mt-2"):
                                ui.icon("format_list_numbered", size="xs").classes(
                                    "text-slate-400"
                                )
                                ui.label(f"{step_count} steps").classes("text-sm text-slate-600")

                        with ui.card_actions():
                            ui.button(
                                "View Details",
                                icon="visibility",
                                on_click=lambda s=seq: ui.navigate.to(f"/sequences/{s['id']}"),
                            ).props("flat")
                            ui.button(
                                "Run",
                                icon="play_arrow",
                                on_click=lambda s=seq: ui.navigate.to(
                                    f"/launch?sequence={s['id']}"
                                ),
                            ).props("flat color=primary")
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("No test sequences found.").classes("text-slate-500")
                ui.label("Add YAML files to the sequences/ directory.").classes(
                    "text-sm text-slate-400"
                )


@ui.page("/sequences/{sequence_id}")
def sequence_detail_page(sequence_id: str):
    """Sequence detail page with tabbed interface."""
    from litmus.execution.runner import get_runner

    runner = get_runner()
    seq = runner._load_sequence(sequence_id)

    if seq:
        create_layout(seq.get("name") or sequence_id)
    else:
        create_layout("Sequence Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if seq:
            # Sequence info card (above tabs)
            with ui.card().classes("w-full"):
                with ui.card_section():
                    with ui.row().classes("items-center gap-4"):
                        ui.label("Sequence Information").classes("text-lg font-semibold")
                        phase = seq.get("test_phase")
                        if phase:
                            phase_colors = {
                                "validation": "blue",
                                "characterization": "purple",
                                "production": "green",
                            }
                            ui.badge(phase, color=phase_colors.get(phase, "gray")).props("outline")

                with ui.card_section():
                    with ui.grid(columns=3).classes("gap-6"):
                        with ui.column().classes("gap-1"):
                            ui.label("Sequence ID").classes("text-xs text-slate-500 uppercase")
                            ui.label(seq.get("id", "")).classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Product Family").classes("text-xs text-slate-500 uppercase")
                            product_family = seq.get("product_family")
                            if product_family:
                                ui.link(product_family, f"/products/{product_family}").classes(
                                    "font-semibold text-blue-600 hover:underline"
                                )
                            else:
                                ui.label("-").classes("font-semibold")
                        with ui.column().classes("gap-1"):
                            ui.label("Test Phase").classes("text-xs text-slate-500 uppercase")
                            ui.label(seq.get("test_phase") or "-").classes("font-semibold")
                        with ui.column().classes("gap-1 col-span-3"):
                            ui.label("Description").classes("text-xs text-slate-500 uppercase")
                            ui.label(seq.get("description", "")).classes("font-semibold")

            # Tabbed content
            steps = seq.get("steps", [])
            dialogs = seq.get("dialogs", {})

            with ui.tabs().classes("w-full") as tabs:
                steps_tab = ui.tab("Steps", icon="format_list_numbered")
                requirements_tab = ui.tab("Requirements", icon="rule")
                dialogs_tab = ui.tab("Dialogs", icon="chat")
                runs_tab = ui.tab("Recent Runs", icon="history")

            with ui.tab_panels(tabs, value=steps_tab).classes("w-full"):
                # Steps tab
                with ui.tab_panel(steps_tab):
                    with ui.row().classes("items-center gap-2 mb-4"):
                        ui.badge(f"{len(steps)} steps").props("outline")

                    if steps:
                        expanded_tests = runner._expand_sequence(sequence_id)

                        with ui.card().classes("w-full"):
                            for i, step in enumerate(steps):
                                step_id = step.get("id", f"step_{i}")
                                is_sequence_ref = bool(step.get("sequence"))

                                with ui.expansion(
                                    text=step_id,
                                    icon="folder" if is_sequence_ref else "science",
                                ).classes("w-full"):
                                    with ui.column().classes("gap-2 p-2"):
                                        if step.get("description"):
                                            ui.label(step["description"]).classes("text-slate-600")

                                        if step.get("test"):
                                            with ui.row().classes("items-center gap-2"):
                                                ui.label("Test:").classes(
                                                    "text-xs text-slate-500 uppercase"
                                                )
                                                ui.label(step["test"]).classes("font-mono text-sm")

                                        if step.get("sequence"):
                                            with ui.row().classes("items-center gap-2"):
                                                ui.label("Sequence:").classes(
                                                    "text-xs text-slate-500 uppercase"
                                                )
                                                ui.link(
                                                    step["sequence"],
                                                    f"/sequences/{step['sequence']}",
                                                ).classes("font-mono text-sm text-blue-600")

                                            nested_tests = runner._expand_sequence(step["sequence"])
                                            if nested_tests:
                                                ui.label("Expands to:").classes(
                                                    "text-xs text-slate-500 uppercase mt-2"
                                                )
                                                for test in nested_tests:
                                                    with ui.row().classes("items-center gap-2 ml-4"):
                                                        ui.icon(
                                                            "subdirectory_arrow_right", size="xs"
                                                        ).classes("text-slate-400")
                                                        ui.label(test).classes("font-mono text-xs")

                                        config_items = []
                                        if step.get("limit_ref"):
                                            config_items.append(("Limit Ref", step["limit_ref"]))
                                        if step.get("pre_dialog"):
                                            config_items.append(("Pre-Dialog", step["pre_dialog"]))
                                        if step.get("post_dialog"):
                                            config_items.append(("Post-Dialog", step["post_dialog"]))
                                        if step.get("skip_on"):
                                            config_items.append(
                                                ("Skip On", ", ".join(step["skip_on"]))
                                            )
                                        if step.get("retry"):
                                            retry = step["retry"]
                                            config_items.append(
                                                ("Retry", f"{retry.get('max_attempts', 1)} attempts")
                                            )

                                        if config_items:
                                            ui.separator().classes("my-2")
                                            with ui.grid(columns=2).classes("gap-2"):
                                                for label, value in config_items:
                                                    ui.label(label).classes(
                                                        "text-xs text-slate-500 uppercase"
                                                    )
                                                    ui.label(value).classes("text-sm")

                        # Expanded test order
                        with ui.card().classes("w-full mt-4"):
                            with ui.card_section():
                                ui.label("Expanded Test Order").classes("text-sm font-semibold")
                                ui.label(
                                    "Actual order tests will run when this sequence executes."
                                ).classes("text-xs text-slate-500")
                            with ui.card_section():
                                for i, test in enumerate(expanded_tests, 1):
                                    with ui.row().classes("items-center gap-2"):
                                        ui.label(f"{i}.").classes(
                                            "text-slate-400 w-6 text-right"
                                        )
                                        ui.label(test).classes("font-mono text-sm")
                    else:
                        ui.label("No steps defined.").classes("text-slate-500 italic")

                # Requirements tab
                with ui.tab_panel(requirements_tab):
                    # Station & Fixture requirements
                    with ui.card().classes("w-full"):
                        with ui.card_section():
                            ui.label("Station & Fixture Requirements").classes("font-semibold")
                        with ui.card_section():
                            with ui.grid(columns=2).classes("gap-6"):
                                with ui.column().classes("gap-1"):
                                    ui.label("Required Fixture").classes(
                                        "text-xs text-slate-500 uppercase"
                                    )
                                    ui.label(seq.get("required_fixture") or "-").classes(
                                        "font-semibold font-mono"
                                    )
                                with ui.column().classes("gap-1"):
                                    ui.label("Required Station Type").classes(
                                        "text-xs text-slate-500 uppercase"
                                    )
                                    ui.label(seq.get("required_station_type") or "-").classes(
                                        "font-semibold font-mono"
                                    )
                                with ui.column().classes("gap-1"):
                                    ui.label("Timeout").classes(
                                        "text-xs text-slate-500 uppercase"
                                    )
                                    timeout = seq.get("timeout_seconds")
                                    ui.label(f"{timeout}s" if timeout else "-").classes(
                                        "font-semibold"
                                    )
                                with ui.column().classes("gap-1"):
                                    ui.label("pytest Args").classes(
                                        "text-xs text-slate-500 uppercase"
                                    )
                                    args = seq.get("pytest_args", [])
                                    ui.label(" ".join(args) if args else "-").classes(
                                        "font-semibold font-mono"
                                    )

                    # Required capabilities (derived from product)
                    product_family = seq.get("product_family")
                    if product_family:
                        product = _load_product_model(product_family)
                        if product:
                            required_caps = _get_required_capabilities(product)
                            if required_caps:
                                with ui.card().classes("w-full mt-4"):
                                    with ui.card_section():
                                        ui.label("Required Instrument Capabilities").classes(
                                            "font-semibold"
                                        )
                                        ui.label(
                                            f"Derived from product: {product_family}"
                                        ).classes("text-xs text-slate-500")
                                    with ui.card_section():
                                        columns = [
                                            {
                                                "name": "char",
                                                "label": "Characteristic",
                                                "field": "char",
                                                "align": "left",
                                            },
                                            {
                                                "name": "direction",
                                                "label": "Direction",
                                                "field": "direction",
                                            },
                                            {
                                                "name": "domain",
                                                "label": "Domain",
                                                "field": "domain",
                                            },
                                            {
                                                "name": "signals",
                                                "label": "Signals",
                                                "field": "signals",
                                            },
                                        ]
                                        rows = [
                                            {
                                                "char": cap["characteristic"],
                                                "direction": cap["direction"],
                                                "domain": cap["domain"],
                                                "signals": ", ".join(cap["signal_types"]),
                                            }
                                            for cap in required_caps
                                        ]
                                        ui.table(
                                            columns=columns, rows=rows, row_key="char"
                                        ).classes("w-full")

                            # Compatible stations
                            compatible_stations = _get_compatible_stations_for_product(
                                product_family
                            )
                            with ui.row().classes("items-center gap-2 mt-6"):
                                ui.icon("memory").classes("text-slate-600")
                                ui.label("Compatible Stations").classes(
                                    "text-lg font-semibold text-slate-700"
                                )
                                ui.badge(f"{len(compatible_stations)} found").props("outline")

                            if compatible_stations:
                                with ui.row().classes("gap-4 flex-wrap"):
                                    for station in compatible_stations:
                                        with ui.card().classes("w-64"):
                                            with ui.card_section():
                                                ui.label(station["name"]).classes(
                                                    "font-semibold"
                                                )
                                                ui.label(station["location"]).classes(
                                                    "text-xs text-slate-500"
                                                )
                                            with ui.card_actions():
                                                ui.button(
                                                    "Run Here",
                                                    icon="play_arrow",
                                                    on_click=lambda s=station: ui.navigate.to(
                                                        f"/launch?sequence={sequence_id}&station={s['id']}"
                                                    ),
                                                ).props("flat dense color=primary")
                            else:
                                ui.label(
                                    "No compatible stations found. Check instrument capabilities."
                                ).classes("text-slate-500 italic")

                # Dialogs tab
                with ui.tab_panel(dialogs_tab):
                    if dialogs:
                        with ui.card().classes("w-full"):
                            columns = [
                                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                                {"name": "type", "label": "Type", "field": "type", "align": "left"},
                                {
                                    "name": "message",
                                    "label": "Message",
                                    "field": "message",
                                    "align": "left",
                                },
                            ]
                            rows = [
                                {
                                    "id": dialog_id,
                                    "type": dialog.get("dialog_type", ""),
                                    "message": dialog.get("message", "")[:60] + "..."
                                    if len(dialog.get("message", "")) > 60
                                    else dialog.get("message", ""),
                                }
                                for dialog_id, dialog in dialogs.items()
                            ]
                            ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
                    else:
                        ui.label("No dialogs defined.").classes("text-slate-500 italic")

                # Recent runs tab
                with ui.tab_panel(runs_tab):
                    backend = ParquetBackend(results_dir="results")
                    all_runs = backend.list_runs(limit=100)
                    seq_runs = [
                        r for r in all_runs if r.get("test_sequence_id") == sequence_id
                    ]

                    if seq_runs:
                        with ui.card().classes("w-full"):
                            columns = [
                                {
                                    "name": "run_id",
                                    "label": "Run ID",
                                    "field": "run_id",
                                    "align": "left",
                                },
                                {
                                    "name": "dut",
                                    "label": "DUT",
                                    "field": "dut",
                                    "align": "left",
                                },
                                {
                                    "name": "station",
                                    "label": "Station",
                                    "field": "station",
                                    "align": "left",
                                },
                                {
                                    "name": "started",
                                    "label": "Started",
                                    "field": "started",
                                    "align": "left",
                                },
                                {
                                    "name": "outcome",
                                    "label": "Outcome",
                                    "field": "outcome",
                                    "align": "center",
                                },
                            ]
                            rows = [
                                {
                                    "run_id": r.get("test_run_id", "")[:8],
                                    "full_run_id": r.get("test_run_id", ""),
                                    "dut": r.get("dut_serial", ""),
                                    "station": r.get("station_id", ""),
                                    "started": format_datetime(r.get("started_at")),
                                    "outcome": r.get("outcome", ""),
                                }
                                for r in seq_runs[:20]
                            ]
                            table = ui.table(
                                columns=columns, rows=rows, row_key="run_id"
                            ).classes("w-full")
                            table.on(
                                "row-click",
                                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
                            )
                    else:
                        ui.label("No runs found for this sequence.").classes(
                            "text-slate-500 italic"
                        )

            # Actions
            with ui.row().classes("mt-6 gap-2"):
                ui.button(
                    "Run Sequence",
                    icon="play_arrow",
                    on_click=lambda: ui.navigate.to(f"/launch?sequence={sequence_id}"),
                ).props("color=primary")
                ui.link("← Back to Sequences", "/sequences").classes(
                    "text-blue-600 hover:underline self-center"
                )
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label("Sequence not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Sequences", "/sequences").classes(
                    "text-blue-600 hover:underline"
                )
