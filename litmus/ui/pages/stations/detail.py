"""Station detail page."""

from nicegui import ui

from litmus.data.backends.parquet import ParquetBackend
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_sequences,
    get_station_capabilities,
    load_product_model,
    load_station_config,
    station_compatible_with_product,
)


def format_datetime(dt):
    """Format datetime for display."""
    if not dt:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)[:16] if dt else ""


@ui.page("/stations/{station_id}")
def station_detail_page(station_id: str):
    """Station detail page with tabbed interface."""
    config = load_station_config(station_id)

    if config:
        station = config.get("station", {})
        create_layout(station.get("name", station_id))
    else:
        create_layout("Station Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if config:
            _render_station_detail(station_id, config)
        else:
            _render_not_found()


def _render_station_detail(station_id: str, config: dict):
    """Render the station detail view."""
    station = config.get("station", {})
    instruments = config.get("instruments", {})
    phases = config.get("supported_phases", [])

    # Station info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Station Information").classes("text-lg font-semibold")
                    ui.badge("Online", color="green").props("outline")
                ui.button(
                    "Edit",
                    icon="edit",
                    on_click=lambda: ui.navigate.to(f"/stations/{station_id}/edit"),
                ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                _info_field("Station ID", station.get("id", ""))
                _info_field("Name", station.get("name", ""))
                _info_field("Location", station.get("location", ""))
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
        with ui.tab_panel(instruments_tab):
            _render_instruments_tab(instruments)

        with ui.tab_panel(sequences_tab):
            _render_sequences_tab(station_id, config)

        with ui.tab_panel(runs_tab):
            _render_runs_tab(station_id)

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


def _info_field(label: str, value: str):
    """Render an info field."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("font-semibold")


def _render_instruments_tab(instruments: dict):
    """Render the instruments tab."""
    if instruments:
        with ui.row().classes("gap-4 flex-wrap"):
            for name, inst in instruments.items():
                _instrument_card(name, inst)
    else:
        ui.label("No instruments configured.").classes("text-slate-500 italic")


def _instrument_card(name: str, inst: dict):
    """Render an instrument card."""
    simulated = inst.get("simulated", False)
    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("sim_card" if simulated else "cable").classes("text-slate-600")
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
                    ).classes("text-sm font-medium text-blue-600 hover:underline")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Resource:").classes("text-sm text-slate-500")
                    ui.label(inst.get("resource", "N/A")).classes("text-sm font-mono")
                if inst.get("description"):
                    ui.label(inst["description"]).classes("text-sm text-slate-600 mt-2")


def _render_sequences_tab(station_id: str, config: dict):
    """Render the sequences tab with capabilities and compatible sequences."""
    # Station capabilities summary
    station_caps = get_station_capabilities(config)
    if station_caps:
        with ui.card().classes("w-full mb-4"):
            with ui.card_section():
                ui.label("Station Capabilities").classes("font-semibold")
                ui.label("What this station's instruments can measure/source").classes(
                    "text-xs text-slate-500"
                )
            with ui.card_section():
                columns = [
                    {
                        "name": "instrument",
                        "label": "Instrument",
                        "field": "instrument",
                        "align": "left",
                    },
                    {"name": "capability", "label": "Capability", "field": "capability"},
                    {"name": "direction", "label": "Direction", "field": "direction"},
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
                ui.table(columns=columns, rows=rows, row_key="capability").classes("w-full")

    # Compatible sequences
    sequences = discover_sequences()
    compatible_sequences = []
    for seq in sequences:
        product_family = seq.get("product_family")
        if product_family:
            product = load_product_model(product_family)
            if product and station_compatible_with_product(config, product):
                compatible_sequences.append(seq)
        else:
            # Sequences without product_family are always shown
            compatible_sequences.append(seq)

    with ui.row().classes("items-center gap-2 mt-4 mb-2"):
        ui.icon("list_alt").classes("text-slate-600")
        ui.label("Compatible Sequences").classes("font-semibold text-slate-700")
        ui.badge(f"{len(compatible_sequences)} found").props("outline")

    if compatible_sequences:
        with ui.row().classes("gap-4 flex-wrap"):
            for seq in compatible_sequences:
                _sequence_card(station_id, seq)
    else:
        ui.label("No compatible sequences found.").classes("text-slate-500 italic")


def _sequence_card(station_id: str, seq: dict):
    """Render a sequence card."""
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
            ui.label(seq.get("description", "")[:60]).classes("text-sm text-slate-500 mt-1")
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


def _render_runs_tab(station_id: str):
    """Render the recent runs tab."""
    backend = ParquetBackend(results_dir="results")
    all_runs = backend.list_runs(limit=100)
    station_runs = [r for r in all_runs if r.get("station_id") == station_id]

    if station_runs:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "dut", "label": "DUT", "field": "dut", "align": "left"},
                {"name": "sequence", "label": "Sequence", "field": "sequence", "align": "left"},
                {"name": "started", "label": "Started", "field": "started", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
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
            table = ui.table(columns=columns, rows=rows, row_key="run_id").classes("w-full")
            table.on(
                "row-click",
                lambda e: ui.navigate.to(f"/results/{e.args[1]['full_run_id']}"),
            )
    else:
        ui.label("No runs found on this station.").classes("text-slate-500 italic")


def _render_not_found():
    """Render station not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Station not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Stations", "/stations").classes("text-blue-600 hover:underline")
