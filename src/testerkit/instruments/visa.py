"""VISA instrument protocol family.

VisaInstrument is the base class for all SCPI/IEEE 488.2 instruments
that communicate via PyVISA. When simulate=True, it uses pyvisa-sim
for realistic simulation based on the instrument library YAML.

Example usage:
    class DMM(VisaInstrument, VoltageInput, CurrentInput):
        def measure_voltage(self, signal_type=SignalType.DC) -> float:
            return float(self.query("MEAS:VOLT:DC?"))

    # Real hardware
    dmm = DMM("TCPIP::192.168.1.100::INSTR")
    dmm.connect()
    v = dmm.measure_voltage()

    # Simulation
    dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True)
    dmm.connect()
    v = dmm.measure_voltage()  # Returns value from pyvisa-sim
"""

import random
import tempfile
from pathlib import Path
from typing import Any

import pyvisa
from pyvisa.resources import MessageBasedResource

from testerkit.instruments.base import Instrument
from testerkit.instruments.discovery import parse_idn


class VisaInstrument(Instrument):
    """Base class for VISA/SCPI instruments with automatic simulation.

    This is the protocol family base for all SCPI instruments. It extends
    Instrument and provides:

    - Real hardware communication via PyVISA
    - Simulation via pyvisa-sim (auto-generated from instrument library)
    - Standard SCPI methods: write(), query(), read()

    Concrete drivers (DMM, PSU, Scope, etc.) extend this class and
    implement capability interfaces.
    """

    # Class-level simulation defaults (override in subclasses or via sim_config)
    _default_idn: str = "TesterKit,SimulatedVisa,SN001,1.0"
    _sim_responses: dict[str, str | float] = {}

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ):
        """Initialize VISA instrument.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.100::INSTR")
            simulate: If True, use pyvisa-sim instead of real hardware
            sim_config: Simulation configuration with keys like:
                - idn: Custom *IDN? response
                - responses: Dict of SCPI command -> response value
                - noise: Dict of measurement name -> noise percentage
            timeout_ms: Communication timeout in milliseconds
        """
        super().__init__(resource=resource, simulate=simulate, sim_config=sim_config)
        self.timeout_ms = timeout_ms

        self._rm: pyvisa.ResourceManager | None = None
        self._inst: MessageBasedResource | None = None
        self._sim_yaml_path: Path | None = None

    def connect(self) -> None:
        """Connect to instrument (real or simulated).

        For simulated mode, generates a pyvisa-sim configuration file
        and connects to the simulated instrument.
        """
        if self._connected:
            return

        if self.simulate:
            # Generate pyvisa-sim config and connect
            self._sim_yaml_path = self._generate_sim_config()
            self._rm = pyvisa.ResourceManager(f"{self._sim_yaml_path}@sim")
        else:
            # Connect to real hardware
            self._rm = pyvisa.ResourceManager()

        assert self._rm is not None
        # open_resource returns Resource, but VISA/SCPI instruments are MessageBasedResource
        resource = self._rm.open_resource(self.resource)
        assert isinstance(resource, MessageBasedResource)
        self._inst = resource
        self._inst.timeout = self.timeout_ms
        self._inst.write_termination = "\n"
        self._inst.read_termination = "\n"
        self._connected = True

        # All VISA instruments support *IDN? (IEEE 488.2 mandatory)
        # Parse and set identity fields automatically
        try:
            info = parse_idn(self.query("*IDN?"))
            self.manufacturer = info.manufacturer
            self.model = info.model
            self.serial = info.serial
            self.firmware = info.firmware
        except (OSError, ValueError, pyvisa.errors.VisaIOError):
            pass  # Identity fields remain None if query fails

    def disconnect(self) -> None:
        """Disconnect from instrument and clean up resources."""
        if self._inst:
            self._inst.close()
            self._inst = None
        if self._rm:
            self._rm.close()
            self._rm = None

        # Clean up temp sim config file
        if self._sim_yaml_path and self._sim_yaml_path.exists():
            try:
                self._sim_yaml_path.unlink()
            except OSError:
                pass
            self._sim_yaml_path = None

        self._connected = False

    def write(self, command: str) -> None:
        """Send command to instrument.

        Args:
            command: SCPI command string

        Raises:
            RuntimeError: If not connected
        """
        if self._inst is None:
            raise RuntimeError("Not connected to instrument")
        self._inst.write(command)

    def query(self, command: str) -> str:
        """Send command and return response.

        Args:
            command: SCPI query string (typically ends with ?)

        Returns:
            Response string from instrument

        Raises:
            RuntimeError: If not connected
        """
        if self._inst is None:
            raise RuntimeError("Not connected to instrument")
        return self._inst.query(command).strip()

    def read(self) -> str:
        """Read response from instrument.

        Returns:
            Response string

        Raises:
            RuntimeError: If not connected
        """
        if self._inst is None:
            raise RuntimeError("Not connected to instrument")
        return self._inst.read().strip()

    def _generate_sim_config(self) -> Path:
        """Generate pyvisa-sim YAML configuration.

        Creates a temporary YAML file with simulation configuration using
        pyvisa-sim's stateful properties system. This allows:
        - Commands like "VOLT 5.0" to set state
        - Queries like "VOLT?" or "MEAS:VOLT?" to read that state

        Configuration sources (in order of precedence):
        1. Instance sim_config overrides
        2. Class-level _sim_responses (for backwards compatibility)

        Returns:
            Path to generated YAML file
        """
        # Merge class defaults with instance config
        idn = self.sim_config.get("idn", self._default_idn)
        responses = {**self._sim_responses, **self.sim_config.get("responses", {})}

        # Get default values from sim_config
        default_voltage = self.sim_config.get("voltage", 0.0)
        default_current = self.sim_config.get("current", 0.0)

        # Build pyvisa-sim YAML content with stateful properties
        #
        # specs.type must be 'float' so values are stored as float (not string),
        # otherwise "{:f}".format() fails with "Unknown format code 'f' for str"
        #
        # Getter/setter must match actual SCPI queries used by the driver:
        # - PSU.measure_output_voltage() queries "MEAS:VOLT?"
        # - PSU.set_voltage() writes "VOLT {value}"
        yaml_lines = [
            "spec: '1.0'",
            "devices:",
            "  device 1:",
            "    eom:",
            "      TCPIP INSTR:",
            '        q: "\\n"',
            '        r: "\\n"',
            "      GPIB INSTR:",
            '        q: "\\n"',
            '        r: "\\n"',
            "      USB INSTR:",
            '        q: "\\n"',
            '        r: "\\n"',
            "",
            "    properties:",
            "      voltage:",
            f"        default: {default_voltage}",
            "        specs:",
            "          type: float",
            "        getter:",
            '          q: "MEAS:VOLT?"',
            '          r: "{:f}"',
            "        setter:",
            '          q: "VOLT {:f}"',
            "      current:",
            f"        default: {default_current}",
            "        specs:",
            "          type: float",
            "        getter:",
            '          q: "MEAS:CURR?"',
            '          r: "{:f}"',
            "        setter:",
            '          q: "CURR {:f}"',
            "",
            "    dialogues:",
            '      - q: "*IDN?"',
            f'        r: "{idn}"',
            '      - q: "OUTP ON"',
            '      - q: "OUTP OFF"',
            '      - q: "OUTP:PROT:CLE"',
        ]

        # Add any additional static response dialogues
        for cmd, response in responses.items():
            # Skip voltage/current - handled by properties above
            if cmd in ("VOLT?", "CURR?", "MEAS:VOLT?", "MEAS:CURR?"):
                continue
            if isinstance(response, (int, float)):
                response = str(response)
            yaml_lines.append(f'      - q: "{cmd}"')
            yaml_lines.append(f'        r: "{response}"')

        # Add resources section to map resource string to device
        yaml_lines.append("")
        yaml_lines.append("resources:")
        yaml_lines.append(f"  {self.resource}:")
        yaml_lines.append("    device: device 1")

        # Write to temp file
        yaml_content = "\n".join(yaml_lines)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="testerkit_sim_"
        ) as f:
            f.write(yaml_content)
            return Path(f.name)

    def _get_sim_value(self, name: str, default: float = 0.0) -> float:
        """Get a simulated measurement value with optional noise.

        Helper method for concrete drivers implementing measurement methods.

        Args:
            name: Measurement name (e.g., "voltage", "current")
            default: Default value if not configured

        Returns:
            Simulated value as float
        """
        base = float(self.sim_config.get(name, default))
        noise_pct = float(self.sim_config.get("noise", {}).get(name, 0))

        if noise_pct > 0:
            noise = base * noise_pct / 100
            base = base + random.uniform(-noise, noise)

        return round(base, 9)
