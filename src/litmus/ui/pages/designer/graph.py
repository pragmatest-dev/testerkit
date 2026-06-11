"""ECharts graph builder for the system designer.

Transforms DesignerState into an ECharts graph series option with:
- Part pins as nodes on the left
- Instrument channels as nodes on the right
- Connections as edges between them
- Color coding by capability and selection state
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litmus.models.enums import COAXIAL_CONNECTORS, ConnectorType

if TYPE_CHECKING:
    from litmus.ui.pages.designer.state import DesignerState

# Layout constants
LEFT_X = 80
RIGHT_X = 620
MID_X = (LEFT_X + RIGHT_X) // 2  # Elbow routing corridor
GROUP_GAP = 50
NODE_GAP = 35
HEADER_SIZE = 20
NODE_SIZE = 16
WIRE_SPACING = 4  # Pixels between parallel subway-style wires

# Connection color palette — distinct hues for subway-style routing
WIRE_COLORS = [
    "#2563eb",  # blue
    "#dc2626",  # red
    "#059669",  # emerald
    "#d97706",  # amber
    "#7c3aed",  # violet
    "#db2777",  # pink
    "#0891b2",  # cyan
    "#65a30d",  # lime
    "#ea580c",  # orange
    "#4f46e5",  # indigo
]

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
CAT_FLOATING = 8  # Floating/isolated channel


def build_graph_option(state: DesignerState) -> dict:
    """Build complete ECharts option from designer state."""
    nodes = build_nodes(state)
    links, waypoint_nodes = build_links(state, nodes)
    nodes.extend(waypoint_nodes)

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
                    "curveness": 0,
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
    """Build graph nodes for part pins and instrument channels."""
    nodes: list[dict] = []
    y = 20

    # --- Part side (left) ---
    groups = _group_pins_by_connector(state.dut_pins)
    for group_type, pin_keys in groups.items():
        # Group header
        nodes.append(
            {
                "name": f"__header_part_{group_type}",
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
                "side": "part",
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
                "side": "part",
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
        # Skip disconnected instruments if hiding enabled, UNLESS a pin is selected
        if state.hide_disconnected:
            has_connections = state.instrument_has_connections(role)
            pin_selected = state.selected_pin is not None
            if not has_connections and not pin_selected:
                continue

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
        channel_details = inst.get("channel_details", {})

        for ch in channels:
            channel_key = f"{role}:{ch}"
            ch_detail = channel_details.get(ch, {})
            terminals = list(ch_detail.get("terminals", []))
            ground_type = ch_detail.get("ground", "unknown")
            is_floating = ground_type == "floating"
            connector = ch_detail.get("connector", "")

            # Auto-add shield terminal for coaxial connectors if not explicit
            if connector:
                try:
                    conn_type = ConnectorType(connector)
                    if conn_type in COAXIAL_CONNECTORS:
                        # Add shield if no ground-like terminal already present
                        if not any(t.lower() in {"ground", "shield", "gnd"} for t in terminals):
                            terminals.append("shield")
                except ValueError:
                    pass  # Unknown connector type, skip auto-add

            # Format channel label
            if ch.isdigit():
                ch_label = f"CH{ch}"
            else:
                ch_label = ch

            # If we have terminals, show them as individual nodes
            # Otherwise fall back to showing just the channel
            if terminals:
                # Check if there's a wirable ground terminal
                ground_terminals = {"lo", "gnd", "ground", "return", "com", "sense_lo", "shield"}
                has_ground_terminal = any(t.lower() in ground_terminals for t in terminals)

                # Channel subheader
                subheader_text = ch_label
                if connector:
                    subheader_text += f" ({connector})"
                # Only show ground indicator if there's a terminal to wire to
                if has_ground_terminal:
                    if is_floating:
                        subheader_text += " ⏊"
                    elif ground_type == "shared":
                        subheader_text += " ⏚"

                nodes.append(
                    {
                        "name": f"__subheader_{channel_key}",
                        "x": RIGHT_X,
                        "y": y,
                        "category": CAT_HEADER,
                        "symbolSize": 12,
                        "symbol": "rect",
                        "label": {
                            "show": True,
                            "position": "right",
                            "formatter": subheader_text,
                            "fontSize": 11,
                            "color": "#64748b",
                        },
                        "itemStyle": {"color": "transparent", "borderWidth": 0},
                        "side": "instrument",
                        "node_type": "channel_header",
                        "interactive": False,
                        "role": role,
                        "channel": ch,
                    }
                )
                y += NODE_GAP * 0.7

                # Show each terminal as a connection point
                for term in terminals:
                    terminal_key = f"{role}:{ch}:{term}"
                    is_used = state.is_terminal_used(role, ch, term)
                    is_compatible = channel_key in state.compatible_channels
                    has_selection = state.selected_pin is not None

                    # Determine category
                    if is_used:
                        cat = CAT_CONNECTED
                    elif is_compatible and has_selection:
                        cat = CAT_COMPATIBLE
                    else:
                        cat = CAT_INSTRUMENT

                    # Format terminal label (HI, LO, etc.)
                    term_label = term.upper()

                    node = {
                        "name": terminal_key,
                        "x": RIGHT_X + 20,  # Indent terminals slightly
                        "y": y,
                        "category": cat,
                        "symbolSize": NODE_SIZE - 2,
                        "symbol": "circle",
                        "label": {
                            "show": True,
                            "position": "right",
                            "formatter": term_label,
                            "fontSize": 11,
                        },
                        "side": "instrument",
                        "node_type": "terminal",
                        "interactive": True,
                        "role": role,
                        "channel": ch,
                        "terminal": term,
                        "ground": ground_type,
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
                    y += NODE_GAP * 0.7

            else:
                # No terminal info - show channel as a single node (legacy/fallback)
                # Without terminal info, we can't show ground indicators since
                # we don't know if there's a wirable ground connection
                is_used = state.is_channel_used(role, ch)
                is_compatible = channel_key in state.compatible_channels
                has_selection = state.selected_pin is not None

                if is_used:
                    cat = CAT_CONNECTED
                elif is_compatible and has_selection:
                    cat = CAT_COMPATIBLE
                else:
                    cat = CAT_INSTRUMENT

                label_text = ch_label

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
                        "formatter": label_text,
                        "fontSize": 12,
                    },
                    "side": "instrument",
                    "node_type": "channel",
                    "interactive": True,
                    "role": role,
                    "channel": ch,
                    "ground": ground_type,
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


def build_links(state: DesignerState, nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build edges with orthogonal elbow routing and subway-style coloring.

    Each connection becomes 3 segments via 2 invisible waypoint nodes:
    pin ─── wp1 (horizontal out)
             │
            wp2 (vertical)
             ─── channel (horizontal in)

    Vertical segments are spread across the corridor so they don't overlap.
    Horizontal segments sharing the same Y level are offset like subway lines
    so parallel wires run side-by-side instead of on top of each other.
    Each connection gets a distinct color from the palette.

    Returns (links, waypoint_nodes).
    """
    node_y = {n["name"]: n["y"] for n in nodes}
    links: list[dict] = []
    waypoints: list[dict] = []

    conn_list = list(state.connections.items())
    n_conns = len(conn_list)
    if not n_conns:
        return links, waypoints

    # Sort connections by source (DUT pin) Y position so top-to-bottom pins
    # get right-to-left vertical lanes in the corridor (top pin = rightmost)
    conn_list.sort(key=lambda item: node_y.get(item[1]["dut_pin"], 0))

    # Spread verticals evenly across the corridor
    corridor_left = LEFT_X + 60
    corridor_right = RIGHT_X - 60
    corridor_width = corridor_right - corridor_left

    # Pre-compute base Y values and group by shared Y levels to
    # calculate subway offsets for parallel horizontal segments.
    source_y_groups: dict[float, list[int]] = {}
    target_y_groups: dict[float, list[int]] = {}

    def _make_target_key(conn: dict) -> str:
        """Build target node key from connection, including terminal if present."""
        base = f"{conn['instrument']}:{conn['channel']}"
        if conn.get("terminal"):
            return f"{base}:{conn['terminal']}"
        return base

    for idx, (point_name, conn) in enumerate(conn_list):
        source = conn["dut_pin"]
        target = _make_target_key(conn)
        sy = node_y.get(source, 0)
        ty = node_y.get(target, 0)
        source_y_groups.setdefault(sy, []).append(idx)
        target_y_groups.setdefault(ty, []).append(idx)

    # Build offset lookup: conn index -> (source_y_offset, target_y_offset)
    def _offsets_for_group(group: list[int]) -> dict[int, float]:
        if len(group) <= 1:
            return {group[0]: 0.0}
        offsets = {}
        for rank, idx in enumerate(group):
            offsets[idx] = (rank - (len(group) - 1) / 2) * WIRE_SPACING
        return offsets

    source_offsets: dict[int, float] = {}
    for group in source_y_groups.values():
        source_offsets.update(_offsets_for_group(group))

    target_offsets: dict[int, float] = {}
    for group in target_y_groups.values():
        target_offsets.update(_offsets_for_group(group))

    def _make_wp(name: str, x: float, y: float) -> dict:
        return {
            "name": name,
            "x": x,
            "y": y,
            "category": CAT_HEADER,
            "symbolSize": 0,
            "symbol": "none",
            "label": {"show": False},
            "itemStyle": {"color": "transparent", "borderWidth": 0},
            "interactive": False,
            "node_type": "waypoint",
        }

    for idx, (point_name, conn) in enumerate(conn_list):
        source = conn["dut_pin"]
        target = _make_target_key(conn)

        # Skip connections to non-existent nodes (orphaned fixture data)
        if source not in node_y or target not in node_y:
            continue

        # Determine target X position (terminals are indented)
        target_x = RIGHT_X + 20 if conn.get("terminal") else RIGHT_X

        src_orig_y = node_y.get(source, 0)
        tgt_orig_y = node_y.get(target, 0)
        src_offset = source_offsets.get(idx, 0)
        tgt_offset = target_offsets.get(idx, 0)
        src_y = src_orig_y + src_offset
        tgt_y = tgt_orig_y + tgt_offset

        # Each connection gets its own X lane in the corridor
        # Top pins get rightmost lanes, working left as we go down
        if n_conns <= 1:
            lane_x = MID_X
        else:
            lane_x = corridor_right - (corridor_width * idx / (n_conns - 1))

        color = WIRE_COLORS[idx % len(WIRE_COLORS)]
        line_style = {
            "color": color,
            "width": 2.5,
            "type": "solid",
        }

        # Build the path as a chain of waypoints.
        # When there's a Y offset, add stub waypoints at the pin/channel
        # X position so the wire stays horizontal all the way to the
        # connector, then drops vertically to meet the node.
        #
        # No offset:  pin ─── wp1 │ wp2 ─── channel   (3 segments)
        # With offset: pin │ stub_L ─── wp1 │ wp2 ─── stub_R │ channel
        chain: list[str] = [source]

        if src_offset:
            stub_l = f"__wp_{point_name}_sl"
            waypoints.append(_make_wp(stub_l, LEFT_X, src_y))
            chain.append(stub_l)

        wp1 = f"__wp_{point_name}_1"
        wp2 = f"__wp_{point_name}_2"
        waypoints.append(_make_wp(wp1, lane_x, src_y))
        waypoints.append(_make_wp(wp2, lane_x, tgt_y))
        chain.extend([wp1, wp2])

        if tgt_offset:
            stub_r = f"__wp_{point_name}_sr"
            waypoints.append(_make_wp(stub_r, target_x, tgt_y))
            chain.append(stub_r)

        chain.append(target)

        # Emit link segments along the chain
        for i in range(len(chain) - 1):
            link: dict = {
                "source": chain[i],
                "target": chain[i + 1],
                "lineStyle": line_style,
                "point_name": point_name,  # All segments share the same connection ID
            }
            links.append(link)

    return links, waypoints


