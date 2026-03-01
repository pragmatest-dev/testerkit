"""Digital Multimeter (DMM) instrument class.

This defines the interface for a DMM. Use with Mock for testing:

    from demo.drivers import DMM
    from litmus.instruments import Mock

    dmm = Mock(DMM, measure_dc_voltage=3.3, measure_dc_current=0.1)
    print(dmm.measure_dc_voltage())  # 3.3
"""


class DMM:
    """Digital Multimeter interface.

    Common implementations:
    - Keysight 34401A, 34461A
    - Keithley 2000, 2100
    - Fluke 8845A
    """

    def __init__(self, resource: str = ""):
        """Initialize DMM.

        Args:
            resource: VISA resource string (e.g., "GPIB::22::INSTR")
        """
        self.resource = resource
        self._connected = False

    def connect(self) -> None:
        """Connect to the instrument."""
        self._connected = True

    def disconnect(self) -> None:
        """Disconnect from the instrument."""
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # Voltage measurements
    def measure_dc_voltage(self) -> float:
        """Signal DC voltage.

        Returns:
            Voltage in Volts.
        """
        raise NotImplementedError

    def measure_ac_voltage(self) -> float:
        """Signal AC voltage (RMS).

        Returns:
            Voltage in Volts RMS.
        """
        raise NotImplementedError

    def measure_voltage(self) -> float:
        """Signal voltage (alias for measure_dc_voltage).

        Returns:
            Voltage in Volts.
        """
        return self.measure_dc_voltage()

    # Current measurements
    def measure_dc_current(self) -> float:
        """Signal DC current.

        Returns:
            Current in Amps.
        """
        raise NotImplementedError

    def measure_ac_current(self) -> float:
        """Signal AC current (RMS).

        Returns:
            Current in Amps RMS.
        """
        raise NotImplementedError

    def measure_current(self) -> float:
        """Signal current (alias for measure_dc_current).

        Returns:
            Current in Amps.
        """
        return self.measure_dc_current()

    # Resistance measurements
    def measure_resistance(self) -> float:
        """Signal 2-wire resistance.

        Returns:
            Resistance in Ohms.
        """
        raise NotImplementedError

    def measure_4wire_resistance(self) -> float:
        """Signal 4-wire (Kelvin) resistance.

        Returns:
            Resistance in Ohms.
        """
        raise NotImplementedError

    # Configuration
    def configure_voltage_range(self, range_val: float | str) -> None:
        """Configure voltage measurement range.

        Args:
            range_val: Range in Volts, or "AUTO" for autoranging.
        """
        raise NotImplementedError

    def configure_current_range(self, range_val: float | str) -> None:
        """Configure current measurement range.

        Args:
            range_val: Range in Amps, or "AUTO" for autoranging.
        """
        raise NotImplementedError
