"""System Designer page — interactive test system architect.

Assembles products (DUT pins), instruments, and fixture wiring
using an ECharts interactive graph as the design surface.
"""

from __future__ import annotations

import yaml
from nicegui import ui

from litmus.ui.pages.designer.graph import build_graph_option
from litmus.ui.pages.designer.matching import (
    auto_suggest_connections,
    get_compatible_channels_for_pin,
)
from litmus.ui.pages.designer.properties import (
    create_properties_drawer,
    show_add_instrument_dialog,
    show_connection_properties,
    show_instrument_properties,
)
from litmus.ui.pages.designer.state import DesignerState
from litmus.ui.shared.components import data_table
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_fixtures,
    discover_instrument_types,
    discover_products,
    discover_stations,
    load_fixture_config,
    load_product_model,
    load_station_config,
    save_fixture,
)


@ui.page("/designer")
def designer_page(product: str = "", station: str = "", fixture: str = ""):
    """System Designer — interactive test system architect.

    Args:
        product: Pre-fill product from URL query param
        station: Pre-fill station from URL query param
        fixture: Pre-fill fixture from URL query param
    """
    create_layout("System Designer")
    state = DesignerState()
    drawer = create_properties_drawer()

    products = discover_products()
    product_options = {p["id"]: p["name"] for p in products}

    stations = discover_stations()
    station_options = {s.id: s.name or s.id for s in stations}

    # Container references — populated inside the layout below
    containers: dict = {}

    def update_url():
        """Update URL query params to reflect current selections."""
        params = []
        if state.product_id:
            params.append(f"product={state.product_id}")
        if state.system_id:
            params.append(f"station={state.system_id}")
        if state.fixture_id:
            params.append(f"fixture={state.fixture_id}")
        query = "&".join(params)
        new_url = f"/designer{'?' + query if query else ''}"
        ui.run_javascript(f"history.replaceState(null, '', '{new_url}')")

    def rebuild():
        """Rebuild all dynamic content from current state."""
        if "chart" in containers:
            _rebuild_chart(state, containers["chart"], drawer, rebuild)
        if "connections" in containers:
            _rebuild_connections_tab(state, containers["connections"], drawer, rebuild)
        if "station_type" in containers:
            _rebuild_station_type_tab(state, containers["station_type"])
        if "yaml" in containers:
            _rebuild_yaml_tab(state, containers["yaml"])
        if "status" in containers:
            _rebuild_status(state, containers["status"], rebuild)

    def on_station_change(station_id: str) -> None:
        """Load station instruments into state."""
        if not station_id:
            return
        config = load_station_config(station_id)
        if config:
            state.load_station(config.model_dump())
            update_url()
            rebuild()

    # Load from URL params or use defaults
    initial_station = (
        station if station in station_options else (stations[0].id if stations else None)
    )
    if initial_station:
        config = load_station_config(initial_station)
        if config:
            state.load_station(config.model_dump())

    # Load product from URL if provided
    if product and product in product_options:
        _on_product_change(product, state, lambda: None)  # Don't rebuild yet

    # Load fixture from URL if provided
    if fixture:
        fixture_config = load_fixture_config(fixture)
        if fixture_config:
            state.load_fixture(fixture_config)

    def on_product_change(product_id: str) -> None:
        """Handle product selection change."""
        _on_product_change(product_id, state, rebuild)
        update_url()

    with ui.column().classes("w-full p-6 gap-4"):
        # --- Selection bar ---
        with ui.row().classes("w-full items-end gap-3 flex-wrap justify-between"):
            with ui.row().classes("items-end gap-3 flex-wrap"):
                ui.select(
                    product_options,
                    label="Product",
                    value=product if product in product_options else None,
                    with_input=True,
                    on_change=lambda e: on_product_change(e.value),
                ).classes("w-48")

                ui.select(
                    station_options,
                    label="Station",
                    value=initial_station,
                    with_input=True,
                    on_change=lambda e: on_station_change(e.value),
                ).classes("w-48")

                ui.button(
                    "Load Fixture",
                    icon="upload",
                    on_click=lambda: _show_load_fixture_dialog(state, rebuild, update_url),
                ).props("flat")

                ui.button(
                    "Add Instrument",
                    icon="add",
                    on_click=lambda: show_add_instrument_dialog(
                        state, rebuild, discover_instrument_types()
                    ),
                ).props("flat")

        # --- IDs ---
        with ui.row().classes("w-full items-end gap-3"):
            system_id_input = ui.input("System ID", value=state.system_id).classes("w-48")
            system_id_input.bind_value(state, "system_id")

            fixture_id_input = ui.input("Fixture ID", value=state.fixture_id).classes("w-48")
            fixture_id_input.bind_value(state, "fixture_id")

        # --- Status bar ---
        containers["status"] = ui.row().classes(
            "w-full items-center gap-4 bg-white rounded-lg px-4 py-2 shadow-sm"
        )

        # --- Graph card ---
        with ui.card().classes("w-full"):
            ui.label("Design Surface").classes("text-xs text-slate-500 uppercase tracking-wide")
            ui.label(
                "Click a pin to select it, then click a channel to wire. "
                "Click a wire to disconnect."
            ).classes("text-xs text-slate-400 mb-2")
            containers["chart"] = ui.column().classes("w-full")

        # --- Detail tabs ---
        with ui.card().classes("w-full"):
            with ui.tabs().classes("w-full") as tabs:
                conn_tab = ui.tab("Connections", icon="cable")
                st_tab = ui.tab("Station Type", icon="dns")
                yaml_tab = ui.tab("YAML Preview", icon="code")

            with ui.tab_panels(tabs, value=conn_tab).classes("w-full"):
                with ui.tab_panel(conn_tab):
                    containers["connections"] = ui.column().classes("w-full")
                with ui.tab_panel(st_tab):
                    containers["station_type"] = ui.column().classes("w-full")
                with ui.tab_panel(yaml_tab):
                    containers["yaml"] = ui.column().classes("w-full")

    # Initial rebuild
    rebuild()


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------


