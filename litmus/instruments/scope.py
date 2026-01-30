"""Oscilloscope driver.

The Scope driver implements the WaveformInput capability interface.
It extends VisaInstrument for SCPI communication.

Example usage:
    # Real hardware
    scope = Scope("TCPIP::192.168.1.102::INSTR")
    with scope:
        scope.configure_acquisition(sample_rate=1e9, record_length=10000)
        scope.configure_trigger(source="CH1", level=1.5, slope="rising")
        scope.initiate_acquisition()
        data, x_inc = scope.fetch_waveform("CH1")

    # Simulation
    scope = Scope("TCPIP::192.168.1.102::INSTR", simulate=True)
    with scope:
        data, x_inc = scope.fetch_waveform("CH1")  # Returns simulated waveform
"""

from decimal import Decimal
from typing import Any

from litmus.capabilities.interfaces import WaveformInput
from litmus.instruments.visa import VisaInstrument


class Scope(VisaInstrument, WaveformInput):
    """Oscilloscope driver.

    Implements capability interfaces:
    - WaveformInput: configure_acquisition(), initiate_acquisition(), fetch_waveform()

    Supports both real hardware and simulation via VisaInstrument.
    """

    # Default simulation responses
    _default_idn = "Litmus,SimScope,SN001,1.0"
    _sim_responses: dict[str, str | float] = {
        ":WAV:DATA?": "0.0,0.1,0.2,0.3,0.4,0.5",
        ":WAV:XINC?": 1e-9,
        ":MEAS:FREQ?": 1000.0,
        ":MEAS:VPP?": 1.0,
        ":MEAS:VMAX?": 0.5,
        ":MEAS:VMIN?": -0.5,
    }

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
        timeout_ms: int = 10000,
    ):
        """Initialize Scope.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.102::INSTR")
            simulate: If True, use pyvisa-sim simulation
            sim_config: Simulation configuration:
                - waveform: List of floats for simulated waveform data
                - x_increment: Time between samples in seconds
                - frequency: Measured frequency
                - vpp: Peak-to-peak voltage
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

    def _process_sim_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Process sim_config to map friendly names to SCPI responses."""
        processed = dict(config)

        responses = {}
        if "waveform" in config:
            # Convert list to comma-separated string
            waveform = config["waveform"]
            if isinstance(waveform, list):
                responses[":WAV:DATA?"] = ",".join(str(v) for v in waveform)
        if "x_increment" in config:
            responses[":WAV:XINC?"] = config["x_increment"]
        if "frequency" in config:
            responses[":MEAS:FREQ?"] = config["frequency"]
        if "vpp" in config:
            responses[":MEAS:VPP?"] = config["vpp"]
            responses[":MEAS:VMAX?"] = config["vpp"] / 2
            responses[":MEAS:VMIN?"] = -config["vpp"] / 2

        if responses:
            processed["responses"] = {**responses, **processed.get("responses", {})}

        return processed

    def connect(self) -> None:
        """Connect to Scope and read identification."""
        super().connect()
        if self._connected:
            self._idn = self.query("*IDN?")

    @property
    def idn(self) -> str | None:
        """Return instrument identification string."""
        return self._idn

    # -------------------------------------------------------------------------
    # WaveformInput interface
    # -------------------------------------------------------------------------

    def configure_acquisition(self, sample_rate: Decimal, record_length: int) -> None:
        """Configure acquisition parameters.

        Args:
            sample_rate: Sample rate in samples/second
            record_length: Number of points to acquire
        """
        self.write(f":ACQ:SRAT {sample_rate}")
        self.write(f":ACQ:POIN {record_length}")

    def initiate_acquisition(self) -> None:
        """Start acquisition."""
        self.write(":SING")  # Single acquisition

    def fetch_waveform(self, channel: str) -> tuple[list[float], float]:
        """Fetch waveform data from specified channel.

        Args:
            channel: Channel name (e.g., "CH1", "1")

        Returns:
            Tuple of (waveform_data, x_increment)
            - waveform_data: List of voltage values
            - x_increment: Time between samples in seconds
        """
        # Select channel
        ch_num = channel.replace("CH", "").replace("ch", "")
        self.write(f":WAV:SOUR CHAN{ch_num}")
        self.write(":WAV:FORM ASC")

        # Get data
        data_str = self.query(":WAV:DATA?")
        x_inc_str = self.query(":WAV:XINC?")

        # Parse data
        try:
            data = [float(v) for v in data_str.split(",")]
        except ValueError:
            data = [0.0]

        try:
            x_inc = float(x_inc_str)
        except ValueError:
            x_inc = 1e-9

        return data, x_inc

    def configure_trigger(self, source: str, level: Decimal, slope: str) -> None:
        """Configure trigger.

        Args:
            source: Trigger source (e.g., "CH1")
            level: Trigger level in volts
            slope: Trigger slope ("rising", "falling", "either")
        """
        ch_num = source.replace("CH", "").replace("ch", "")
        self.write(f":TRIG:SOUR CHAN{ch_num}")
        self.write(f":TRIG:LEV {level}")

        slope_map = {"rising": "POS", "falling": "NEG", "either": "EITH"}
        self.write(f":TRIG:SLOP {slope_map.get(slope.lower(), 'POS')}")

    # -------------------------------------------------------------------------
    # Additional measurement methods
    # -------------------------------------------------------------------------

    def measure_frequency(self, channel: str = "CH1") -> Decimal:
        """Measure frequency on a channel.

        Args:
            channel: Channel name

        Returns:
            Measured frequency in Hz
        """
        ch_num = channel.replace("CH", "").replace("ch", "")
        self.write(f":MEAS:SOUR CHAN{ch_num}")
        response = self.query(":MEAS:FREQ?")
        return Decimal(response)

    def measure_vpp(self, channel: str = "CH1") -> Decimal:
        """Measure peak-to-peak voltage on a channel.

        Args:
            channel: Channel name

        Returns:
            Peak-to-peak voltage in volts
        """
        ch_num = channel.replace("CH", "").replace("ch", "")
        self.write(f":MEAS:SOUR CHAN{ch_num}")
        response = self.query(":MEAS:VPP?")
        return Decimal(response)

    def auto_scale(self) -> None:
        """Run auto-scale to automatically configure display."""
        self.write(":AUT")
