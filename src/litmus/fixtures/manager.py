"""Fixture manager for DUT pin routing.

The FixtureManager provides runtime resolution from DUT pin names to
instrument instances. This enables UUT-centric test code:

    def test_output_voltage(pins):
        pins["VIN"].set_voltage(5.0)
        pins["VIN"].enable_output()
        assert pins["VOUT"].measure_voltage() > 3.0

Resolution Flow:
    pins["VOUT"]
        → PinAccessor.__getitem__("VOUT")
        → FixtureManager.get_instrument_for_connection("VOUT")
        → FixtureConnection: instrument="dmm_main", channel="1"
        → instruments["dmm_main"] (actual DMM instance)
        → .measure_voltage() on the DMM
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.base import Instrument
from litmus.models.config import FixtureConfig, FixtureConnection


class FixtureManager:
    """Runtime fixture resolution from DUT pins to instruments.

    Resolves DUT pin names to instrument instances using fixture configuration.
    Handles the mapping between:
    - DUT pins (logical: VOUT, VIN, GND)
    - Fixture connections (named DUT-pin ↔ instrument-channel pairings)
    - Instrument instances (physical: DMM, PSU)
    - Instrument channels (for multi-channel instruments)

    When a ``route_manager`` is provided and a connection has a
    ``route``, the returned instrument is wrapped in a ``RoutedProxy``
    that lazy-activates the switch route on first use.

    Example:
        # Load from configs
        instruments = {"dmm_main": DMM(...), "psu_main": PSU(...)}
        fixture = FixtureConfig.model_validate(yaml_data)
        manager = FixtureManager(fixture, instruments)

        # Resolve and use
        dmm = manager.get_instrument_for_connection("vout_measure")
        voltage = dmm.measure_voltage()
    """

    def __init__(
        self,
        fixture_config: FixtureConfig,
        instruments: dict[str, Instrument],
        route_manager: Any = None,
    ):
        """Initialize fixture manager.

        Args:
            fixture_config: Fixture configuration with connection definitions
            instruments: Dictionary mapping instrument names to instances
            route_manager: Optional RouteManager for switched routing
        """
        self.fixture_config = fixture_config
        self.instruments = instruments
        self._route_manager = route_manager

        # Build reverse lookup: dut_pin -> connection_name
        self._pin_to_connection: dict[str, str] = {}
        for connection_name, connection in fixture_config.connections.items():
            if connection.dut_pin:
                self._pin_to_connection[connection.dut_pin] = connection_name

        # Build reverse lookup: net -> connection_name
        self._net_to_connection: dict[str, str] = {}
        for connection_name, connection in fixture_config.connections.items():
            if connection.net:
                self._net_to_connection[connection.net] = connection_name

    def get_connection(self, name: str) -> FixtureConnection:
        """Get fixture connection by name.

        Args:
            name: Connection name (e.g., "vout_measure")

        Returns:
            FixtureConnection configuration

        Raises:
            KeyError: If connection not found
        """
        if name not in self.fixture_config.connections:
            raise KeyError(f"Fixture connection '{name}' not found")
        return self.fixture_config.connections[name]

    def get_connection_for_pin(self, pin_name: str) -> FixtureConnection:
        """Get fixture connection that connects to a DUT pin.

        Args:
            pin_name: DUT pin name (e.g., "VOUT")

        Returns:
            FixtureConnection configuration

        Raises:
            KeyError: If no connection binds to this pin
        """
        if pin_name not in self._pin_to_connection:
            raise KeyError(f"No fixture connection for DUT pin '{pin_name}'")
        connection_name = self._pin_to_connection[pin_name]
        return self.fixture_config.connections[connection_name]

    def get_connection_for_net(self, net_name: str) -> FixtureConnection:
        """Get fixture connection that connects to a schematic net.

        Args:
            net_name: Schematic net name (e.g., "VOUT_3V3")

        Returns:
            FixtureConnection configuration

        Raises:
            KeyError: If no connection binds to this net
        """
        if net_name not in self._net_to_connection:
            raise KeyError(f"No fixture connection for net '{net_name}'")
        connection_name = self._net_to_connection[net_name]
        return self.fixture_config.connections[connection_name]

    def get_instrument_for_connection(self, connection_name: str) -> Instrument:
        """Get instrument instance for a fixture connection.

        If the connection has a switch route and a route_manager is available,
        returns a RoutedProxy that lazy-activates the route on first use.

        Args:
            connection_name: Fixture connection name (e.g., "vout_measure")

        Returns:
            Instrument instance (or RoutedProxy wrapping it)

        Raises:
            KeyError: If connection or instrument not found (unless shared)
        """
        connection = self.get_connection(connection_name)
        return self._resolve_instrument(connection_name, connection)

    def has_pin(self, pin_name: str) -> bool:
        """Check if a pin has a fixture connection."""
        return pin_name in self._pin_to_connection

    def get_instrument_for_pin(self, pin_name: str) -> Instrument:
        """Get instrument instance for a DUT pin.

        If the connection has a switch route and a route_manager is available,
        returns a RoutedProxy that lazy-activates the route on first use.

        Args:
            pin_name: DUT pin name (e.g., "VOUT")

        Returns:
            Instrument instance (or RoutedProxy wrapping it)

        Raises:
            KeyError: If pin or instrument not found (unless shared)
        """
        if pin_name not in self._pin_to_connection:
            raise KeyError(f"No fixture connection for DUT pin '{pin_name}'")
        connection_name = self._pin_to_connection[pin_name]
        connection = self.fixture_config.connections[connection_name]
        return self._resolve_instrument(connection_name, connection)

    def get_channel_for_connection(self, connection_name: str) -> str | None:
        """Get instrument channel for a fixture connection.

        Args:
            connection_name: Fixture connection name

        Returns:
            Channel identifier, or None if not specified
        """
        connection = self.get_connection(connection_name)
        return connection.instrument_channel

    def get_channel_for_pin(self, pin_name: str) -> str | None:
        """Get instrument channel for a DUT pin.

        Args:
            pin_name: DUT pin name

        Returns:
            Channel identifier, or None if not specified
        """
        connection = self.get_connection_for_pin(pin_name)
        return connection.instrument_channel

    def list_pins(self) -> list[str]:
        """List all DUT pins with fixture connections.

        Returns:
            List of DUT pin names
        """
        return list(self._pin_to_connection.keys())

    def list_connections(self) -> list[str]:
        """List all fixture connection names.

        Returns:
            List of connection names
        """
        return list(self.fixture_config.connections.keys())

    def route(self, instrument: str) -> FixtureConnection | None:
        """Resolve the active ``FixtureConnection`` for a driver fixture.

        Reads ``_active_connection_var`` (pushed by ``ctx.connections``
        iteration). When a connection is active, confirms it routes to
        ``instrument`` and activates the connection's switch route (if
        any) before returning. When no connection is active (e.g.,
        tests without spec/connections markers or tests that bypass
        ``ctx.connections``), returns ``None`` and the driver fixture
        falls back to whatever default wiring it chooses.

        Args:
            instrument: Name of the driver instrument requesting routing
                (e.g., ``"dmm"``, ``"psu"``).

        Returns:
            Active ``FixtureConnection`` routed to ``instrument``, or
            ``None`` if no connection is active.

        Raises:
            RuntimeError: If an active connection targets a different
                instrument than the one requesting routing — a test
                authoring error that would otherwise silently mis-route.
        """
        from litmus.execution._state import get_active_connection

        connection = get_active_connection()
        if connection is None:
            return None
        if connection.instrument != instrument:
            raise RuntimeError(
                f"Active fixture connection {connection.name!r} routes to instrument "
                f"{connection.instrument!r}, not {instrument!r}. Check the test's "
                f"litmus_connections marker declares the correct instrument for this driver."
            )
        if connection.route is not None and self._route_manager is not None:
            self._route_manager.activate(connection.name)
        return connection

    def _resolve_instrument(
        self, connection_name: str, connection: FixtureConnection
    ) -> Instrument:
        """Resolve a fixture connection to its instrument, wrapping if routed."""
        if connection.instrument not in self.instruments:
            raise KeyError(
                f"Instrument '{connection.instrument}' for connection '{connection_name}' not found"
            )
        inst = self.instruments[connection.instrument]
        return self._maybe_wrap_routed(inst, connection_name, connection)

    def _maybe_wrap_routed(
        self,
        inst: Any,
        connection_name: str,
        connection: FixtureConnection,
    ) -> Instrument:
        """Wrap instrument in RoutedProxy if the connection has a switch route."""
        if connection.route is not None and self._route_manager is not None:
            from litmus.instruments.routed_proxy import RoutedProxy

            return RoutedProxy(inst, connection_name, self._route_manager)  # type: ignore[return-value]
        return inst


class PinAccessor:
    """Dictionary-like accessor for UUT-centric test code.

    Provides syntactic sugar for accessing instruments via DUT pin names:

        pins["VOUT"].measure_voltage()
        pins["VIN"].set_voltage(5.0)

    The accessor resolves pin names to instruments at access time.
    """

    def __init__(self, manager: FixtureManager):
        """Initialize pin accessor.

        Args:
            manager: FixtureManager with routing configuration
        """
        self._manager = manager

    def __getitem__(self, pin_name: str) -> Instrument:
        """Get instrument for a DUT pin.

        Args:
            pin_name: DUT pin name (e.g., "VOUT")

        Returns:
            Instrument instance connected to this pin
        """
        return self._manager.get_instrument_for_pin(pin_name)

    def __contains__(self, pin_name: str) -> bool:
        """Check if a pin has a fixture connection."""
        return self._manager.has_pin(pin_name)

    def __iter__(self):
        """Iterate over pin names."""
        return iter(self._manager.list_pins())

    def __len__(self) -> int:
        """Return number of available pins."""
        return len(self._manager.list_pins())

    def keys(self) -> list[str]:
        """Return all available pin names."""
        return self._manager.list_pins()

    def values(self) -> list[Instrument]:
        """Return all instruments mapped to pins."""
        return [self._manager.get_instrument_for_pin(p) for p in self._manager.list_pins()]

    def items(self) -> list[tuple[str, Instrument]]:
        """Return (pin_name, instrument) pairs."""
        return [(p, self._manager.get_instrument_for_pin(p)) for p in self._manager.list_pins()]

    def get(self, pin_name: str, default: Any = None) -> Instrument | Any:
        """Get instrument for a pin, with default.

        Args:
            pin_name: DUT pin name
            default: Value to return if pin not found

        Returns:
            Instrument instance or default
        """
        try:
            return self._manager.get_instrument_for_pin(pin_name)
        except KeyError:
            return default

    def channel(self, pin_name: str) -> str | None:
        """Get the instrument channel for a pin.

        Args:
            pin_name: DUT pin name

        Returns:
            Channel identifier, or None if not specified
        """
        return self._manager.get_channel_for_pin(pin_name)
