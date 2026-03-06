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
        → FixtureManager.get_instrument_for_point("VOUT")
        → FixturePoint: instrument="dmm_main", channel="1"
        → instruments["dmm_main"] (actual DMM instance)
        → .measure_voltage() on the DMM
"""

from typing import Any

from litmus.config.models import FixtureConfig, FixturePoint
from litmus.instruments.base import Instrument


class FixtureManager:
    """Runtime fixture resolution from DUT pins to instruments.

    Resolves DUT pin names to instrument instances using fixture configuration.
    Handles the mapping between:
    - DUT pins (logical: VOUT, VIN, GND)
    - Fixture points (routing junctions)
    - Instrument instances (physical: DMM, PSU)
    - Instrument channels (for multi-channel instruments)

    Example:
        # Load from configs
        instruments = {"dmm_main": DMM(...), "psu_main": PSU(...)}
        fixture = FixtureConfig.model_validate(yaml_data)
        manager = FixtureManager(fixture, instruments)

        # Resolve and use
        dmm = manager.get_instrument_for_point("vout_measure")
        voltage = dmm.measure_voltage()
    """

    def __init__(
        self,
        fixture_config: FixtureConfig,
        instruments: dict[str, Instrument],
    ):
        """Initialize fixture manager.

        Args:
            fixture_config: Fixture configuration with point definitions
            instruments: Dictionary mapping instrument names to instances
        """
        self.fixture_config = fixture_config
        self.instruments = instruments

        # Build reverse lookup: dut_pin -> point_name
        self._pin_to_point: dict[str, str] = {}
        for point_name, point in fixture_config.points.items():
            if point.dut_pin:
                self._pin_to_point[point.dut_pin] = point_name

        # Build reverse lookup: net -> point_name
        self._net_to_point: dict[str, str] = {}
        for point_name, point in fixture_config.points.items():
            if point.net:
                self._net_to_point[point.net] = point_name

    def get_point(self, name: str) -> FixturePoint:
        """Get fixture point by name.

        Args:
            name: Point name (e.g., "vout_measure")

        Returns:
            FixturePoint configuration

        Raises:
            KeyError: If point not found
        """
        if name not in self.fixture_config.points:
            raise KeyError(f"Fixture point '{name}' not found")
        return self.fixture_config.points[name]

    def get_point_for_pin(self, pin_name: str) -> FixturePoint:
        """Get fixture point that connects to a DUT pin.

        Args:
            pin_name: DUT pin name (e.g., "VOUT")

        Returns:
            FixturePoint configuration

        Raises:
            KeyError: If no point connects to this pin
        """
        if pin_name not in self._pin_to_point:
            raise KeyError(f"No fixture point for DUT pin '{pin_name}'")
        point_name = self._pin_to_point[pin_name]
        return self.fixture_config.points[point_name]

    def get_point_for_net(self, net_name: str) -> FixturePoint:
        """Get fixture point that connects to a schematic net.

        Args:
            net_name: Schematic net name (e.g., "VOUT_3V3")

        Returns:
            FixturePoint configuration

        Raises:
            KeyError: If no point connects to this net
        """
        if net_name not in self._net_to_point:
            raise KeyError(f"No fixture point for net '{net_name}'")
        point_name = self._net_to_point[net_name]
        return self.fixture_config.points[point_name]

    def get_instrument_for_point(self, point_name: str) -> Instrument:
        """Get instrument instance for a fixture point.

        Args:
            point_name: Fixture point name (e.g., "vout_measure")

        Returns:
            Instrument instance

        Raises:
            KeyError: If point or instrument not found
        """
        point = self.get_point(point_name)
        if point.instrument not in self.instruments:
            raise KeyError(
                f"Instrument '{point.instrument}' for point '{point_name}' not found"
            )
        return self.instruments[point.instrument]

    def has_pin(self, pin_name: str) -> bool:
        """Check if a pin has a fixture connection."""
        return pin_name in self._pin_to_point

    def get_instrument_for_pin(self, pin_name: str) -> Instrument:
        """Get instrument instance for a DUT pin.

        Args:
            pin_name: DUT pin name (e.g., "VOUT")

        Returns:
            Instrument instance

        Raises:
            KeyError: If pin or instrument not found
        """
        point = self.get_point_for_pin(pin_name)
        if point.instrument not in self.instruments:
            raise KeyError(
                f"Instrument '{point.instrument}' for pin '{pin_name}' not found"
            )
        return self.instruments[point.instrument]

    def get_channel_for_point(self, point_name: str) -> str | None:
        """Get instrument channel for a fixture point.

        Args:
            point_name: Fixture point name

        Returns:
            Channel identifier, or None if not specified
        """
        point = self.get_point(point_name)
        return point.instrument_channel

    def get_channel_for_pin(self, pin_name: str) -> str | None:
        """Get instrument channel for a DUT pin.

        Args:
            pin_name: DUT pin name

        Returns:
            Channel identifier, or None if not specified
        """
        point = self.get_point_for_pin(pin_name)
        return point.instrument_channel

    def list_pins(self) -> list[str]:
        """List all DUT pins with fixture connections.

        Returns:
            List of DUT pin names
        """
        return list(self._pin_to_point.keys())

    def list_points(self) -> list[str]:
        """List all fixture point names.

        Returns:
            List of point names
        """
        return list(self.fixture_config.points.keys())


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
