"""Digital Multimeter (DMM) driver."""

from decimal import Decimal
from typing import Any

from litmus.instruments.base import Instrument, SimulatedBackend, VisaInstrument


class DMM(Instrument[None]):
    """Digital Multimeter driver.

    Provides methods for common DMM measurements including
    DC voltage, DC current, and resistance (2-wire and 4-wire).

    Supports both real hardware and simulation modes.
    """

    # Default simulation configuration
    _default_sim_idn = "Litmus,SimDMM,SN001,1.0"
    _default_sim_responses = {
        "MEAS:VOLT:DC?": "5.0",
        "MEAS:CURR:DC?": "0.1",
        "MEAS:RES?": "1000.0",
        "MEAS:FRES?": "1000.0",
    }

    def __init__(
        self,
        resource: str,
        visa_library: str = "",
        simulated: bool = False,
        sim_values: dict[str, Any] | None = None,
    ):
        """Initialize DMM.

        Args:
            resource: VISA resource string
            visa_library: Path to VISA library or pyvisa-sim config
            simulated: If True, use in-memory simulation
            sim_values: Dict of measurement values for simulation
                       (e.g., {"voltage": 3.3, "current": 0.5, "resistance": 470})
        """
        super().__init__(resource, visa_library, simulated, sim_values)
        self._idn: str | None = None

    def connect(self) -> None:
        """Connect to DMM and read identification."""
        if self.simulated:
            responses = self._build_sim_responses()
            self._sim = SimulatedBackend(
                self.resource,
                idn=self._default_sim_idn,
                responses=responses,
            )
            self._idn = self._sim.connect()
        else:
            self._visa = VisaInstrument(self.resource, self.visa_library)
            self._idn = self._visa.connect()

    def disconnect(self) -> None:
        """Disconnect from DMM."""
        if self._sim:
            self._sim.disconnect()
            self._sim = None
        if self._visa:
            self._visa.disconnect()
            self._visa = None

    def _build_sim_responses(self) -> dict[str, str]:
        """Build response dict from defaults + sim_values overrides."""
        responses = dict(self._default_sim_responses)
        # Map friendly names to SCPI commands
        if "voltage" in self.sim_values:
            responses["MEAS:VOLT:DC?"] = str(self.sim_values["voltage"])
        if "current" in self.sim_values:
            responses["MEAS:CURR:DC?"] = str(self.sim_values["current"])
        if "resistance" in self.sim_values:
            responses["MEAS:RES?"] = str(self.sim_values["resistance"])
            responses["MEAS:FRES?"] = str(self.sim_values["resistance"])
        return responses

    @property
    def _backend(self) -> VisaInstrument | SimulatedBackend:
        """Return active backend (visa or simulated)."""
        if self.simulated:
            if self._sim is None:
                raise RuntimeError("Not connected to DMM")
            return self._sim
        else:
            if self._visa is None:
                raise RuntimeError("Not connected to DMM")
            return self._visa

    @property
    def idn(self) -> str | None:
        """Return instrument identification string."""
        return self._idn

    def measure_dc_voltage(self, range: float | str = "AUTO") -> Decimal:
        """Measure DC voltage.

        Args:
            range: Measurement range in volts, or "AUTO" for auto-ranging

        Returns:
            Measured voltage as Decimal
        """
        if range != "AUTO":
            self._backend.write(f"CONF:VOLT:DC {range}")
        response = self._backend.query("MEAS:VOLT:DC?")
        return Decimal(response)

    def measure_dc_current(self, range: float | str = "AUTO") -> Decimal:
        """Measure DC current.

        Args:
            range: Measurement range in amps, or "AUTO" for auto-ranging

        Returns:
            Measured current as Decimal
        """
        if range != "AUTO":
            self._backend.write(f"CONF:CURR:DC {range}")
        response = self._backend.query("MEAS:CURR:DC?")
        return Decimal(response)

    def measure_resistance(self, range: float | str = "AUTO", four_wire: bool = False) -> Decimal:
        """Measure resistance.

        Args:
            range: Measurement range in ohms, or "AUTO" for auto-ranging
            four_wire: Use 4-wire (Kelvin) measurement if True

        Returns:
            Measured resistance as Decimal
        """
        if range != "AUTO":
            cmd = "CONF:FRES" if four_wire else "CONF:RES"
            self._backend.write(f"{cmd} {range}")
        query_cmd = "MEAS:FRES?" if four_wire else "MEAS:RES?"
        response = self._backend.query(query_cmd)
        return Decimal(response)
