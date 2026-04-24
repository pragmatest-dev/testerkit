"""Power Supply (PSU) instrument class.

This defines the interface for a power supply. Use with Mock for testing:

    from drivers import PSU
    from litmus.instruments import Mock

    psu = Mock(PSU, measure_voltage=5.0, measure_current=0.1)
    psu.set_voltage(5.0)
    psu.enable_output()
    print(psu.measure_voltage())  # 5.0
"""


class PSU:
    """Power Supply interface.

    Common implementations:
    - Keysight E36xx series
    - Rigol DP800 series
    - BK Precision 9200 series
    - Siglent SPD series
    """

    def __init__(self, resource: str = ""):
        """Initialize power supply.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.101::INSTR")
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

    # Output control
    def set_voltage(self, voltage: float) -> None:
        """Set output voltage.

        Args:
            voltage: Voltage in Volts.
        """
        raise NotImplementedError

    def set_current(self, current: float) -> None:
        """Set output current (current limit in CV mode).

        Args:
            current: Current in Amps.
        """
        raise NotImplementedError

    def set_current_limit(self, current: float) -> None:
        """Set current limit (alias for set_current in CV mode).

        Args:
            current: Current limit in Amps.
        """
        self.set_current(current)

    def enable_output(self, channel: str | None = None) -> None:
        """Enable output.

        Args:
            channel: Channel to enable (e.g., "CH1"), or None for all.
        """
        raise NotImplementedError

    def disable_output(self, channel: str | None = None) -> None:
        """Disable output.

        Args:
            channel: Channel to disable (e.g., "CH1"), or None for all.
        """
        raise NotImplementedError

    # Readback
    def measure_voltage(self) -> float:
        """Signal actual output voltage.

        Returns:
            Voltage in Volts.
        """
        raise NotImplementedError

    def measure_current(self) -> float:
        """Signal actual output current.

        Returns:
            Current in Amps.
        """
        raise NotImplementedError

    # Protection
    def set_ovp(self, voltage: float) -> None:
        """Set over-voltage protection level.

        Args:
            voltage: OVP threshold in Volts.
        """
        raise NotImplementedError

    def set_ocp(self, current: float) -> None:
        """Set over-current protection level.

        Args:
            current: OCP threshold in Amps.
        """
        raise NotImplementedError
