"""Designer state model — all mutable state for the system designer page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litmus.models.product import Product


class DesignerState:
    """Holds all mutable state for the system designer.

    Manages product pins, instruments, connections (fixture points),
    and UI selection state. Provides CRUD methods and serialization
    to fixture/station-type YAML formats.
    """

    def __init__(self) -> None:
        # --- Identifiers ---
        self.product_id: str | None = None
        self.system_id: str = ""
        self.fixture_id: str = ""

        # --- Product side ---
        self.product: Product | None = None
        self.dut_pins: dict[str, dict] = {}  # pin_key -> {name, net, type, description}
        self.char_by_pin: dict[str, list[str]] = {}  # pin_key -> [characteristic names]

        # --- Instrument side ---
        # role -> {type, driver, capabilities, channels}
        self.instruments: dict[str, dict] = {}

        # --- Fixture connections ---
        # connection_name -> {dut_pin, instrument, channel, terminal, net}
        self.connections: dict[str, dict] = {}

        # --- UI state ---
        self.selected_pin: str | None = None
        self.compatible_channels: set[str] = set()  # "role:channel" keys
        self.hide_disconnected: bool = False  # Hide instruments with no connections

    # -------------------------------------------------------------------------
    # Pin CRUD
    # -------------------------------------------------------------------------

    def add_pin(
        self,
        key: str,
        name: str,
        net: str,
        description: str | None = None,
    ) -> None:
        """Add a new DUT pin."""
        self.dut_pins[key] = {
            "name": name,
            "net": net,
            "description": description or "",
        }

    def edit_pin(self, key: str, **updates: Any) -> None:
        """Update fields on an existing pin."""
        if key in self.dut_pins:
            self.dut_pins[key].update(updates)

    def remove_pin(self, key: str) -> None:
        """Remove a pin and any connections referencing it."""
        self.dut_pins.pop(key, None)
        self.char_by_pin.pop(key, None)
        # Remove connections that reference this pin
        to_remove = [name for name, conn in self.connections.items() if conn["dut_pin"] == key]
        for name in to_remove:
            del self.connections[name]

    # -------------------------------------------------------------------------
    # Instrument CRUD
    # -------------------------------------------------------------------------

    def add_instrument(
        self,
        role: str,
        type_name: str,
        driver: str,
        capabilities: list[dict] | None = None,
        channels: list[str] | None = None,
    ) -> None:
        """Add an instrument to the system."""
        self.instruments[role] = {
            "type": type_name,
            "driver": driver,
            "capabilities": capabilities or [],
            "channels": channels or ["1"],
        }

    def remove_instrument(self, role: str) -> None:
        """Remove an instrument and all its connections."""
        self.instruments.pop(role, None)
        to_remove = [name for name, conn in self.connections.items() if conn["instrument"] == role]
        for name in to_remove:
            del self.connections[name]

    def load_station(self, station_config: dict) -> None:
        """Bulk-import instruments from a station configuration dict.

        Resolves channels from catalog entries when catalog_ref is provided,
        falling back to station-defined channels if no catalog reference.
        Also loads channel details (terminals, ground type) from catalog.
        """
        from litmus.ui.shared.services import load_catalog_entry_by_type

        instruments = station_config.get("instruments", {})
        for role, inst in instruments.items():
            driver = inst.get("driver", "")
            inst_type = inst.get("type", "")
            catalog_ref = inst.get("catalog_ref")

            # Try to get channels from catalog entry
            channels: list[str] = []
            channel_details: dict[str, dict] = {}  # ch -> {terminals, ground, connector}
            catalog_entry = None
            if catalog_ref:
                catalog_entry = load_catalog_entry_by_type(catalog_ref)
                if catalog_entry and catalog_entry.channels:
                    channels = list(catalog_entry.channels.keys())
                    for ch_name, ch_def in catalog_entry.channels.items():
                        channel_details[ch_name] = {
                            "terminals": ch_def.terminals or [],
                            "ground": ch_def.ground or "unknown",
                            "connector": ch_def.connector or "unknown",
                            "label": ch_def.label or ch_name,
                        }

            # Fall back to station-defined channels
            if not channels:
                station_channels = inst.get("channels", ["1"])
                if isinstance(station_channels, dict):
                    channels = list(station_channels.keys())
                elif isinstance(station_channels, list):
                    channels = [str(ch) for ch in station_channels]
                else:
                    channels = [str(station_channels)]

            self.instruments[role] = {
                "type": inst_type,
                "driver": driver,
                "capabilities": catalog_entry.capabilities if catalog_entry else [],
                "channels": channels,
                "channel_details": channel_details,
                "catalog_ref": catalog_ref,
            }

    # -------------------------------------------------------------------------
    # Connection CRUD
    # -------------------------------------------------------------------------

    def add_connection(
        self,
        point_name: str,
        dut_pin: str,
        instrument: str,
        channel: str,
        net: str | None = None,
        terminal: str | None = None,
    ) -> None:
        """Create a fixture point connection."""
        if net is None:
            pin_data = self.dut_pins.get(dut_pin, {})
            net = pin_data.get("net", "")
        self.connections[point_name] = {
            "dut_pin": dut_pin,
            "instrument": instrument,
            "channel": channel,
            "terminal": terminal,
            "net": net,
        }

    def remove_connection(self, point_name: str) -> None:
        """Remove a connection by point name."""
        self.connections.pop(point_name, None)

    def find_connection_by_link(self, pin_key: str, channel_key: str) -> str | None:
        """Find connection point name by pin and channel keys."""
        for name, conn in self.connections.items():
            conn_channel_key = f"{conn['instrument']}:{conn['channel']}"
            if conn["dut_pin"] == pin_key and conn_channel_key == channel_key:
                return name
        return None

    def find_connection_for_pin(self, pin_key: str) -> dict | None:
        """Find the first connection for a given pin, if any."""
        for conn in self.connections.values():
            if conn["dut_pin"] == pin_key:
                return conn
        return None

    def find_connections_for_pin(self, pin_key: str) -> list[dict]:
        """Find all connections for a given pin (GND pins may have multiple)."""
        return [conn for conn in self.connections.values() if conn["dut_pin"] == pin_key]

    # -------------------------------------------------------------------------
    # Selection
    # -------------------------------------------------------------------------

    def select_pin(self, pin_key: str) -> None:
        """Select a pin and compute compatible channels."""
        self.selected_pin = pin_key
        # compatible_channels is set externally by matching.py
        # since it needs product + instrument data

    def clear_selection(self) -> None:
        """Clear all selection state."""
        self.selected_pin = None
        self.compatible_channels = set()

    def is_pin_connected(self, pin_key: str) -> bool:
        """Check if a pin has a connection."""
        return any(c["dut_pin"] == pin_key for c in self.connections.values())

    def is_channel_used(self, role: str, channel: str) -> bool:
        """Check if an instrument channel is already wired."""
        return any(
            c["instrument"] == role and c["channel"] == channel for c in self.connections.values()
        )

    def is_terminal_used(self, role: str, channel: str, terminal: str) -> bool:
        """Check if a specific terminal is already wired."""
        return any(
            c["instrument"] == role and c["channel"] == channel and c["terminal"] == terminal
            for c in self.connections.values()
        )

    def instrument_has_connections(self, role: str) -> bool:
        """Check if an instrument role has any wired connections."""
        return any(conn["instrument"] == role for conn in self.connections.values())

    def would_create_output_conflict(self, pin_key: str, role: str, channel: str) -> bool:
        """Check if wiring this channel to this pin would create an output conflict.

        ONE OUTPUT MAX per connection group. Count outputs:
        - The channel (if OUTPUT)
        - The pin (if OUTPUT based on characteristics)
        - Any existing connections to this pin with OUTPUT channels
        - Any existing connections to this channel with OUTPUT pins

        If total > 1, it's a conflict.
        """
        output_count = 0

        # Count: is this channel an output?
        ch_is_out = self._channel_is_output(role, channel)
        if ch_is_out:
            output_count += 1

        # Count: is this pin an output?
        pin_is_out = self._pin_is_output(pin_key)
        if pin_is_out:
            output_count += 1

        # Count: existing output channels connected to this pin
        for conn in self.connections.values():
            if conn["dut_pin"] == pin_key:
                if self._channel_is_output(conn["instrument"], conn["channel"]):
                    output_count += 1

        # Count: existing output pins connected to this channel
        for conn in self.connections.values():
            if conn["instrument"] == role and conn["channel"] == channel:
                if self._pin_is_output(conn["dut_pin"]):
                    output_count += 1

        return output_count > 1

    def _pin_is_output(self, pin_key: str) -> bool:
        """Check if a DUT pin is an output (provides signal)."""
        from litmus.models.enums import Direction

        char_names = self.char_by_pin.get(pin_key, [])
        if char_names and self.product:
            for char_name in char_names:
                char = self.product.characteristics.get(char_name)
                if char and hasattr(char, "direction"):
                    if char.direction == Direction.OUTPUT:
                        return True
        return False

    def _channel_is_output(self, role: str, channel: str) -> bool:
        """Check if a channel is an OUTPUT (source) channel."""
        from litmus.models.enums import Direction

        inst = self.instruments.get(role)
        if not inst:
            return False

        caps = inst.get("capabilities", [])
        for cap in caps:
            raw_channels = getattr(cap, "channels", None)
            if raw_channels:
                cap_channels = raw_channels if isinstance(raw_channels, list) else [raw_channels]
            else:
                cap_channels = inst.get("channels", ["1"])

            direction = getattr(cap, "direction", None)
            if channel not in cap_channels:
                continue

            if direction in (Direction.OUTPUT, Direction.BIDIR):
                return True

        return False

    # -------------------------------------------------------------------------
    # Bulk Operations
    # -------------------------------------------------------------------------

    def clear_all_connections(self) -> None:
        """Remove all connections."""
        self.connections.clear()

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    @property
    def wired_pin_count(self) -> int:
        """Number of pins that have connections."""
        wired = {c["dut_pin"] for c in self.connections.values()}
        return len(wired)

    @property
    def total_pin_count(self) -> int:
        """Total number of DUT pins."""
        return len(self.dut_pins)

    @property
    def available_pin_count(self) -> int:
        """Number of pins without connections."""
        return self.total_pin_count - self.wired_pin_count

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_fixture_yaml(self) -> dict:
        """Serialize to FixtureConfig YAML format."""
        fixture = {
            "id": self.fixture_id,
            "name": self.fixture_id.replace("_", " ").replace("-", " ").title(),
        }
        if self.product_id:
            fixture["product_id"] = self.product_id

        connections = {}
        for connection_name, conn in self.connections.items():
            entry: dict[str, Any] = {
                "name": connection_name,  # Required by FixtureConnection model
                "dut_pin": conn["dut_pin"],
                "instrument": conn["instrument"],
            }
            if conn.get("channel"):
                entry["instrument_channel"] = conn["channel"]
            if conn.get("terminal"):
                entry["instrument_terminal"] = conn["terminal"]
            if conn.get("net"):
                entry["net"] = conn["net"]
            connections[connection_name] = entry

        return {"fixture": fixture, "connections": connections}

    def to_station_type_yaml(self) -> dict:
        """Serialize to StationType YAML format."""
        station_type = {
            "id": self.system_id,
            "description": f"Station type for {self.product_id or 'system'}",
            "instruments": {},
        }

        for role, inst in self.instruments.items():
            inst_config: dict[str, str] = {}
            if inst.get("type"):
                inst_config["type"] = inst["type"]
            if inst.get("driver"):
                inst_config["driver"] = inst["driver"]
            station_type["instruments"][role] = inst_config

        return {"station_type": station_type}

    def to_product_pins_patch(self) -> dict:
        """Generate pin updates for product spec YAML."""
        pins = {}
        for key, pin in self.dut_pins.items():
            pin_data: dict[str, str] = {"name": pin["name"]}
            if pin.get("net"):
                pin_data["net"] = pin["net"]
            if pin.get("role") and pin["role"] != "signal":
                pin_data["role"] = pin["role"]
            if pin.get("description"):
                pin_data["description"] = pin["description"]
            pins[key] = pin_data
        return pins

    def load_product(self, product: Any) -> None:
        """Load product data into state.

        Args:
            product: Product model with pins and characteristics.
        """
        self.product = product
        self.product_id = product.id

        # Load pins
        self.dut_pins = {}
        if hasattr(product, "pins") and product.pins:
            for key, pin in product.pins.items():
                self.dut_pins[key] = {
                    "name": pin.name,
                    "net": pin.net or "",
                    "role": pin.role.value if hasattr(pin, "role") else "signal",
                    "description": pin.description or "",
                }

        # Build pin -> characteristics reverse map
        self.char_by_pin = {}
        if hasattr(product, "characteristics"):
            for char_name, char in product.characteristics.items():
                for pin_key in char.resolved_pins:
                    if pin_key not in self.char_by_pin:
                        self.char_by_pin[pin_key] = []
                    self.char_by_pin[pin_key].append(char_name)

        # Default IDs from product
        if not self.system_id:
            self.system_id = f"{product.id}_system"
        if not self.fixture_id:
            self.fixture_id = f"{product.id}_fixture_v1"

    def load_fixture(self, fixture_config: Any) -> None:
        """Load existing fixture data into connections."""
        self.connections.clear()
        if hasattr(fixture_config, "id") and fixture_config.id:
            self.fixture_id = fixture_config.id
        elif isinstance(fixture_config, dict):
            fixture_info = fixture_config.get("fixture", {})
            if fixture_info.get("id"):
                self.fixture_id = fixture_info["id"]

        connections_attr = getattr(fixture_config, "connections", None)
        connections = (
            connections_attr
            if connections_attr is not None
            else fixture_config.get("connections", {})
        )
        for connection_name, fc in connections.items():
            if hasattr(fc, "dut_pin"):
                self.connections[connection_name] = {
                    "dut_pin": fc.dut_pin or "",
                    "instrument": fc.instrument or "",
                    "channel": fc.instrument_channel or "1",
                    "terminal": fc.instrument_terminal,
                    "net": fc.net or "",
                }
            else:
                self.connections[connection_name] = {
                    "dut_pin": fc.get("dut_pin", ""),
                    "instrument": fc.get("instrument", ""),
                    "channel": fc.get("instrument_channel", "1"),
                    "terminal": fc.get("instrument_terminal"),
                    "net": fc.get("net", ""),
                }