def _rebuild_status(state: DesignerState, container, rebuild) -> None:
    """Rebuild the status bar content."""
    container.clear()
    with container:
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle").classes("text-green-500 text-sm")
            ui.label(f"{state.wired_pin_count}/{state.total_pin_count} pins wired").classes(
                "text-sm text-slate-600"
            )
        with ui.row().classes("items-center gap-2"):
            ui.icon("radio_button_unchecked").classes("text-slate-400 text-sm")
            ui.label(f"{state.available_pin_count} available").classes("text-sm text-slate-400")
        with ui.row().classes("items-center gap-2"):
            ui.icon("precision_manufacturing").classes("text-purple-500 text-sm")
            ui.label(f"{len(state.instruments)} instruments").classes("text-sm text-slate-600")

        if state.selected_pin:
            with ui.row().classes("items-center gap-2 bg-blue-50 rounded px-3 py-1"):
                ui.icon("radio_button_checked").classes("text-blue-500 text-sm")
                ui.label(f"Wiring: {state.selected_pin}").classes(
                    "text-sm text-blue-700 font-medium"
                )
                ui.label("Click a channel to connect").classes("text-xs text-blue-400")

        ui.space()
        ui.checkbox("Hide Unused", value=state.hide_disconnected).bind_value(
            state, "hide_disconnected"
        ).on_value_change(lambda: rebuild()).classes("text-sm")
        ui.button(
            "Auto-Match",
            icon="auto_fix_high",
            on_click=lambda: _auto_match(state, rebuild),
        ).props("flat dense")
        ui.button(
            "Clear All",
            icon="clear_all",
            on_click=lambda: _clear_all(state, rebuild),
        ).props("flat dense")


# ---------------------------------------------------------------------------
# Chart and click handling
# ---------------------------------------------------------------------------


def _rebuild_chart(
    state: DesignerState,
    container: ui.column,
    drawer: ui.right_drawer,
    rebuild,
) -> None:
    """Rebuild the ECharts graph."""
    container.clear()
    if not state.dut_pins and not state.instruments:
        with container:
            with ui.column().classes("w-full items-center py-12"):
                ui.icon("design_services").classes("text-6xl text-slate-300")
                ui.label("Select a product and load a station to begin").classes("text-slate-400")
        return

    option = build_graph_option(state)
    chart_height = option.pop("_chartHeight", 400)
    with container:
        chart = ui.echart(option).classes("w-full").style(f"height: {chart_height}px")
        # Register on 'componentClick' directly instead of on_point_click
        # because NiceGUI's on_point_click assumes e.args['value'] exists,
        # which is not the case for graph series node/edge clicks.
        chart.on(
            "componentClick",
            lambda e: _handle_chart_click(e, state, drawer, rebuild),
            [
                "componentType",
                "seriesType",
                "dataType",
                "name",
                "dataIndex",
                "data",
            ],
        )
        # Connection highlighting: use JavaScript to highlight all segments on hover
        # Highlighting for connection hover (highlight all segments sharing point_name)
        ui.run_javascript(f"""
            const el = getElement({chart.id});
            if (el && el.chart) {{
                el.chart.on('mouseover', (params) => {{
                    if (params.dataType !== 'edge') return;
                    const pointName = params.data?.point_name;
                    if (!pointName) return;
                    const links = el.chart.getOption().series[0].links;
                    const indices = links
                        .map((l, i) => l.point_name === pointName ? i : -1)
                        .filter(i => i >= 0);
                    el.chart.dispatchAction({{
                        type: 'highlight',
                        seriesIndex: 0,
                        dataType: 'edge',
                        dataIndex: indices
                    }});
                }});
                el.chart.on('mouseout', (params) => {{
                    if (params.dataType !== 'edge') return;
                    el.chart.dispatchAction({{ type: 'downplay', seriesIndex: 0 }});
                }});
            }}
        """)


