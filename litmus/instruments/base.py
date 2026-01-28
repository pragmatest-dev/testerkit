"""Base classes for instrument drivers."""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

import pyvisa

ConfigT = TypeVar("ConfigT")


class VisaInstrument:
    """Low-level VISA communication wrapper.

    Handles connection management and basic SCPI communication
    for VISA-compatible instruments.
    """

    def __init__(
        self,
        resource: str,
        visa_library: str = "",
        timeout_ms: int = 5000,
    ):
        """Initialize VISA instrument wrapper.

        Args:
            resource: VISA resource string (e.g., "TCPIP::192.168.1.100::INSTR")
            visa_library: Path to VISA library or pyvisa-sim config file
            timeout_ms: Communication timeout in milliseconds
        """
        self.resource = resource
        self.visa_library = visa_library
        self.timeout_ms = timeout_ms
        self._rm: pyvisa.ResourceManager | None = None
        self._inst: pyvisa.resources.Resource | None = None

    def connect(self) -> str:
        """Connect to instrument and return *IDN? response."""
        self._rm = pyvisa.ResourceManager(self.visa_library)
        self._inst = self._rm.open_resource(self.resource)
        self._inst.timeout = self.timeout_ms
        # Set standard SCPI message terminators
        self._inst.write_termination = "\n"
        self._inst.read_termination = "\n"
        return self._inst.query("*IDN?").strip()

    def disconnect(self) -> None:
        """Disconnect from instrument."""
        if self._inst:
            self._inst.close()
            self._inst = None
        if self._rm:
            self._rm.close()
            self._rm = None

    def write(self, command: str) -> None:
        """Send command to instrument."""
        if self._inst is None:
            raise RuntimeError("Not connected to instrument")
        self._inst.write(command)

    def query(self, command: str) -> str:
        """Send command and return response."""
        if self._inst is None:
            raise RuntimeError("Not connected to instrument")
        return self._inst.query(command).strip()

    def __enter__(self) -> "VisaInstrument":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()


class SimulatedBackend:
    """In-memory simulation backend (no PyVISA required).

    Provides the same interface as VisaInstrument but returns
    configurable simulated responses instead of communicating
    with real hardware.
    """

    def __init__(
        self,
        resource: str,
        idn: str = "Litmus,Simulated,SN001,1.0",
        responses: dict[str, str] | None = None,
    ):
        """Initialize simulated backend.

        Args:
            resource: Simulated resource string (for identification)
            idn: Response to return for *IDN? query
            responses: Dict mapping SCPI commands to responses
        """
        self.resource = resource
        self._idn = idn
        self._responses = responses or {}
        self._connected = False

    def connect(self) -> str:
        """Simulate connection and return IDN string."""
        self._connected = True
        return self._idn

    def disconnect(self) -> None:
        """Simulate disconnection."""
        self._connected = False

    def write(self, command: str) -> None:
        """Simulate write command (ignored in simulation)."""
        if not self._connected:
            raise RuntimeError("Not connected to instrument")
        # Simulation ignores write commands (configuration, etc.)

    def query(self, command: str) -> str:
        """Return simulated response for command."""
        if not self._connected:
            raise RuntimeError("Not connected to instrument")
        return self._responses.get(command, "0")

    def __enter__(self) -> "SimulatedBackend":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()


class Instrument(ABC, Generic[ConfigT]):
    """Base class for all instrument types.

    Provides common interface for instrument drivers with
    connection management and context manager support.

    Supports both real hardware (via PyVISA) and simulation modes.
    """

    # Class-level defaults for simulation (override in subclasses)
    _default_sim_idn: str = "Litmus,Simulated,SN001,1.0"
    _default_sim_responses: dict[str, str] = {}

    def __init__(
        self,
        resource: str,
        visa_library: str = "",
        simulated: bool = False,
        sim_values: dict[str, Any] | None = None,
    ):
        """Initialize instrument.

        Args:
            resource: VISA resource string
            visa_library: Path to VISA library or pyvisa-sim config
            simulated: If True, use in-memory simulation instead of real hardware
            sim_values: Dict of measurement names to simulated values (e.g., {"voltage": 3.3})
        """
        self.resource = resource
        self.visa_library = visa_library
        self.simulated = simulated
        self.sim_values = sim_values or {}
        self._visa: VisaInstrument | None = None
        self._sim: SimulatedBackend | None = None

    @abstractmethod
    def connect(self) -> None:
        """Connect to instrument."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from instrument."""
        pass

    def __enter__(self) -> "Instrument[ConfigT]":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
