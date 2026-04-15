"""Properties drawer panels for the system designer.

Provides contextual editing panels for pins, instruments, and connections
in a right-hand slide-in drawer. Also contains Add Pin and Add Instrument dialogs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from litmus.models.station import StationConfig
    from litmus.ui.pages.designer.state import DesignerState


def create_properties_drawer() -> ui.right_drawer:
    """Create the right-hand properties drawer (initially hidden)."""
    drawer = ui.right_drawer(value=False).classes(
        "bg-white border-l border-slate-200"
    )
    drawer.props("width=320 bordered overlay")
    return drawer


def show_pin_properties(
    pin_key: str,
    state: DesignerState,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Render pin editing form in the drawer."""
    pin = state.dut_pins.get(pin_key)
    if not pin:
        return

    drawer.clear()
    with drawer:
        with ui.column().classes("w-full p-4 gap-3"):
            # Header
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Pin Properties").classes(
                    "text-lg font-semibold text-slate-800"
                )
                _close_button(state, drawer, rebuild)

            ui.label(pin_key).classes("text-sm font-mono text-slate-500 -mt-2")
            ui.separator()

            # Editable fields
            ui.input(
                "Name",
                value=pin.get("name", ""),
                on_change=lambda e, k=pin_key: _update_pin_field(
                    state, k, "name", e.value, rebuild
                ),
            ).classes("w-full")

            ui.input(
                "Net",
                value=pin.get("net", ""),
                on_change=lambda e, k=pin_key: _update_pin_field(
                    state, k, "net", e.value, rebuild
                ),
            ).classes("w-full")

            ui.input(
                "Description",
                value=pin.get("description", ""),
                on_change=lambda e, k=pin_key: _update_pin_field(
                    state, k, "description", e.value, rebuild
                ),
            ).classes("w-full")

            # Characteristics section (read-only)
            chars = state.char_by_pin.get(pin_key, [])
            if chars:
                ui.separator()
                ui.label("Characteristics").classes(
                    "text-sm font-semibold text-slate-600"
                )
                for char_name in chars:
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("tune").classes("text-slate-400 text-sm")
                        ui.label(char_name).classes("text-sm text-slate-600")

            # Connection info (read-only)
            conns = state.find_connections_for_pin(pin_key)
            if conns:
                ui.separator()
                ui.label("Connected to").classes(
                    "text-sm font-semibold text-slate-600"
                )
                for conn in conns:
                    conn_text = f"{conn['instrument']}:{conn['channel']}"
                    if conn.get("terminal"):
                        conn_text += f":{conn['terminal']}"
                    ui.label(conn_text).classes("text-sm font-mono text-green-600")

            # Delete button
            ui.separator()
            ui.button(
                "Delete Pin",
                icon="delete",
                on_click=lambda: _delete_pin(state, pin_key, drawer, rebuild),
                color="red",
            ).props("flat").classes("w-full")

    drawer.value = True


