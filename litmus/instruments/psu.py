"""Power Supply Unit (PSU) driver.

The PSU driver implements the VoltageOutput and CurrentOutput capability interfaces.
It extends VisaInstrument for SCPI communication.

Example usage:
    # Real hardware
    psu = PSU("TCPIP::192.168.1.101::INSTR")
    with psu:
        psu.set_voltage(5.0)
        psu.enable_output()

    # Simulation
    psu = PSU("TCPIP::192.168.1.101::INSTR", simulate=True)
    with psu:
        psu.set_voltage(5.0)
        psu.enable_output()
        v = psu.measure_output_voltage()  # Returns ~5.0V
"""

from decimal import Decimal
from typing import Any

from litmus.capabilities.interfaces import CurrentOutput, VoltageOutput
from litmus.instruments.visa import VisaInstrument


class PSU(VisaInstrument, VoltageOutput, CurrentOutput):
    """Power Supply Unit driver.

    Implements capability interfaces:
    - VoltageOutput: set_voltage(), enable_output(), disable_output()
    - CurrentOutput: set_current(), set_current_limit()

    Supports both real hardware and simulation via VisaInstrument.
    """

    # Default simulation responses
    _default_idn = "Litmus,SimPSU,SN001,1.0"
    _sim_responses: dict[str, str | float] = {
        "MEAS:VOLT?": 0.0,
        "MEAS:CURR?": 0.0,
        "VOLT?": 0.0,
        "CURR?": 0.0,
    }

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ):
        """Initialize PSU.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.101::INSTR")
            simulate: If True, use pyvisa-sim simulation
            sim_config: Simulation configuration:
                - voltage: Default voltage setting/readback (default: 0.0)
                - current: Default current setting/readback (default: 0.0)
                - voltage_limit: Max voltage (default: 30.0)
                - current_limit: Max current (default: 5.0)
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
        self._output_enabled: bool = False
        self._set_voltage: Decimal = Decimal("0")
        self._set_current: Decimal = Decimal("0")

    def _process_sim_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Process sim_config to map friendly names to SCPI responses."""
        processed = dict(config)

        responses = {}
        if "voltage" in config:
            responses["MEAS:VOLT?"] = config["voltage"]
            responses["VOLT?"] = config["voltage"]
        if "current" in config:
            responses["MEAS:CURR?"] = config["current"]
            responses["CURR?"] = config["current"]

        if responses:
            processed["responses"] = {**responses, **processed.get("responses", {})}

        return processed

    def connect(self) -> None:
        """Connect to PSU and read identification."""
        super().connect()
        if self._connected:
            self._idn = self.query("*IDN?")

    @property
    def idn(self) -> str | None:
        """Return instrument identification string."""
        return self._idn

    # -------------------------------------------------------------------------
    # VoltageOutput interface
    # -------------------------------------------------------------------------

    def set_voltage(self, voltage: Decimal) -> None:
        """Set output voltage.

        Args:
            voltage: Voltage in Volts
        """
        self._set_voltage = Decimal(str(voltage))
        self.write(f"VOLT {voltage}")

    def set_voltage_limit(self, limit: Decimal) -> None:
        """Set voltage protection limit.

        Args:
            limit: Voltage limit in Volts
        """
        self.write(f"VOLT:PROT {limit}")

    def enable_output(self, channel: str | None = None) -> None:
        """Enable power supply output.

        Args:
            channel: Optional channel identifier (for multi-channel PSUs)
        """
        if channel:
            self.write(f"OUTP:CHAN{channel} ON")
        else:
            self.write("OUTP ON")
        self._output_enabled = True

    def disable_output(self, channel: str | None = None) -> None:
        """Disable power supply output.

        Args:
            channel: Optional channel identifier (for multi-channel PSUs)
        """
        if channel:
            self.write(f"OUTP:CHAN{channel} OFF")
        else:
            self.write("OUTP OFF")
        self._output_enabled = False

    def measure_output_voltage(self) -> Decimal:
        """Measure actual output voltage.

        Returns:
            Measured voltage in Volts
        """
        response = self.query("MEAS:VOLT?")
        return Decimal(response)

    # -------------------------------------------------------------------------
    # CurrentOutput interface
    # -------------------------------------------------------------------------

    def set_current(self, current: Decimal) -> None:
        """Set output current (for CC mode) or current limit (for CV mode).

        Args:
            current: Current in Amps
        """
        self._set_current = Decimal(str(current))
        self.write(f"CURR {current}")

    def set_current_limit(self, limit: Decimal) -> None:
        """Set current protection limit.

        Args:
            limit: Current limit in Amps
        """
        self.write(f"CURR:PROT {limit}")

    def measure_output_current(self) -> Decimal:
        """Measure actual output current.

        Returns:
            Measured current in Amps
        """
        response = self.query("MEAS:CURR?")
        return Decimal(response)

    # -------------------------------------------------------------------------
    # Additional PSU-specific methods
    # -------------------------------------------------------------------------

    @property
    def output_enabled(self) -> bool:
        """Return whether output is enabled."""
        return self._output_enabled

    def set_ovp(self, voltage: Decimal) -> None:
        """Set over-voltage protection.

        Args:
            voltage: OVP threshold in Volts
        """
        self.write(f"VOLT:PROT {voltage}")

    def set_ocp(self, current: Decimal) -> None:
        """Set over-current protection.

        Args:
            current: OCP threshold in Amps
        """
        self.write(f"CURR:PROT {current}")

    def clear_protection(self) -> None:
        """Clear any tripped protection."""
        self.write("OUTP:PROT:CLE")

    # -------------------------------------------------------------------------
    # Convenience aliases for simpler test API
    # -------------------------------------------------------------------------

    def measure_voltage(self) -> Decimal:
        """Alias for measure_output_voltage()."""
        return self.measure_output_voltage()

    def measure_current(self) -> Decimal:
        """Alias for measure_output_current()."""
        return self.measure_output_current()
