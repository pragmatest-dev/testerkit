"""Station detail page."""

from nicegui import ui

from litmus.data.backends.parquet import ParquetBackend
from litmus.store import load_project_config
from litmus.ui.shared.components import format_datetime, info_field, setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_sequences,
    get_station_capabilities,
    load_product_model,
    load_station_config,
    resolve_station_instrument_records,
    station_compatible_with_product,
)


@ui.page("/stations/{station_id}")
def station_detail_page(station_id: str):
    """Station detail page with tabbed interface."""
    config = load_station_config(station_id)

    if config:
        create_layout(config.name or station_id)
    else:
        create_layout("Station Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if config:
            _render_station_detail(station_id, config)
        else:
            _render_not_found()


def _render_station_detail(station_id: str, config):
    """Render the station detail view."""
    instruments = config.instruments or {}
    phases = config.supported_phases or []

    # Station info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.label("Station Information").classes("text-lg font-semibold")
                    ui.badge("Online", color="green").props("outline")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/stations"),
                    ).props("flat")
                    ui.button(
                        "Edit",
                        icon="edit",
                        on_click=lambda: ui.navigate.to(f"/stations/{station_id}/edit"),
                    ).props("flat color=primary")

        with ui.card_section():
            with ui.grid(columns=3).classes("gap-6"):
                info_field("Station ID", config.id or "")
                info_field("Name", config.name or "")
                info_field("Location", config.location or "")
                with ui.column().classes("gap-1 col-span-3"):
                    ui.label("Description").classes("text-xs text-slate-500 uppercase")
                    ui.label(config.description or "").classes("font-semibold")

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

    setup_hash_sync_for_tabs(tabs, ["Instruments", "Sequences", "Recent Runs"])

    with ui.tab_panels(tabs, value=instruments_tab).classes("w-full"):
        with ui.tab_panel(instruments_tab):
            _render_instruments_tab(station_id, instruments)

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


def _render_instruments_tab(station_id: str, instruments: dict):
    """Render the instruments tab."""
    if instruments:
        # Resolve instrument records for identity/calibration info
        records = resolve_station_instrument_records(station_id)
        with ui.row().classes("gap-4 flex-wrap"):
            for name, inst in instruments.items():
                _instrument_card(name, inst, record=records.get(name))
    else:
        ui.label("No instruments configured.").classes("text-slate-500 italic")


def _instrument_card(name: str, inst, record=None):
    """Render an instrument card with optional identity/calibration from record."""
    mocked = inst.mock or False
    with ui.card().classes("w-80"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("sim_card" if mocked else "cable").classes("text-slate-600")
                    ui.label(name).classes("text-lg font-semibold")
                if mocked:
                    ui.badge("Mocked", color="blue").props("outline")
                else:
                    ui.badge("Ready", color="green").props("outline")

        with ui.card_section():
            with ui.column().classes("gap-2"):
                driver = inst.driver or ""
                with ui.row().classes("items-center gap-2"):
                    ui.label("Driver:").classes("text-sm text-slate-500")
                    ui.label(driver or "N/A").classes("text-sm font-mono")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Resource:").classes("text-sm text-slate-500")
                    ui.label(inst.resource or "N/A").classes("text-sm font-mono")

                # Identity info from resolved record
                if record and record.info:
                    ui.separator().classes("my-1")
                    mfr = record.info.manufacturer or ""
                    model = record.info.model or ""
                    if mfr or model:
                        ui.label(f"{mfr} {model}".strip()).classes(
                            "text-sm font-semibold text-slate-700"
                        )
                    if record.info.serial:
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Serial:").classes("text-xs text-slate-500")
                            ui.label(record.info.serial).classes("text-xs font-mono")
                    if record.info.firmware:
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Firmware:").classes("text-xs text-slate-500")
                            ui.label(record.info.firmware).classes("text-xs font-mono")

                # Calibration info from resolved record
                if record and record.calibration:
                    cal = record.calibration
                    if cal.due_date:
                        ui.separator().classes("my-1")
                        days = cal.days_until_due()
                        if days is not None:
                            if days < 0:
                                cal_color = "text-red-600"
                                cal_label = "OVERDUE"
                            elif days < 90:
                                cal_color = "text-amber-600"
                                cal_label = f"Due {cal.due_date.isoformat()}"
                            else:
                                cal_color = "text-green-600"
                                cal_label = f"Due {cal.due_date.isoformat()}"
                        else:
                            cal_color = "text-slate-500"
                            cal_label = str(cal.due_date)
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("event").classes(f"text-sm {cal_color}")
                            ui.label(cal_label).classes(f"text-xs {cal_color}")
                    if cal.lab:
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Cal Lab:").classes("text-xs text-slate-500")
                            ui.label(cal.lab).classes("text-xs")
                    if cal.certificate:
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Cert:").classes("text-xs text-slate-500")
                            ui.label(cal.certificate).classes("text-xs font-mono")

                if inst.description:
                    ui.label(inst.description).classes("text-sm text-slate-600 mt-2")


def _render_sequences_tab(station_id: str, config):
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
                    {"name": "function", "label": "Function", "field": "function"},
                    {"name": "direction", "label": "Direction", "field": "direction"},
                ]
                rows = [
                    {
                        "instrument": cap.instrument_name,
                        "capability": cap.name,
                        "function": cap.function,
                        "direction": cap.direction,
                    }
                    for cap in station_caps
                ]
                ui.table(columns=columns, rows=rows, row_key="capability").classes("w-full")

    # Compatible sequences
    sequences = discover_sequences()
    compatible_sequences = []
    for seq in sequences:
        product_family = seq.product_family
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


def _sequence_card(station_id: str, seq):
    """Render a sequence card."""
    with ui.card().classes("w-72"):
        with ui.card_section():
            ui.label(seq.name or seq.id).classes("font-semibold")
            if seq.test_phase:
                phase_colors = {
                    "validation": "blue",
                    "characterization": "purple",
                    "production": "green",
                }
                ui.badge(
                    seq.test_phase,
                    color=phase_colors.get(seq.test_phase, "gray"),
                ).props("outline")
            ui.label((seq.description or "")[:60]).classes("text-sm text-slate-500 mt-1")
            if seq.product_family:
                ui.label(f"Product: {seq.product_family}").classes("text-xs text-slate-400 mt-1")
        with ui.card_actions():
            ui.button(
                "Run",
                icon="play_arrow",
                on_click=lambda _, s=seq: ui.navigate.to(
                    f"/launch?sequence={s.id}&station={station_id}"
                ),
            ).props("flat dense color=primary")


def _render_runs_tab(station_id: str):
    """Render the recent runs tab."""
    backend = ParquetBackend(results_dir=load_project_config().results_dir)
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
