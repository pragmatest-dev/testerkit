"""Digital Multimeter (DMM) driver."""

from decimal import Decimal

from litmus.instruments.base import Instrument, VisaInstrument


class DMM(Instrument[None]):
    """Digital Multimeter driver.

    Provides methods for common DMM measurements including
    DC voltage, DC current, and resistance (2-wire and 4-wire).
    """

    def __init__(self, resource: str, visa_library: str = ""):
        """Initialize DMM.

        Args:
            resource: VISA resource string
            visa_library: Path to VISA library or pyvisa-sim config
        """
        super().__init__(resource, visa_library)
        self._idn: str | None = None

    def connect(self) -> None:
        """Connect to DMM and read identification."""
        self._visa = VisaInstrument(self.resource, self.visa_library)
        self._idn = self._visa.connect()

    def disconnect(self) -> None:
        """Disconnect from DMM."""
        if self._visa:
            self._visa.disconnect()
            self._visa = None

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
        if self._visa is None:
            raise RuntimeError("Not connected to DMM")
        if range != "AUTO":
            self._visa.write(f"CONF:VOLT:DC {range}")
        response = self._visa.query("MEAS:VOLT:DC?")
        return Decimal(response)

    def measure_dc_current(self, range: float | str = "AUTO") -> Decimal:
        """Measure DC current.

        Args:
            range: Measurement range in amps, or "AUTO" for auto-ranging

        Returns:
            Measured current as Decimal
        """
        if self._visa is None:
            raise RuntimeError("Not connected to DMM")
        if range != "AUTO":
            self._visa.write(f"CONF:CURR:DC {range}")
        response = self._visa.query("MEAS:CURR:DC?")
        return Decimal(response)

    def measure_resistance(self, range: float | str = "AUTO", four_wire: bool = False) -> Decimal:
        """Measure resistance.

        Args:
            range: Measurement range in ohms, or "AUTO" for auto-ranging
            four_wire: Use 4-wire (Kelvin) measurement if True

        Returns:
            Measured resistance as Decimal
        """
        if self._visa is None:
            raise RuntimeError("Not connected to DMM")
        if range != "AUTO":
            cmd = "CONF:FRES" if four_wire else "CONF:RES"
            self._visa.write(f"{cmd} {range}")
        query_cmd = "MEAS:FRES?" if four_wire else "MEAS:RES?"
        response = self._visa.query(query_cmd)
        return Decimal(response)
