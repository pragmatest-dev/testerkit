"""Electronic Load (ELoad) driver.

The ELoad driver implements the ConstantCurrentLoad, ConstantPowerLoad,
and ConstantResistanceLoad capability interfaces.
It extends VisaInstrument for SCPI communication.

Example usage:
    # Real hardware
    eload = ELoad("TCPIP::192.168.1.103::INSTR")
    with eload:
        eload.set_load_current(1.0)
        eload.enable_load()
        voltage = eload.measure_voltage()
        power = eload.measure_power()

    # Simulation
    eload = ELoad("TCPIP::192.168.1.103::INSTR", simulate=True)
    with eload:
        eload.set_load_current(1.0)
        eload.enable_load()
        voltage = eload.measure_voltage()  # Returns simulated value
"""

from decimal import Decimal
from typing import Any

from litmus.capabilities.interfaces import (
    ConstantCurrentLoad,
    ConstantPowerLoad,
    ConstantResistanceLoad,
)
from litmus.instruments.visa import VisaInstrument


class ELoad(VisaInstrument, ConstantCurrentLoad, ConstantPowerLoad, ConstantResistanceLoad):
    """Electronic Load driver.

    Implements capability interfaces:
    - ConstantCurrentLoad: set_load_current(), enable_load(), measure_voltage()
    - ConstantPowerLoad: set_load_power()
    - ConstantResistanceLoad: set_load_resistance()

    Supports both real hardware and simulation via VisaInstrument.
    """

    # Default simulation responses
    _default_idn = "Litmus,SimELoad,SN001,1.0"
    _sim_responses: dict[str, str | float] = {
        "MEAS:VOLT?": 5.0,
        "MEAS:CURR?": 0.0,
        "MEAS:POW?": 0.0,
        "CURR?": 0.0,
        "POW?": 0.0,
        "RES?": 1000000.0,
    }

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ):
        """Initialize ELoad.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.103::INSTR")
            simulate: If True, use pyvisa-sim simulation
            sim_config: Simulation configuration:
                - voltage: Input voltage reading (default: 5.0)
                - current: Current reading when load enabled (default: 0.0)
                - power: Power reading when load enabled (default: 0.0)
            timeout_ms: Communication timeout in milliseconds
        """
        processed_config = self._process_sim_config(sim_config or {})
        super().__init__(
            resource=resource,
            simulate=simulate,
            sim_config=processed_config,
            timeout_ms=timeout_ms,
        )
        self._idn: str | None = None
        self._load_enabled: bool = False
        self._mode: str = "CC"

    def _process_sim_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Process sim_config to map friendly names to SCPI responses."""
        processed = dict(config)

        responses = {}
        if "voltage" in config:
            responses["MEAS:VOLT?"] = config["voltage"]
        if "current" in config:
            responses["MEAS:CURR?"] = config["current"]
            responses["CURR?"] = config["current"]
        if "power" in config:
            responses["MEAS:POW?"] = config["power"]
            responses["POW?"] = config["power"]
        if "resistance" in config:
            responses["RES?"] = config["resistance"]

        if responses:
            processed["responses"] = {**responses, **processed.get("responses", {})}

        return processed

    def connect(self) -> None:
        """Connect to ELoad and read identification."""
        super().connect()
        if self._connected:
            self._idn = self.query("*IDN?")

    @property
    def idn(self) -> str | None:
        """Return instrument identification string."""
        return self._idn

    @property
    def load_enabled(self) -> bool:
        """Return whether load input is enabled."""
        return self._load_enabled

    @property
    def mode(self) -> str:
        """Return current operating mode (CC, CP, CR)."""
        return self._mode

    # -------------------------------------------------------------------------
    # ConstantCurrentLoad interface
    # -------------------------------------------------------------------------

    def set_load_current(self, current: Decimal) -> None:
        """Set load current (CC mode).

        Args:
            current: Load current in Amps
        """
        self._mode = "CC"
        self.write("MODE CC")
        self.write(f"CURR {current}")

    def enable_load(self) -> None:
        """Enable load input."""
        self.write("INP ON")
        self._load_enabled = True

    def disable_load(self) -> None:
        """Disable load input."""
        self.write("INP OFF")
        self._load_enabled = False

    def measure_voltage(self) -> Decimal:
        """Measure input voltage.

        Returns:
            Input voltage in Volts
        """
        response = self.query("MEAS:VOLT?")
        return Decimal(response)

    def measure_power(self) -> Decimal:
        """Measure input power.

        Returns:
            Input power in Watts
        """
        response = self.query("MEAS:POW?")
        return Decimal(response)

    # -------------------------------------------------------------------------
    # ConstantPowerLoad interface
    # -------------------------------------------------------------------------

    def set_load_power(self, power: Decimal) -> None:
        """Set load power (CP mode).

        Args:
            power: Load power in Watts
        """
        self._mode = "CP"
        self.write("MODE CP")
        self.write(f"POW {power}")

    # -------------------------------------------------------------------------
    # ConstantResistanceLoad interface
    # -------------------------------------------------------------------------

    def set_load_resistance(self, resistance: Decimal) -> None:
        """Set load resistance (CR mode).

        Args:
            resistance: Load resistance in Ohms
        """
        self._mode = "CR"
        self.write("MODE CR")
        self.write(f"RES {resistance}")

    # -------------------------------------------------------------------------
    # Additional ELoad-specific methods
    # -------------------------------------------------------------------------

    def measure_current(self) -> Decimal:
        """Measure actual load current.

        Returns:
            Current in Amps
        """
        response = self.query("MEAS:CURR?")
        return Decimal(response)

    def set_voltage_limit(self, voltage: Decimal) -> None:
        """Set voltage limit (protection).

        Args:
            voltage: Voltage limit in Volts
        """
        self.write(f"VOLT:LIM {voltage}")

    def set_power_limit(self, power: Decimal) -> None:
        """Set power limit (protection).

        Args:
            power: Power limit in Watts
        """
        self.write(f"POW:LIM {power}")

    def clear_protection(self) -> None:
        """Clear any tripped protection."""
        self.write("INP:PROT:CLE")

    # -------------------------------------------------------------------------
    # Convenience aliases for simpler test API
    # -------------------------------------------------------------------------

    def set_current(self, current: Decimal) -> None:
        """Alias for set_load_current()."""
        self.set_load_current(current)

    def enable(self) -> None:
        """Alias for enable_load()."""
        self.enable_load()

    def disable(self) -> None:
        """Alias for disable_load()."""
        self.disable_load()
