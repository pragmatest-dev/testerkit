"""Mock instrument implementations for fast unit testing.

These mocks implement capability interfaces directly without any
pyvisa-sim overhead. Use for fast unit tests of test logic.

For integration tests that validate SCPI commands, use the real
drivers with simulate=True instead.

Example usage:
    from litmus.instruments.mocks import MockDMM, MockPSU

    def test_voltage_check():
        dmm = MockDMM(voltage=3.3)
        assert dmm.measure_voltage() == Decimal("3.3")

    def test_power_sequence():
        psu = MockPSU()
        psu.set_voltage(5.0)
        psu.enable_output()
        assert psu.output_enabled
        assert psu.voltage_setpoint == Decimal("5.0")
"""

from decimal import Decimal
from typing import Any

from litmus.capabilities.interfaces import (
    ConstantCurrentLoad,
    ConstantPowerLoad,
    ConstantResistanceLoad,
    CurrentInput,
    CurrentOutput,
    FrequencyInput,
    ResistanceInput,
    VoltageInput,
    VoltageOutput,
)
from litmus.capabilities.models import SignalType
from litmus.instruments.base import Instrument


class MockDMM(Instrument, VoltageInput, CurrentInput, ResistanceInput, FrequencyInput):
    """Mock DMM for fast unit tests.

    All values are configurable and returned immediately without I/O.

    Args:
        voltage: Voltage reading to return
        current: Current reading to return
        resistance: Resistance reading to return
        frequency: Frequency reading to return
        **kwargs: Additional values accessible via get_value()
    """

    def __init__(
        self,
        voltage: float = 0.0,
        current: float = 0.0,
        resistance: float = 1000.0,
        frequency: float = 1000.0,
        **kwargs: Any,
    ):
        super().__init__(resource="MOCK::DMM", simulate=True)
        self._values = {
            "voltage": Decimal(str(voltage)),
            "current": Decimal(str(current)),
            "resistance": Decimal(str(resistance)),
            "frequency": Decimal(str(frequency)),
            **{k: Decimal(str(v)) if isinstance(v, (int, float)) else v for k, v in kwargs.items()},
        }
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def set_value(self, name: str, value: float | Decimal) -> None:
        """Update a simulated value dynamically."""
        self._values[name] = Decimal(str(value))

    def get_value(self, name: str) -> Decimal:
        """Get a configured value."""
        return self._values.get(name, Decimal("0"))

    # VoltageInput
    def measure_voltage(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        return self._values["voltage"]

    def configure_voltage_range(self, range_val: Decimal | str) -> None:
        pass  # No-op for mock

    # CurrentInput
    def measure_current(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        return self._values["current"]

    def configure_current_range(self, range_val: Decimal | str) -> None:
        pass

    # ResistanceInput
    def measure_resistance(self, four_wire: bool = False) -> Decimal:
        return self._values["resistance"]

    def configure_resistance_range(self, range_val: Decimal | str) -> None:
        pass

    # FrequencyInput
    def measure_frequency(self) -> Decimal:
        return self._values["frequency"]

    def measure_period(self) -> Decimal:
        freq = self._values["frequency"]
        return Decimal("1") / freq if freq else Decimal("0")


class MockPSU(Instrument, VoltageOutput, CurrentOutput):
    """Mock PSU for fast unit tests.

    Tracks setpoints and output state without any I/O.

    Args:
        voltage: Initial voltage readback
        current: Initial current readback
    """

    def __init__(self, voltage: float = 0.0, current: float = 0.0):
        super().__init__(resource="MOCK::PSU", simulate=True)
        self._voltage_setpoint = Decimal("0")
        self._current_setpoint = Decimal("0")
        self._voltage_readback = Decimal(str(voltage))
        self._current_readback = Decimal(str(current))
        self._output_enabled = False
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def voltage_setpoint(self) -> Decimal:
        return self._voltage_setpoint

    @property
    def current_setpoint(self) -> Decimal:
        return self._current_setpoint

    @property
    def output_enabled(self) -> bool:
        return self._output_enabled

    # VoltageOutput
    def set_voltage(self, voltage: Decimal) -> None:
        self._voltage_setpoint = Decimal(str(voltage))
        # When output enabled, readback follows setpoint
        if self._output_enabled:
            self._voltage_readback = self._voltage_setpoint

    def set_voltage_limit(self, limit: Decimal) -> None:
        pass

    def enable_output(self, channel: str | None = None) -> None:
        self._output_enabled = True
        self._voltage_readback = self._voltage_setpoint
        self._current_readback = self._current_setpoint

    def disable_output(self, channel: str | None = None) -> None:
        self._output_enabled = False
        self._voltage_readback = Decimal("0")
        self._current_readback = Decimal("0")

    def measure_output_voltage(self) -> Decimal:
        return self._voltage_readback

    # CurrentOutput
    def set_current(self, current: Decimal) -> None:
        self._current_setpoint = Decimal(str(current))
        if self._output_enabled:
            self._current_readback = self._current_setpoint

    def set_current_limit(self, limit: Decimal) -> None:
        pass

    def measure_output_current(self) -> Decimal:
        return self._current_readback


class MockELoad(Instrument, ConstantCurrentLoad, ConstantPowerLoad, ConstantResistanceLoad):
    """Mock Electronic Load for fast unit tests.

    Tracks load settings and simulates voltage/power based on mode.

    Args:
        voltage: Simulated input voltage from DUT
    """

    def __init__(self, voltage: float = 5.0):
        super().__init__(resource="MOCK::ELOAD", simulate=True)
        self._input_voltage = Decimal(str(voltage))
        self._load_current = Decimal("0")
        self._load_power = Decimal("0")
        self._load_resistance = Decimal("1000000")  # High-Z default
        self._enabled = False
        self._mode = "CC"  # CC, CP, CR
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def mode(self) -> str:
        return self._mode

    def set_input_voltage(self, voltage: float) -> None:
        """Set simulated input voltage (for test setup)."""
        self._input_voltage = Decimal(str(voltage))

    # ConstantCurrentLoad
    def set_load_current(self, current: Decimal) -> None:
        self._load_current = Decimal(str(current))
        self._mode = "CC"

    def enable_load(self) -> None:
        self._enabled = True

    def disable_load(self) -> None:
        self._enabled = False

    def measure_voltage(self) -> Decimal:
        return self._input_voltage

    def measure_power(self) -> Decimal:
        if not self._enabled:
            return Decimal("0")
        if self._mode == "CC":
            return self._input_voltage * self._load_current
        elif self._mode == "CP":
            return self._load_power
        else:  # CR
            return self._input_voltage * self._input_voltage / self._load_resistance

    # ConstantPowerLoad
    def set_load_power(self, power: Decimal) -> None:
        self._load_power = Decimal(str(power))
        self._mode = "CP"

    # ConstantResistanceLoad
    def set_load_resistance(self, resistance: Decimal) -> None:
        self._load_resistance = Decimal(str(resistance))
        self._mode = "CR"
