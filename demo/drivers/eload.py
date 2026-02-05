"""Electronic Load (ELoad) instrument class.

This defines the interface for an electronic load. Use with Mock for testing:

    from demo.drivers import ELoad
    from litmus.instruments import Mock

    eload = Mock(ELoad, measure_voltage=5.0, measure_current=0.5)
    eload.set_current(0.5)
    eload.enable()
"""


class ELoad:
    """Electronic Load interface.

    Common implementations:
    - BK Precision 8500 series
    - Rigol DL3000 series
    - Chroma 6310 series
    - Siglent SDL series
    """

    def __init__(self, resource: str = ""):
        """Initialize electronic load.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.103::INSTR")
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

    # Load control - Constant Current mode
    def set_current(self, current: float) -> None:
        """Set load current (CC mode).

        Args:
            current: Current in Amps.
        """
        pass

    # Load control - Constant Power mode
    def set_power(self, power: float) -> None:
        """Set load power (CP mode).

        Args:
            power: Power in Watts.
        """
        pass

    # Load control - Constant Resistance mode
    def set_resistance(self, resistance: float) -> None:
        """Set load resistance (CR mode).

        Args:
            resistance: Resistance in Ohms.
        """
        pass

    # Enable/disable
    def enable(self) -> None:
        """Enable the load."""
        pass

    def disable(self) -> None:
        """Disable the load."""
        pass

    def enable_load(self) -> None:
        """Enable the load (alias for enable)."""
        self.enable()

    def disable_load(self) -> None:
        """Disable the load (alias for disable)."""
        self.disable()

    # Readback
    def measure_voltage(self) -> float:
        """Measure input voltage.

        Returns:
            Voltage in Volts.
        """
        pass

    def measure_current(self) -> float:
        """Measure actual load current.

        Returns:
            Current in Amps.
        """
        pass

    def measure_power(self) -> float:
        """Measure actual power dissipation.

        Returns:
            Power in Watts.
        """
        pass