def _handle_chart_click(event, state, drawer, rebuild) -> None:
    """Handle click events on the ECharts graph.

    ``event`` is a ``GenericEventArguments`` from ``chart.on('componentClick')``.
    ``event.args`` is a dict with keys: componentType, seriesType, dataType, name,
    dataIndex, data.
    """
    args = event.args

    if args.get("componentType") != "series":
        return
    data_type = args.get("dataType")
    data = args.get("data", {})

    if data_type == "node":
        side = data.get("side")
        node_type = data.get("node_type")

        if not data.get("interactive", True):
            return

        if side == "product" and node_type == "pin":
            pin_key = data.get("pin_key", data.get("name", ""))
            if state.selected_pin == pin_key:
                # Same pin clicked again — deselect (exit wiring mode)
                state.clear_selection()
                drawer.value = False
            else:
                # Select pin — enter/switch wiring mode (no drawer)
                state.select_pin(pin_key)
                state.compatible_channels = get_compatible_channels_for_pin(
                    pin_key, state.char_by_pin, state.product, state.instruments
                )
                drawer.value = False

        elif side == "instrument" and node_type in ("channel", "terminal"):
            role = data.get("role", "")
            channel = data.get("channel", "")
            terminal = data.get("terminal")  # Only set for terminal nodes

            if state.selected_pin:
                # Wiring mode: create or toggle connection
                channel_key = f"{role}:{channel}"
                existing = state.find_connection_by_link(state.selected_pin, channel_key)
                if existing:
                    state.remove_connection(existing)
                else:
                    # Create connection (allow any wiring for now)
                    pin = state.selected_pin
                    net = state.dut_pins.get(pin, {}).get("net", "")
                    term_suffix = f"_{terminal}" if terminal else ""
                    point_name = f"{pin.lower()}_{role}_ch{channel}{term_suffix}"
                    state.add_connection(point_name, pin, role, channel, net, terminal)
                state.clear_selection()
                drawer.value = False
                _auto_save(state)  # Auto-save on connection change
            else:
                # Not wiring — show instrument properties
                show_instrument_properties(role, state, drawer, rebuild)

        elif side == "instrument" and node_type == "header":
            role = data.get("role", data.get("name", ""))
            if role.startswith("__header_inst_"):
                role = role[len("__header_inst_") :]
            state.clear_selection()
            show_instrument_properties(role, state, drawer, rebuild)

    elif data_type == "edge":
        point_name = data.get("point_name")
        if point_name and point_name in state.connections:
            state.clear_selection()
            show_connection_properties(point_name, state, drawer, rebuild)

    rebuild()


# ---------------------------------------------------------------------------
# Tab content builders
# ---------------------------------------------------------------------------


def _rebuild_connections_tab(state, container, drawer, rebuild) -> None:
    """Rebuild the connections table."""
    container.clear()
    with container:
        if not state.connections:
            ui.label("No connections yet. Wire pins to instrument channels above.").classes(
                "text-slate-400 text-sm py-4"
            )
            return

        columns = [
            {"name": "point", "label": "Point", "field": "point", "align": "left"},
            {"name": "pin", "label": "DUT Pin", "field": "pin", "align": "left"},
            {"name": "net", "label": "Net", "field": "net", "align": "left"},
            {
                "name": "instrument",
                "label": "Instrument",
                "field": "instrument",
                "align": "left",
            },
            {
                "name": "channel",
                "label": "Channel",
                "field": "channel",
                "align": "left",
            },
        ]
        rows = []
        for point_name, conn in state.connections.items():
            rows.append(
                {
                    "point": point_name,
                    "pin": conn.get("dut_pin", ""),
                    "net": conn.get("net", ""),
                    "instrument": conn.get("instrument", ""),
                    "channel": conn.get("channel", ""),
                }
            )

        data_table(
            columns=columns,
            rows=rows,
            row_key="point",
            on_row_click=lambda r: show_connection_properties(r["point"], state, drawer, rebuild),
        )


