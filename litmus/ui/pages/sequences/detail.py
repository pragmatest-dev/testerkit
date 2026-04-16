"""Sequence detail page."""

from nicegui import ui

from litmus.config.project import load_project_config
from litmus.data.backends.parquet import ParquetBackend
from litmus.models.config import TestSequenceConfig
from litmus.ui.shared.components import format_datetime, setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    get_compatible_stations_for_product,
    get_required_capabilities,
    load_product_model,
)


def _load_full_sequence(sequence_id: str):
    """Load full sequence configuration from YAML."""
    from litmus.api.runner import get_runner

    runner = get_runner()
    return runner._load_sequence(sequence_id)


def _expand_sequence(sequence_id: str) -> list[str]:
    """Expand sequence to list of test paths."""
    from litmus.api.runner import get_runner

    runner = get_runner()
    return runner._expand_sequence(sequence_id)


@ui.page("/sequences/{sequence_id}")
def sequence_detail_page(sequence_id: str):
    """Sequence detail page with tabbed interface."""
    seq = _load_full_sequence(sequence_id)

    if seq:
        create_layout(seq.name or sequence_id)
    else:
        create_layout("Sequence Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if seq:
            _render_sequence_detail(sequence_id, seq)
        else:
            _render_not_found()


def _render_sequence_detail(sequence_id: str, seq: TestSequenceConfig):
    """Render the sequence detail view."""
    # Sequence info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Sequence Information").classes("text-lg font-semibold")
                    phase = seq.test_phase
                    if phase:
                        phase_colors = {
                            "validation": "blue",
                            "characterization": "purple",
                            "production": "green",
                        }
                        ui.badge(phase, color=phase_colors.get(phase, "gray")).props("outline")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/sequences"),
                    ).props("flat")
                    ui.button(
                        "Edit",
                        icon="edit",
                        on_click=lambda: ui.navigate.to(f"/sequences/{sequence_id}/edit"),
                    ).props("flat")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                _info_field("Sequence ID", seq.id)
                _info_field_link("Product Family", seq.product_family, "/products")
                _info_field("Test Phase", seq.test_phase or "-")
                with ui.column().classes("gap-1 col-span-3"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(seq.description or "").classes("font-semibold")

    # Tabbed content
    steps = seq.steps
    dialogs = seq.dialogs

    with ui.tabs().classes("w-full") as tabs:
        steps_tab = ui.tab("Steps", icon="format_list_numbered")
        requirements_tab = ui.tab("Requirements", icon="rule")
        dialogs_tab = ui.tab("Dialogs", icon="chat")
        runs_tab = ui.tab("Recent Runs", icon="history")

    setup_hash_sync_for_tabs(tabs, ["Steps", "Requirements", "Dialogs", "Recent Runs"])

    with ui.tab_panels(tabs, value=steps_tab).classes("w-full"):
        with ui.tab_panel(steps_tab):
            _render_steps_tab(sequence_id, steps)

        with ui.tab_panel(requirements_tab):
            _render_requirements_tab(sequence_id, seq)

        with ui.tab_panel(dialogs_tab):
            _render_dialogs_tab(dialogs)

        with ui.tab_panel(runs_tab):
            _render_runs_tab(sequence_id)

    ui.link("← Back to Sequences", "/sequences").classes("text-blue-600 hover:underline mt-4")


def _info_field(label: str, value: str):
    """Render an info field."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("font-semibold")


def _info_field_link(label: str, value: str | None, base_path: str):
    """Render an info field with a link."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        if value:
            ui.link(value, f"{base_path}/{value}").classes(
                "font-semibold text-blue-600 hover:underline"
            )
        else:
            ui.label("-").classes("font-semibold")


def _render_steps_tab(sequence_id: str, steps: list):
    """Render the steps tab."""
    with ui.row().classes("items-center gap-2 mb-4"):
        ui.badge(f"{len(steps)} steps").props("outline")

    if steps:
        expanded_tests = _expand_sequence(sequence_id)

        with ui.card().classes("w-full"):
            for i, step in enumerate(steps):
                _render_step_expansion(i, step, sequence_id)

        # Expanded test order
        with ui.card().classes("w-full mt-4"):
            with ui.card_section():
                ui.label("Expanded Test Order").classes("text-sm font-semibold")
                ui.label("Actual order tests will run when this sequence executes.").classes(
                    "text-xs text-slate-500"
                )
            with ui.card_section():
                for i, test in enumerate(expanded_tests, 1):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{i}.").classes("text-slate-400 w-6 text-right")
                        ui.label(test).classes("font-mono text-sm")
    else:
        ui.label("No steps defined.").classes("text-slate-500 italic")


def _render_step_expansion(index: int, step: dict, sequence_id: str):
    """Render a step expansion panel."""
    step_id = step.get("id", f"step_{index}")
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
                    ui.label("Test:").classes("text-xs text-slate-500 uppercase")
                    ui.label(step["test"]).classes("font-mono text-sm")

            if step.get("sequence"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Sequence:").classes("text-xs text-slate-500 uppercase")
                    ui.link(step["sequence"], f"/sequences/{step['sequence']}").classes(
                        "font-mono text-sm text-blue-600"
                    )

                nested_tests = _expand_sequence(step["sequence"])
                if nested_tests:
                    ui.label("Expands to:").classes("text-xs text-slate-500 uppercase mt-2")
                    for test in nested_tests:
                        with ui.row().classes("items-center gap-2 ml-4"):
                            ui.icon("subdirectory_arrow_right", size="xs").classes("text-slate-400")
                            ui.label(test).classes("font-mono text-xs")

            # Config items
            config_items = []
            if step.get("limit_ref"):
                config_items.append(("Limit Ref", step["limit_ref"]))
            if step.get("pre_dialog"):
                config_items.append(("Pre-Dialog", step["pre_dialog"]))
            if step.get("post_dialog"):
                config_items.append(("Post-Dialog", step["post_dialog"]))
            if step.get("skip_on"):
                config_items.append(("Skip On", ", ".join(step["skip_on"])))
            if step.get("retry"):
                retry = step["retry"]
                config_items.append(("Retry", f"{retry.get('max_attempts', 1)} attempts"))

            # Vectors summary
            vectors = step.get("vectors")
            if vectors:
                if isinstance(vectors, list):
                    config_items.append(("Vectors", f"{len(vectors)} vectors"))
                elif isinstance(vectors, dict):
                    config_items.append(("Vectors", f"expand: {vectors.get('expand', 'product')}"))

            # Limits summary
            limits = step.get("limits")
            if limits:
                limit_parts = []
                for name, lim in limits.items():
                    low = lim.get("low")
                    high = lim.get("high")
                    if low is not None and high is not None:
                        limit_parts.append(f"{name}: [{low}, {high}]")
                    else:
                        limit_parts.append(name)
                config_items.append(("Limits", ", ".join(limit_parts)))

            # Mocks summary
            mocks = step.get("mocks")
            if mocks:
                config_items.append(("Mocks", f"{len(mocks)} mock values"))

            if config_items:
                ui.separator().classes("my-2")
                with ui.grid(columns=2).classes("gap-2"):
                    for label, value in config_items:
                        ui.label(label).classes("text-xs text-slate-500 uppercase")
                        ui.label(value).classes("text-sm")


def _render_requirements_tab(sequence_id: str, seq: TestSequenceConfig):
    """Render the requirements tab."""
    # Station & Fixture requirements
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Station & Fixture Requirements").classes("font-semibold")
        with ui.card_section():
            with ui.grid(columns=2).classes("gap-6"):
                _info_field("Required Fixture", seq.required_fixture or "-")
                _info_field("Required Station Type", seq.required_station_type or "-")
                timeout = seq.timeout_seconds
                _info_field("Timeout", f"{timeout}s" if timeout else "-")
                args = seq.pytest_args
                _info_field("pytest Args", " ".join(args) if args else "-")

    # Required capabilities from product
    product_family = seq.product_family
    if product_family:
        product = load_product_model(product_family)
        if product:
            required_caps = get_required_capabilities(product)
            if required_caps:
                with ui.card().classes("w-full mt-4"):
                    with ui.card_section():
                        ui.label("Required Instrument Capabilities").classes("font-semibold")
                        ui.label(f"Derived from product: {product_family}").classes(
                            "text-xs text-slate-500"
                        )
                    with ui.card_section():
                        columns = [
                            {
                                "name": "char",
                                "label": "Characteristic",
                                "field": "char",
                                "align": "left",
                            },
                            {"name": "function", "label": "Function", "field": "function"},
                            {"name": "direction", "label": "Direction", "field": "direction"},
                            {"name": "signals", "label": "Signals", "field": "signals"},
                        ]
                        rows = [
                            {
                                "char": cap["characteristic"],
                                "function": cap["function"],
                                "direction": cap["direction"],
                                "signals": cap.get("signals", ""),
                            }
                            for cap in required_caps
                        ]
                        ui.table(columns=columns, rows=rows, row_key="char").classes("w-full")

            # Compatible stations
            compatible_stations = get_compatible_stations_for_product(product_family)
            with ui.row().classes("items-center gap-2 mt-6"):
                ui.icon("memory").classes("text-slate-600")
                ui.label("Compatible Stations").classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(compatible_stations)} found").props("outline")

            if compatible_stations:
                with ui.row().classes("gap-4 flex-wrap"):
                    for station in compatible_stations:
                        _station_card(sequence_id, station)
            else:
                ui.label("No compatible stations found. Check instrument capabilities.").classes(
                    "text-slate-500 italic"
                )


def _station_card(sequence_id: str, station: dict):
    """Render a station card."""
    with ui.card().classes("w-64"):
        with ui.card_section():
            ui.label(station["name"]).classes("font-semibold")
            ui.label(station["location"]).classes("text-xs text-slate-500")
        with ui.card_actions():
            ui.button(
                "Run Here",
                icon="play_arrow",
                on_click=lambda _, s=station: ui.navigate.to(
                    f"/launch?sequence={sequence_id}&station={s['id']}"
                ),
            ).props("flat dense color=primary")


def _render_dialogs_tab(dialogs: dict):
    """Render the dialogs tab."""
    if dialogs:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                {"name": "type", "label": "Type", "field": "type", "align": "left"},
                {"name": "message", "label": "Message", "field": "message", "align": "left"},
            ]
            rows = [
                {
                    "id": dialog_id,
                    "type": dialog.get("dialog_type", ""),
                    "message": (
                        dialog.get("message", "")[:60] + "..."
                        if len(dialog.get("message", "")) > 60
                        else dialog.get("message", "")
                    ),
                }
                for dialog_id, dialog in dialogs.items()
            ]
            ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
    else:
        ui.label("No dialogs defined.").classes("text-slate-500 italic")


def _render_runs_tab(sequence_id: str):
    """Render the recent runs tab."""
    backend = ParquetBackend(results_dir=load_project_config().results_dir)
    all_runs = backend.list_runs(limit=100)
    seq_runs = [r for r in all_runs if r.get("test_sequence_id") == sequence_id]

    if seq_runs:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "dut", "label": "DUT", "field": "dut", "align": "left"},
                {"name": "station", "label": "Station", "field": "station", "align": "left"},
                {"name": "started", "label": "Started", "field": "started", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
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
            table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
            table.on(
                "row-click",
                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
            )
    else:
        ui.label("No runs found for this sequence.").classes("text-slate-500 italic")


def _render_not_found():
    """Render sequence not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Sequence not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Sequences", "/sequences").classes("text-blue-600 hover:underline")
