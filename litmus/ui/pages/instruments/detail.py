"""Instrument detail page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import load_instrument_definition


@ui.page("/instruments/{instrument_type}")
def instrument_detail_page(instrument_type: str):
    """Instrument definition detail page."""
    data = load_instrument_definition(instrument_type)

    if data:
        inst = data.get("instrument", {})
        create_layout(inst.get("name", instrument_type))
    else:
        create_layout("Instrument Not Found")

    with ui.column().classes("w-full p-6 gap-6"):
        if data:
            _render_instrument_detail(instrument_type, data)
        else:
            _render_not_found()


def _render_instrument_detail(instrument_type: str, data: dict):
    """Render the instrument detail view."""
    inst = data.get("instrument", {})
    capabilities = data.get("capabilities", [])
    scpi_commands = data.get("scpi_commands", {})
    simulation = data.get("simulation", {})

    # Info card
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-4"):
                    ui.icon(inst.get("icon", "device_unknown")).classes(
                        "text-3xl text-slate-600"
                    )
                    with ui.column().classes("gap-0"):
                        ui.label(inst.get("name", instrument_type)).classes(
                            "text-xl font-semibold"
                        )
                        ui.label(inst.get("type", instrument_type)).classes(
                            "text-sm text-slate-500 font-mono"
                        )
                ui.button(
                    "Edit",
                    icon="edit",
                    on_click=lambda: ui.navigate.to(f"/instruments/{instrument_type}/edit"),
                ).props("flat")

        with ui.card_section():
            ui.label(inst.get("description", "")).classes("text-slate-600")

            if inst.get("driver_class"):
                with ui.row().classes("items-center gap-2 mt-3"):
                    ui.label("Driver:").classes("text-xs text-slate-500 uppercase")
                    ui.label(inst["driver_class"]).classes("font-mono text-sm")

    # Tabs
    with ui.tabs().classes("w-full") as tabs:
        caps_tab = ui.tab("Capabilities", icon="tune")
        scpi_tab = ui.tab("SCPI Commands", icon="terminal")
        sim_tab = ui.tab("Simulation", icon="sim_card")

    with ui.tab_panels(tabs, value=caps_tab).classes("w-full"):
        with ui.tab_panel(caps_tab):
            _render_capabilities_tab(capabilities)

        with ui.tab_panel(scpi_tab):
            _render_scpi_tab(scpi_commands)

        with ui.tab_panel(sim_tab):
            _render_simulation_tab(simulation)

    ui.link("← Back to Instruments", "/instruments").classes(
        "text-blue-600 hover:underline mt-4"
    )


def _render_capabilities_tab(capabilities: list):
    """Render the capabilities tab."""
    if capabilities:
        with ui.card().classes("w-full"):
            for cap in capabilities:
                _render_capability_card(cap)
    else:
        ui.label("No capabilities defined.").classes("text-slate-500 italic")


def _render_capability_card(cap: dict):
    """Render a capability card."""
    with ui.expansion(cap.get("name", ""), icon="tune").classes("w-full"):
        with ui.column().classes("gap-2 p-2"):
            if cap.get("description"):
                ui.label(cap["description"]).classes("text-sm text-slate-600")

            with ui.grid(columns=3).classes("gap-4"):
                _info_field("Direction", cap.get("direction", ""))
                _info_field("Domain", cap.get("domain", ""))
                signal_types = cap.get("signal_types", [])
                _info_field(
                    "Signal Types", ", ".join(signal_types) if signal_types else "-"
                )


def _render_scpi_tab(scpi_commands: dict):
    """Render the SCPI commands tab."""
    if scpi_commands:
        with ui.card().classes("w-full"):
            columns = [
                {"name": "command", "label": "Command", "field": "command", "align": "left"},
                {"name": "scpi", "label": "SCPI", "field": "scpi", "align": "left"},
            ]
            rows = [
                {"command": cmd, "scpi": scpi}
                for cmd, scpi in scpi_commands.items()
            ]
            ui.table(columns=columns, rows=rows, row_key="command").classes("w-full")
    else:
        ui.label("No SCPI commands defined.").classes("text-slate-500 italic")


def _render_simulation_tab(simulation: dict):
    """Render the simulation tab."""
    if simulation:
        with ui.card().classes("w-full"):
            with ui.card_section():
                if simulation.get("idn"):
                    _info_field("IDN Response", simulation["idn"])

                defaults = simulation.get("defaults", {})
                if defaults:
                    ui.label("Default Values").classes(
                        "text-xs text-slate-500 uppercase mt-4"
                    )
                    with ui.grid(columns=3).classes("gap-4 mt-2"):
                        for key, value in defaults.items():
                            _info_field(key, str(value))
    else:
        ui.label("No simulation configuration.").classes("text-slate-500 italic")


def _info_field(label: str, value: str):
    """Render an info field."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("font-medium font-mono")


def _render_not_found():
    """Render instrument not found message."""
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Instrument not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Instruments", "/instruments").classes(
            "text-blue-600 hover:underline"
        )
