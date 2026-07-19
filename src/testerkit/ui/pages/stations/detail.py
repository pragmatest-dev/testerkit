"""Station detail page."""

from nicegui import ui

from testerkit.data.backends.parquet import ParquetBackend
from testerkit.store import load_project_config
from testerkit.ui.shared.components import (
    data_table,
    format_datetime,
    info_field,
    setup_hash_sync_for_tabs,
)
from testerkit.ui.shared.layout import create_layout
from testerkit.ui.shared.services import (
    get_station_capabilities,
    load_station_config,
    resolve_station_instrument_records,
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
        capabilities_tab = ui.tab("Capabilities", icon="list_alt")
        runs_tab = ui.tab("Recent Runs", icon="history")

    setup_hash_sync_for_tabs(tabs, ["Instruments", "Capabilities", "Recent Runs"])

    with ui.tab_panels(tabs, value=instruments_tab).classes("w-full"):
        with ui.tab_panel(instruments_tab):
            _render_instruments_tab(station_id, instruments)

        with ui.tab_panel(capabilities_tab):
            _render_capabilities_tab(station_id, config)

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
    """Render the instruments tab as a table — one row per instrument."""
    if not instruments:
        ui.label("No instruments configured.").classes("text-slate-500 italic")
        return

    records = resolve_station_instrument_records(station_id)

    columns = [
        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
        {"name": "driver", "label": "Driver", "field": "driver", "align": "left"},
        {"name": "resource", "label": "Resource", "field": "resource", "align": "left"},
        {
            "name": "identity",
            "label": "Manufacturer / Model",
            "field": "identity",
            "align": "left",
        },
        {"name": "serial", "label": "Serial", "field": "serial", "align": "left"},
        {
            "name": "cal_due",
            "label": "Cal Due",
            "field": "cal_due",
            "align": "left",
            "sortable": True,
        },
        {"name": "status", "label": "Status", "field": "status", "align": "center"},
    ]
    rows = []
    for name, inst in instruments.items():
        record = records.get(name)
        info = record.info if record else None
        cal = record.calibration if record else None

        identity = ""
        serial = ""
        if info:
            mfr = info.manufacturer or ""
            model = info.model or ""
            identity = f"{mfr} {model}".strip()
            serial = str(info.serial or "")

        cal_due = ""
        if cal and cal.due_date:
            cal_due = (
                cal.due_date.isoformat()
                if hasattr(cal.due_date, "isoformat")
                else str(cal.due_date)
            )

        rows.append(
            {
                "name": name,
                "driver": inst.driver or "",
                "resource": inst.resource or "",
                "identity": identity,
                "serial": serial,
                "cal_due": cal_due,
                "status": "Mocked" if inst.mock else "Ready",
            }
        )
    data_table(
        columns=columns,
        rows=rows,
        row_key="name",
    )


def _render_capabilities_tab(station_id: str, config):
    """Render the capabilities tab — what this station's instruments can do.

    `station_id` is unused today; kept on the signature for symmetry with
    the sibling renderers and so a future "compatible parts/tests"
    panel can be added without a churn-y signature change.
    """
    _ = station_id
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
                data_table(columns=columns, rows=rows, row_key="capability")


def _render_runs_tab(station_id: str):
    """Render the recent runs tab."""
    backend = ParquetBackend(data_dir=load_project_config().data_dir)
    all_runs = backend.list_runs(limit=100)
    station_runs = [r for r in all_runs if r.station_id == station_id]

    if station_runs:
        columns = [
            {"name": "uut", "label": "UUT", "field": "uut", "align": "left"},
            {"name": "project", "label": "Project", "field": "project", "align": "left"},
            {"name": "started", "label": "Started", "field": "started", "align": "left"},
            {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
        ]
        rows = [
            {
                "full_run_id": r.test_run_id or "",
                "uut": r.uut_serial_number or "",
                "project": r.project_name or "",
                "started": format_datetime(r.started_at),
                "outcome": r.outcome or "",
            }
            for r in station_runs[:20]
        ]
        data_table(
            columns=columns,
            rows=rows,
            row_key="full_run_id",
            on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
            time_columns=["started"],
        )
    else:
        ui.label("No runs found on this station.").classes("text-slate-500 italic")


def _render_not_found():
    """Render station not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Station not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Stations", "/stations").classes("text-blue-600 hover:underline")