def build_categories() -> list[dict]:
    """Build color categories for graph nodes."""
    return [
        # CAT_PIN (0) — DUT pin with characteristics
        {"name": "Pin", "itemStyle": {"color": "#3b82f6", "borderColor": "#2563eb"}},
        # CAT_PIN_REF (1) — reference pin (no characteristics)
        {"name": "Reference", "itemStyle": {"color": "#94a3b8", "borderColor": "#64748b"}},
        # CAT_UNUSED (2)
        {"name": "Unused", "itemStyle": {"color": "#6b7280", "borderColor": "#4b5563"}},
        # CAT_INSTRUMENT (3) — shared ground channel
        {"name": "Instrument", "itemStyle": {"color": "#8b5cf6", "borderColor": "#7c3aed"}},
        # CAT_HEADER (4)
        {"name": "Header", "itemStyle": {"color": "transparent", "borderColor": "transparent"}},
        # CAT_SELECTED (5)
        {"name": "Selected", "itemStyle": {"color": "#3b82f6", "borderColor": "#1d4ed8"}},
        # CAT_COMPATIBLE (6)
        {"name": "Compatible", "itemStyle": {"color": "#22c55e", "borderColor": "#16a34a"}},
        # CAT_CONNECTED (7)
        {"name": "Connected", "itemStyle": {"color": "#22c55e", "borderColor": "#16a34a"}},
        # CAT_FLOATING (8) — floating/isolated channel
        {"name": "Floating", "itemStyle": {"color": "#f59e0b", "borderColor": "#d97706"}},
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