def show_instrument_properties(
    role: str,
    state: DesignerState,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Render instrument editing form in the drawer."""
    inst = state.instruments.get(role)
    if not inst:
        return

    drawer.clear()
    with drawer:
        with ui.column().classes("w-full p-4 gap-3"):
            # Header
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Instrument").classes(
                    "text-lg font-semibold text-slate-800"
                )
                _close_button(state, drawer, rebuild)

            ui.separator()

            # Role name (readonly)
            ui.input("Role", value=role).classes("w-full").props("readonly")

            # Driver
            ui.input(
                "Driver",
                value=inst.get("driver", ""),
                on_change=lambda e: _update_instrument_field(
                    state, role, "driver", e.value
                ),
            ).classes("w-full")

            # Type
            ui.input(
                "Type",
                value=inst.get("type", ""),
                on_change=lambda e: _update_instrument_field(
                    state, role, "type", e.value
                ),
            ).classes("w-full")

            # Channels section with catalog details
            ui.separator()
            ui.label("Channels").classes("text-sm font-semibold text-slate-600")

            channel_details = inst.get("channel_details", {})
            channels_container = ui.column().classes("w-full gap-2")

            def _rebuild_channels():
                channels_container.clear()
                with channels_container:
                    for ch in inst.get("channels", []):
                        ch_detail = channel_details.get(ch, {})
                        ch_label = ch_detail.get("label", ch)
                        if ch.isdigit() and not ch_detail.get("label"):
                            ch_label = f"CH{ch}"
                        is_used = state.is_channel_used(role, ch)
                        ground = ch_detail.get("ground", "unknown")
                        terminals = ch_detail.get("terminals", [])
                        connector = ch_detail.get("connector", "")

                        with ui.card().classes("w-full p-2").props("flat bordered"):
                            with ui.row().classes("items-center justify-between w-full"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(ch_label).classes(
                                        "text-sm font-mono font-semibold "
                                        + ("text-green-600" if is_used else "text-slate-700")
                                    )
                                    if is_used:
                                        ui.icon("link").classes("text-green-500 text-sm")
                                # Ground indicator - only if there's a wirable ground terminal
                                gnd_names = {
                                    "lo", "gnd", "ground", "return", "com", "sense_lo", "shield"
                                }
                                has_gnd = any(t.lower() in gnd_names for t in terminals)
                                if has_gnd:
                                    if ground == "shared":
                                        ui.badge("⏚ shared", color="green").props("outline dense")
                                    elif ground == "floating":
                                        ui.badge("⏊ floating", color="amber").props("outline dense")

                            # Catalog details (read-only)
                            if terminals or connector:
                                with ui.row().classes("gap-3 mt-1"):
                                    if terminals:
                                        ui.label(f"Terminals: {', '.join(terminals)}").classes(
                                            "text-xs text-slate-500"
                                        )
                                    if connector:
                                        ui.label(f"Connector: {connector}").classes(
                                            "text-xs text-slate-500"
                                        )

            _rebuild_channels()

            # Catalog reference
            catalog_ref = inst.get("catalog_ref")
            if catalog_ref:
                ui.separator()
                with ui.row().classes("items-center gap-2"):
                    ui.icon("inventory_2").classes("text-slate-400 text-sm")
                    ui.label(f"Catalog: {catalog_ref}").classes(
                        "text-xs text-slate-500 font-mono"
                    )

            # Capabilities (read-only)
            caps = inst.get("capabilities", [])
            if caps:
                ui.separator()
                ui.label("Capabilities").classes(
                    "text-sm font-semibold text-slate-600"
                )
                for cap in caps:
                    name = cap.get(
                        "name",
                        f"{cap.get('direction', '')} {cap.get('function', '')}",
                    )
                    ui.label(name).classes("text-sm text-slate-500")
            else:
                ui.separator()
                ui.label("No capability data (library)").classes(
                    "text-xs text-slate-400 italic"
                )

            # Connected pins
            connected = [
                conn
                for conn in state.connections.values()
                if conn["instrument"] == role
            ]
            if connected:
                ui.separator()
                ui.label("Connected Pins").classes(
                    "text-sm font-semibold text-slate-600"
                )
                for conn in connected:
                    target = f"CH{conn['channel']}"
                    if conn.get("terminal"):
                        target += f":{conn['terminal'].upper()}"
                    ui.label(f"{conn['dut_pin']} -> {target}").classes(
                        "text-sm text-slate-500 font-mono"
                    )

            # Delete button
            ui.separator()
            ui.button(
                "Delete Instrument",
                icon="delete",
                on_click=lambda: _delete_instrument(
                    state, role, drawer, rebuild
                ),
                color="red",
            ).props("flat").classes("w-full")

    drawer.value = True


def show_connection_properties(
    point_name: str,
    state: DesignerState,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Render connection details in the drawer."""
    conn = state.connections.get(point_name)
    if not conn:
        return

    drawer.clear()
    with drawer:
        with ui.column().classes("w-full p-4 gap-3"):
            # Header
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Connection").classes(
                    "text-lg font-semibold text-slate-800"
                )
                _close_button(state, drawer, rebuild)

            ui.separator()

            # Point name (readonly)
            ui.input("Point Name", value=point_name).classes("w-full").props(
                "readonly"
            )

            # Read-only fields
            ui.input("DUT Pin", value=conn.get("dut_pin", "")).classes(
                "w-full"
            ).props("readonly")
            ui.input("Net", value=conn.get("net", "")).classes("w-full").props(
                "readonly"
            )
            ui.input("Instrument", value=conn.get("instrument", "")).classes(
                "w-full"
            ).props("readonly")
            ui.input("Channel", value=conn.get("channel", "")).classes(
                "w-full"
            ).props("readonly")
            if conn.get("terminal"):
                ui.input("Terminal", value=conn.get("terminal", "")).classes(
                    "w-full"
                ).props("readonly")

            # Delete button
            ui.separator()
            ui.button(
                "Delete Connection",
                icon="link_off",
                on_click=lambda: _delete_connection(
                    state, point_name, drawer, rebuild
                ),
                color="red",
            ).props("flat").classes("w-full")

    drawer.value = True


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