def _rebuild_station_type_tab(state, container) -> None:
    """Rebuild the station type preview."""
    container.clear()
    with container:
        if not state.instruments:
            ui.label("No instruments added yet.").classes("text-slate-400 text-sm py-4")
            return

        data = state.to_station_type_yaml()
        yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
        ui.code(yaml_str, language="yaml").classes("w-full")


def _rebuild_yaml_tab(state, container) -> None:
    """Rebuild the fixture YAML preview."""
    container.clear()
    with container:
        if not state.connections:
            ui.label("No connections to preview.").classes("text-slate-400 text-sm py-4")
            return

        data = state.to_fixture_yaml()
        yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
        ui.code(yaml_str, language="yaml").classes("w-full")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _on_product_change(product_id, state, rebuild) -> None:
    """Handle product selection change."""
    if not product_id:
        return
    product = load_product_model(product_id)
    if product:
        state.load_product(product)

        # Try to load existing fixture with auto-generated ID
        if state.fixture_id:
            fixture = load_fixture_config(state.fixture_id)
            if fixture:
                state.load_fixture(fixture)
                ui.notify(
                    f"Loaded {product.name} + fixture ({len(state.connections)} connections)",
                    type="info",
                )
                rebuild()
                return

        rebuild()
        ui.notify(f"Loaded {product.name} ({len(state.dut_pins)} pins)", type="info")


def _auto_match(state, rebuild) -> None:
    """Auto-suggest connections for unconnected pins."""
    suggestions = auto_suggest_connections(
        state.dut_pins,
        state.char_by_pin,
        state.product,
        state.instruments,
        state.connections,
    )
    if not suggestions:
        ui.notify("No automatic matches found", type="info")
        return

    for s in suggestions:
        state.add_connection(
            s["point_name"],
            s["dut_pin"],
            s["instrument"],
            s["channel"],
            s.get("net"),
            s.get("terminal"),
        )
    _auto_save(state)  # Auto-save after auto-match
    rebuild()
    ui.notify(f"Auto-matched {len(suggestions)} connections", type="positive")


def _clear_all(state, rebuild) -> None:
    """Clear all connections."""
    state.clear_all_connections()
    state.clear_selection()
    _auto_save(state)  # Auto-save cleared state
    rebuild()
    ui.notify("All connections cleared", type="info")


def _auto_save(state, quiet: bool = True) -> bool:
    """Auto-save if fixture_id and system_id are set.

    Returns True if save was attempted, False if IDs not set.
    """
    if not state.fixture_id or not state.system_id:
        return False

    if not state.connections:
        return False

    fixture_data = state.to_fixture_yaml()
    try:
        save_fixture(state.fixture_id, fixture_data["fixture"], fixture_data["connections"])
        if not quiet:
            ui.notify("Saved", type="positive")
        return True
    except (OSError, ValueError, KeyError) as e:
        ui.notify(f"Auto-save failed: {e}", type="negative")
        return False


def _show_load_fixture_dialog(state, rebuild, on_load=None) -> None:
    """Show dialog to load an existing fixture."""
    fixtures = discover_fixtures()

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Load Fixture").classes("text-lg font-semibold")
        ui.separator()

        if not fixtures:
            ui.label("No fixtures found.").classes("text-slate-500")
            ui.button("Close", on_click=dialog.close).props("flat")
        else:
            fixture_options = {f.id: f.name or f.id for f in fixtures}
            selected: dict = {"fixture_id": ""}

            ui.select(fixture_options, label="Fixture", with_input=True).classes(
                "w-full"
            ).bind_value(selected, "fixture_id")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")

                def _load():
                    if selected["fixture_id"]:
                        config = load_fixture_config(selected["fixture_id"])
                        if config:
                            state.load_fixture(config)
                            dialog.close()
                            rebuild()
                            if on_load:
                                on_load()
                            ui.notify(
                                f"Loaded fixture {selected['fixture_id']}",
                                type="positive",
                            )

                ui.button("Load", on_click=_load, color="primary")

    dialog.open()
