"""Digital Multimeter (DMM) driver.

The DMM driver implements the VoltageInput, CurrentInput, and ResistanceInput
capability interfaces. It extends VisaInstrument for SCPI communication.

Example usage:
    # Real hardware
    dmm = DMM("TCPIP::192.168.1.100::INSTR")
    with dmm:
        v = dmm.measure_voltage()

    # Simulation
    dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True, sim_config={"voltage": 3.3})
    with dmm:
        v = dmm.measure_voltage()  # Returns ~3.3V
"""

from decimal import Decimal
from typing import Any

from litmus.capabilities.interfaces import (
    CurrentInput,
    FrequencyInput,
    ResistanceInput,
    VoltageInput,
)
from litmus.capabilities.models import SignalType
from litmus.instruments.visa import VisaInstrument


class DMM(VisaInstrument, VoltageInput, CurrentInput, ResistanceInput, FrequencyInput):
    """Digital Multimeter driver.

    Implements capability interfaces:
    - VoltageInput: measure_voltage(), configure_voltage_range()
    - CurrentInput: measure_current(), configure_current_range()
    - ResistanceInput: measure_resistance(), configure_resistance_range()
    - FrequencyInput: measure_frequency(), measure_period()

    Supports both real hardware and simulation via VisaInstrument.
    """

    # Default simulation responses
    _default_idn = "Litmus,SimDMM,SN001,1.0"
    _sim_responses = {
        "MEAS:VOLT:DC?": 0.0,
        "MEAS:VOLT:AC?": 0.0,
        "MEAS:CURR:DC?": 0.0,
        "MEAS:CURR:AC?": 0.0,
        "MEAS:RES?": 1000.0,
        "MEAS:FRES?": 1000.0,
        "MEAS:FREQ?": 1000.0,
        "MEAS:PER?": 0.001,
    }

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ):
        """Initialize DMM.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.100::INSTR")
            simulate: If True, use pyvisa-sim simulation
            sim_config: Simulation configuration:
                - voltage: Default voltage reading (default: 0.0)
                - current: Default current reading (default: 0.0)
                - resistance: Default resistance reading (default: 1000.0)
                - frequency: Default frequency reading (default: 1000.0)
                - noise: Dict of measurement -> noise percentage
            timeout_ms: Communication timeout in milliseconds
        """
        # Map friendly sim_config names to SCPI commands
        processed_config = self._process_sim_config(sim_config or {})
        super().__init__(
            resource=resource,
            simulate=simulate,
            sim_config=processed_config,
            timeout_ms=timeout_ms,
        )
        self._idn: str | None = None

    def _process_sim_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Process sim_config to map friendly names to SCPI responses."""
        processed = dict(config)

        # Build responses dict from friendly names
        responses = {}
        if "voltage" in config:
            responses["MEAS:VOLT:DC?"] = config["voltage"]
            responses["MEAS:VOLT:AC?"] = config["voltage"]
        if "current" in config:
            responses["MEAS:CURR:DC?"] = config["current"]
            responses["MEAS:CURR:AC?"] = config["current"]
        if "resistance" in config:
            responses["MEAS:RES?"] = config["resistance"]
            responses["MEAS:FRES?"] = config["resistance"]
        if "frequency" in config:
            responses["MEAS:FREQ?"] = config["frequency"]
            responses["MEAS:PER?"] = 1.0 / config["frequency"] if config["frequency"] else 0

        if responses:
            processed["responses"] = {**responses, **processed.get("responses", {})}

        return processed

    def connect(self) -> None:
        """Connect to DMM and read identification."""
        super().connect()
        if self._connected:
            self._idn = self.query("*IDN?")

    @property
    def idn(self) -> str | None:
        """Return instrument identification string."""
        return self._idn

    # -------------------------------------------------------------------------
    # VoltageInput interface
    # -------------------------------------------------------------------------

    def measure_voltage(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        """Measure voltage.

        Args:
            signal_type: DC or AC measurement mode

        Returns:
            Measured voltage in Volts
        """
        cmd = "MEAS:VOLT:AC?" if signal_type == SignalType.AC else "MEAS:VOLT:DC?"
        response = self.query(cmd)
        return Decimal(response)

    def configure_voltage_range(self, range_val: Decimal | str) -> None:
        """Configure the voltage measurement range.

        Args:
            range_val: Range value in Volts, or "AUTO" for autoranging
        """
        if str(range_val).upper() == "AUTO":
            self.write("VOLT:RANG:AUTO ON")
        else:
            self.write(f"CONF:VOLT:DC {range_val}")

    def configure_voltage_nplc(self, nplc: Decimal) -> None:
        """Configure voltage integration time in power line cycles.

        Args:
            nplc: Number of power line cycles (0.02 to 100)
        """
        self.write(f"VOLT:NPLC {nplc}")

    # -------------------------------------------------------------------------
    # CurrentInput interface
    # -------------------------------------------------------------------------

    def measure_current(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        """Measure current.

        Args:
            signal_type: DC or AC measurement mode

        Returns:
            Measured current in Amps
        """
        cmd = "MEAS:CURR:AC?" if signal_type == SignalType.AC else "MEAS:CURR:DC?"
        response = self.query(cmd)
        return Decimal(response)

    def configure_current_range(self, range_val: Decimal | str) -> None:
        """Configure the current measurement range.

        Args:
            range_val: Range value in Amps, or "AUTO" for autoranging
        """
        if str(range_val).upper() == "AUTO":
            self.write("CURR:RANG:AUTO ON")
        else:
            self.write(f"CONF:CURR:DC {range_val}")

    # -------------------------------------------------------------------------
    # ResistanceInput interface
    # -------------------------------------------------------------------------

    def measure_resistance(self, four_wire: bool = False) -> Decimal:
        """Measure resistance.

        Args:
            four_wire: If True, use 4-wire (Kelvin) measurement

        Returns:
            Measured resistance in Ohms
        """
        cmd = "MEAS:FRES?" if four_wire else "MEAS:RES?"
        response = self.query(cmd)
        return Decimal(response)

    def configure_resistance_range(self, range_val: Decimal | str) -> None:
        """Configure the resistance measurement range.

        Args:
            range_val: Range value in Ohms, or "AUTO" for autoranging
        """
        if str(range_val).upper() == "AUTO":
            self.write("RES:RANG:AUTO ON")
        else:
            self.write(f"CONF:RES {range_val}")

    # -------------------------------------------------------------------------
    # FrequencyInput interface
    # -------------------------------------------------------------------------

    def measure_frequency(self) -> Decimal:
        """Measure frequency.

        Returns:
            Measured frequency in Hz
        """
        response = self.query("MEAS:FREQ?")
        return Decimal(response)

    def measure_period(self) -> Decimal:
        """Measure period.

        Returns:
            Measured period in seconds
        """
        response = self.query("MEAS:PER?")
        return Decimal(response)

    # -------------------------------------------------------------------------
    # Convenience methods for common DC measurements
    # -------------------------------------------------------------------------

    def measure_dc_voltage(self, range: float | str = "AUTO") -> Decimal:
        """Measure DC voltage with optional range setting.

        Args:
            range: Voltage range in V, or "AUTO" for autoranging (default)

        Returns:
            Measured voltage in Volts
        """
        if range != "AUTO":
            self.configure_voltage_range(Decimal(str(range)))
        return self.measure_voltage(SignalType.DC)

    def measure_dc_current(self, range: float | str = "AUTO") -> Decimal:
        """Measure DC current with optional range setting.

        Args:
            range: Current range in A, or "AUTO" for autoranging (default)

        Returns:
            Measured current in Amps
        """
        if range != "AUTO":
            self.configure_current_range(Decimal(str(range)))
        return self.measure_current(SignalType.DC)
