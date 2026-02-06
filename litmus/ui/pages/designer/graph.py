"""ECharts graph builder for the system designer.

Transforms DesignerState into an ECharts graph series option with:
- Product pins as nodes on the left
- Instrument channels as nodes on the right
- Connections as edges between them
- Color coding by capability and selection state
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.ui.pages.designer.state import DesignerState

# Layout constants
LEFT_X = 80
RIGHT_X = 620
GROUP_GAP = 50
NODE_GAP = 35
HEADER_SIZE = 20
NODE_SIZE = 16

# Category indices — visual roles, not pin types.
# Pin behaviour is described by capabilities, not categories.
CAT_PIN = 0  # Default DUT pin (has characteristics)
CAT_PIN_REF = 1  # Reference pin (no characteristics, e.g. ground)
CAT_UNUSED = 2  # Reserved
CAT_INSTRUMENT = 3
CAT_HEADER = 4
CAT_SELECTED = 5
CAT_COMPATIBLE = 6
CAT_CONNECTED = 7


def build_graph_option(state: DesignerState) -> dict:
    """Build complete ECharts option from designer state."""
    nodes = build_nodes(state)
    links = build_links(state)
    categories = build_categories()

    # Calculate chart height based on node count
    pin_count = len(state.dut_pins)
    channel_count = sum(len(inst.get("channels", ["1"])) for inst in state.instruments.values())
    # Account for group headers
    pin_groups = len(_group_pins_by_connector(state.dut_pins))
    inst_groups = len(state.instruments)
    left_height = pin_count * NODE_GAP + pin_groups * GROUP_GAP + 60
    right_height = channel_count * NODE_GAP + inst_groups * GROUP_GAP + 60
    chart_height = max(left_height, right_height, 300)

    return {
        "tooltip": {"show": False},
        "animationDuration": 300,
        "grid": {"left": 0, "right": 0, "top": 0, "bottom": 0},
        "series": [
            {
                "type": "graph",
                "layout": "none",
                "roam": True,
                "zoom": 1,
                "scaleLimit": {"min": 0.5, "max": 3},
                "categories": categories,
                "data": nodes,
                "links": links,
                "lineStyle": {
                    "color": "#94a3b8",
                    "width": 2,
                    "curveness": 0.15,
                },
                "emphasis": {
                    "focus": "adjacency",
                    "lineStyle": {"width": 3},
                },
                "label": {
                    "show": True,
                    "fontSize": 12,
                    "color": "#334155",
                },
            }
        ],
        "_chartHeight": chart_height,
    }


def build_nodes(state: DesignerState) -> list[dict]:
    """Build graph nodes for product pins and instrument channels."""
    nodes: list[dict] = []
    y = 20

    # --- Product side (left) ---
    groups = _group_pins_by_connector(state.dut_pins)
    for group_type, pin_keys in groups.items():
        # Group header
        nodes.append(
            {
                "name": f"__header_product_{group_type}",
                "x": LEFT_X,
                "y": y,
                "category": CAT_HEADER,
                "symbolSize": HEADER_SIZE,
                "symbol": "rect",
                "label": {
                    "show": True,
                    "formatter": group_type.upper(),
                    "fontSize": 11,
                    "fontWeight": "bold",
                    "color": "#64748b",
                },
                "itemStyle": {"color": "transparent", "borderWidth": 0},
                "side": "product",
                "node_type": "header",
                "interactive": False,
            }
        )
        y += NODE_GAP

        for pin_key in pin_keys:
            pin = state.dut_pins[pin_key]
            is_selected = state.selected_pin == pin_key
            is_connected = state.is_pin_connected(pin_key)

            # Determine category
            if is_selected:
                cat = CAT_SELECTED
            elif is_connected:
                cat = CAT_CONNECTED
            else:
                cat = _pin_category(pin_key, state)

            # Node symbol: filled if connected, empty if not
            symbol = "circle"
            symbol_size = NODE_SIZE + (4 if is_selected else 0)

            label_text = pin_key
            if pin.get("net"):
                label_text = f"{pin_key} ({pin['net']})"

            node: dict = {
                "name": pin_key,
                "x": LEFT_X,
                "y": y,
                "category": cat,
                "symbolSize": symbol_size,
                "symbol": symbol,
                "label": {
                    "show": True,
                    "position": "left",
                    "formatter": label_text,
                    "fontSize": 12,
                    "color": "#1e293b" if is_selected else "#334155",
                    "fontWeight": "bold" if is_selected else "normal",
                },
                "side": "product",
                "node_type": "pin",
                "interactive": True,
                "pin_key": pin_key,
            }

            if is_selected:
                node["itemStyle"] = {
                    "borderColor": "#3b82f6",
                    "borderWidth": 3,
                    "shadowColor": "rgba(59,130,246,0.4)",
                    "shadowBlur": 10,
                }

            if is_connected and not is_selected:
                node["itemStyle"] = {
                    "color": "#22c55e",
                    "borderColor": "#16a34a",
                    "borderWidth": 2,
                }

            nodes.append(node)
            y += NODE_GAP

        y += GROUP_GAP - NODE_GAP  # Extra space between groups

    # --- Instrument side (right) ---
    y = 20
    for role, inst in state.instruments.items():
        # Role header
        type_label = inst.get("type", "")
        header_text = f"{role} ({type_label})" if type_label else role
        nodes.append(
            {
                "name": f"__header_inst_{role}",
                "x": RIGHT_X,
                "y": y,
                "category": CAT_HEADER,
                "symbolSize": HEADER_SIZE,
                "symbol": "rect",
                "label": {
                    "show": True,
                    "formatter": header_text,
                    "fontSize": 11,
                    "fontWeight": "bold",
                    "color": "#7c3aed",
                    "position": "right",
                },
                "itemStyle": {"color": "transparent", "borderWidth": 0},
                "side": "instrument",
                "node_type": "header",
                "role": role,
                "interactive": True,
            }
        )
        y += NODE_GAP

        channels = inst.get("channels", ["1"])
        for ch in channels:
            channel_key = f"{role}:{ch}"
            is_used = state.is_channel_used(role, ch)
            is_compatible = channel_key in state.compatible_channels
            has_selection = state.selected_pin is not None

            # Determine category
            if is_used:
                cat = CAT_CONNECTED
            elif is_compatible and has_selection:
                cat = CAT_COMPATIBLE
            else:
                cat = CAT_INSTRUMENT

            node = {
                "name": channel_key,
                "x": RIGHT_X,
                "y": y,
                "category": cat,
                "symbolSize": NODE_SIZE,
                "symbol": "circle",
                "label": {
                    "show": True,
                    "position": "right",
                    "formatter": f"CH{ch}" if ch.isdigit() else ch,
                    "fontSize": 12,
                },
                "side": "instrument",
                "node_type": "channel",
                "interactive": True,
                "role": role,
                "channel": ch,
            }

            if is_compatible and has_selection and not is_used:
                node["itemStyle"] = {
                    "borderColor": "#22c55e",
                    "borderWidth": 3,
                    "shadowColor": "rgba(34,197,94,0.4)",
                    "shadowBlur": 8,
                }
            elif has_selection and not is_compatible and not is_used:
                node["itemStyle"] = {"opacity": 0.3}
            elif is_used:
                node["itemStyle"] = {
                    "color": "#22c55e",
                    "borderColor": "#16a34a",
                    "borderWidth": 2,
                }

            nodes.append(node)
            y += NODE_GAP

        y += GROUP_GAP - NODE_GAP

    return nodes


def build_links(state: DesignerState) -> list[dict]:
    """Build edges from connections."""
    links: list[dict] = []
    for point_name, conn in state.connections.items():
        source = conn["dut_pin"]
        target = f"{conn['instrument']}:{conn['channel']}"
        links.append(
            {
                "source": source,
                "target": target,
                "lineStyle": {
                    "color": "#22c55e",
                    "width": 2.5,
                    "type": "solid",
                },
                "point_name": point_name,
            }
        )
    return links


def build_categories() -> list[dict]:
    """Build color categories for graph nodes."""
    return [
        # CAT_PIN (0) — DUT pin with characteristics
        {"name": "Pin", "itemStyle": {"color": "#3b82f6", "borderColor": "#2563eb"}},
        # CAT_PIN_REF (1) — reference pin (no characteristics)
        {"name": "Reference", "itemStyle": {"color": "#94a3b8", "borderColor": "#64748b"}},
        # CAT_UNUSED (2)
        {"name": "Unused", "itemStyle": {"color": "#6b7280", "borderColor": "#4b5563"}},
        # CAT_INSTRUMENT (3)
        {"name": "Instrument", "itemStyle": {"color": "#8b5cf6", "borderColor": "#7c3aed"}},
        # CAT_HEADER (4)
        {"name": "Header", "itemStyle": {"color": "transparent", "borderColor": "transparent"}},
        # CAT_SELECTED (5)
        {"name": "Selected", "itemStyle": {"color": "#3b82f6", "borderColor": "#1d4ed8"}},
        # CAT_COMPATIBLE (6)
        {"name": "Compatible", "itemStyle": {"color": "#22c55e", "borderColor": "#16a34a"}},
        # CAT_CONNECTED (7)
        {"name": "Connected", "itemStyle": {"color": "#22c55e", "borderColor": "#16a34a"}},
    ]


def _group_pins_by_connector(dut_pins: dict[str, dict]) -> dict[str, list[str]]:
    """Group pin keys by connector prefix (e.g., J1, TP).

    Pins without an underscore separator go into a '' group.
    """
    groups: dict[str, list[str]] = {}

    for key in dut_pins:
        parts = key.split("_", 1)
        prefix = parts[0] if len(parts) > 1 else ""
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(key)

    return groups


def _pin_category(pin_key: str, state: DesignerState) -> int:
    """Derive category from characteristics — pins with chars vs reference pins."""
    if state.char_by_pin.get(pin_key):
        return CAT_PIN
    return CAT_PIN_REF