def show_add_pin_dialog(state: DesignerState, rebuild: Callable) -> None:
    """Show dialog to add a new DUT pin."""
    form: dict = {"key": "", "name": "", "net": ""}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Add DUT Pin").classes("text-lg font-semibold")
        ui.separator()

        ui.input(
            "Pin Key", placeholder="e.g. TP_VOUT"
        ).classes("w-full").bind_value(form, "key")

        ui.input(
            "Pin Name", placeholder="e.g. TP5"
        ).classes("w-full").bind_value(form, "name")

        ui.input(
            "Net", placeholder="e.g. VOUT_3V3"
        ).classes("w-full").bind_value(form, "net")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _add():
                if not form["key"]:
                    ui.notify("Pin key is required", type="warning")
                elif form["key"] in state.dut_pins:
                    ui.notify("Pin key already exists", type="warning")
                else:
                    state.add_pin(
                        form["key"], form["name"], form["net"]
                    )
                    dialog.close()
                    rebuild()

            ui.button("Add", on_click=_add, color="primary")

    dialog.open()


def show_add_instrument_dialog(
    state: DesignerState,
    rebuild: Callable,
    instrument_types: list[dict] | None = None,
) -> None:
    """Show dialog to add an instrument."""
    form: dict = {"role": "", "driver": "", "channels": "1", "type": ""}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Add Instrument").classes("text-lg font-semibold")
        ui.separator()

        # If library types available, show dropdown
        if instrument_types:
            type_options = {t["type"]: t["name"] for t in instrument_types}

            def _on_type_select(e):
                selected = e.value
                for t in instrument_types:
                    if t["type"] == selected:
                        form["type"] = t["type"]
                        form["driver"] = ""
                        caps = t.get("capability_details", [])
                        ch_names = set()
                        for cap in caps:
                            channels_spec = cap.get("channels", {})
                            if isinstance(channels_spec, dict):
                                count = channels_spec.get("count", 1)
                                naming = channels_spec.get("naming")
                                if naming:
                                    ch_names.update(
                                        naming.format(n=i + 1)
                                        for i in range(count)
                                    )
                                else:
                                    ch_names.update(
                                        str(i + 1) for i in range(count)
                                    )
                        if ch_names:
                            form["channels"] = ", ".join(sorted(ch_names))
                        break

            ui.select(
                type_options,
                label="Instrument Type (optional)",
                on_change=_on_type_select,
            ).classes("w-full")

        ui.input(
            "Role Name", placeholder='e.g. "dmm", "psu"'
        ).classes("w-full").bind_value(form, "role")

        ui.input(
            "Driver", placeholder="e.g. demo.drivers.DMM"
        ).classes("w-full").bind_value(form, "driver")

        ui.input(
            "Channels", placeholder="e.g. 1, 2 or CH1, CH2"
        ).classes("w-full").bind_value(form, "channels")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _add():
                if not form["role"]:
                    ui.notify("Role name is required", type="warning")
                elif form["role"] in state.instruments:
                    ui.notify("Role name already exists", type="warning")
                else:
                    channels = [
                        c.strip()
                        for c in form["channels"].split(",")
                        if c.strip()
                    ]
                    if not channels:
                        channels = ["1"]

                    caps: list[dict] = []
                    if instrument_types and form.get("type"):
                        for t in instrument_types:
                            if t["type"] == form["type"]:
                                caps = t.get("capability_details", [])
                                break

                    state.add_instrument(
                        form["role"],
                        form.get("type", ""),
                        form["driver"],
                        caps,
                        channels,
                    )
                    dialog.close()
                    rebuild()

            ui.button("Add", on_click=_add, color="primary")

    dialog.open()


