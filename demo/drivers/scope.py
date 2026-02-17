"""Oscilloscope (Scope) instrument class.

This defines the interface for an oscilloscope. Use with Mock for testing:

    from demo.drivers import Scope
    from litmus.instruments import Mock

    # Mock with a callable that returns (samples, dt)
    scope = Mock(Scope, fetch_waveform=lambda ch: ([3.3, 3.31, 3.29, 3.3], 1e-6))
    samples, dt = scope.fetch_waveform("CH1")
"""


class Scope:
    """Oscilloscope interface.

    Common implementations:
    - Keysight InfiniiVision/InfiniiScope series
    - Tektronix TDS/MSO series
    - Rigol DS/MSO series
    - Siglent SDS series
    """

    def __init__(self, resource: str = ""):
        """Initialize oscilloscope.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.104::INSTR")
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

    # Acquisition
    def fetch_waveform(self, channel: str) -> tuple[list[float], float]:
        """Fetch waveform data from a channel.

        Args:
            channel: Channel name (e.g., "CH1", "1")

        Returns:
            Tuple of (samples, dt) where:
            - samples: List of voltage values
            - dt: Time between samples in seconds
        """
        pass

    def run(self) -> None:
        """Start continuous acquisition."""
        pass

    def stop(self) -> None:
        """Stop acquisition."""
        pass

    def single(self) -> None:
        """Arm for single acquisition."""
        pass

    # Configuration
    def configure_channel(
        self,
        channel: str,
        scale: float,
        offset: float = 0.0,
        coupling: str = "DC",
    ) -> None:
        """Configure a channel.

        Args:
            channel: Channel name (e.g., "CH1")
            scale: Volts per division
            offset: Vertical offset in Volts
            coupling: "DC", "AC", or "GND"
        """
        pass

    def configure_timebase(self, scale: float, position: float = 0.0) -> None:
        """Configure horizontal timebase.

        Args:
            scale: Seconds per division
            position: Horizontal position in seconds
        """
        pass

    def configure_trigger(
        self,
        source: str,
        level: float,
        slope: str = "rising",
        mode: str = "edge",
    ) -> None:
        """Configure trigger.

        Args:
            source: Trigger source (e.g., "CH1")
            level: Trigger level in Volts
            slope: "rising" or "falling"
            mode: Trigger mode (e.g., "edge", "pulse")
        """
        pass

    # Measurements
    def measure_frequency(self, channel: str) -> float:
        """Signal frequency on a channel.

        Args:
            channel: Channel name

        Returns:
            Frequency in Hz.
        """
        pass

    def measure_vpp(self, channel: str) -> float:
        """Signal peak-to-peak voltage on a channel.

        Args:
            channel: Channel name

        Returns:
            Peak-to-peak voltage in Volts.
        """
        pass

    def measure_vrms(self, channel: str) -> float:
        """Signal RMS voltage on a channel.

        Args:
            channel: Channel name

        Returns:
            RMS voltage in Volts.
        """
        pass