def show_load_station_dialog(
    state: DesignerState,
    stations: list[StationConfig],
    load_station_fn: Callable,
    rebuild: Callable,
) -> None:
    """Show dialog to load instruments from an existing station."""
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Load Station").classes("text-lg font-semibold")
        ui.separator()

        if not stations:
            ui.label("No stations found.").classes("text-slate-500")
            ui.button("Close", on_click=dialog.close).props("flat")
        else:
            station_options = {s.id: s.name or s.id for s in stations}
            selected = {"station_id": ""}

            ui.select(
                station_options, label="Station", with_input=True
            ).classes("w-full").bind_value(selected, "station_id")

            ui.label(
                "This will import all instruments from the selected station."
            ).classes("text-xs text-slate-500")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")

                def _load():
                    if selected["station_id"]:
                        config = load_station_fn(selected["station_id"])
                        if config:
                            from litmus.ui.pages.designer.matching import (
                                resolve_instrument_capabilities,
                            )

                            config = resolve_instrument_capabilities(config)
                            state.load_station(config)
                            dialog.close()
                            rebuild()
                            ui.notify(
                                f"Loaded station {selected['station_id']}",
                                type="positive",
                            )
                        else:
                            ui.notify(
                                "Failed to load station", type="negative"
                            )

                ui.button("Load", on_click=_load, color="primary")

    dialog.open()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _close_button(
    state: DesignerState, drawer: ui.right_drawer, rebuild: Callable
) -> None:
    """Render a close button for the properties drawer."""

    def on_close():
        state.clear_selection()
        drawer.value = False
        rebuild()

    ui.button(icon="close", on_click=on_close).props(
        "flat dense round"
    ).classes("text-slate-400")


def _update_pin_field(
    state: DesignerState,
    pin_key: str,
    field: str,
    value: str,
    rebuild: Callable,
) -> None:
    """Update a single pin field."""
    state.edit_pin(pin_key, **{field: value})


def _update_instrument_field(
    state: DesignerState, role: str, field: str, value: str
) -> None:
    """Update a single instrument field."""
    if role in state.instruments:
        state.instruments[role][field] = value


def _delete_pin(
    state: DesignerState,
    pin_key: str,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Delete a pin and close drawer."""
    state.remove_pin(pin_key)
    state.clear_selection()
    drawer.value = False
    rebuild()


def _delete_instrument(
    state: DesignerState,
    role: str,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Delete an instrument and close drawer."""
    state.remove_instrument(role)
    state.clear_selection()
    drawer.value = False
    rebuild()


def _delete_connection(
    state: DesignerState,
    point_name: str,
    drawer: ui.right_drawer,
    rebuild: Callable,
) -> None:
    """Delete a connection and close drawer."""
    state.remove_connection(point_name)
    state.clear_selection()
    drawer.value = False
    rebuild()


def _remove_channel(
    state: DesignerState,
    role: str,
    channel: str,
    rebuild: Callable,
    rebuild_channels: Callable,
) -> None:
    """Remove a channel from an instrument."""
    inst = state.instruments.get(role)
    if inst and channel in inst.get("channels", []):
        to_remove = [
            name
            for name, conn in state.connections.items()
            if conn["instrument"] == role and conn["channel"] == channel
        ]
        for name in to_remove:
            del state.connections[name]
        inst["channels"].remove(channel)
        rebuild_channels()
        rebuild()


def _add_channel(
    state: DesignerState,
    role: str,
    input_elem: ui.input,
    rebuild: Callable,
    rebuild_channels: Callable,
) -> None:
    """Add a channel to an instrument."""
    ch = input_elem.value.strip()
    if not ch:
        return
    inst = state.instruments.get(role)
    if inst:
        channels = inst.get("channels", [])
        if ch not in channels:
            channels.append(ch)
            inst["channels"] = channels
            input_elem.value = ""
            rebuild_channels()
            rebuild()
