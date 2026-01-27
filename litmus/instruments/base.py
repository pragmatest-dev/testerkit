"""Base classes for instrument drivers."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

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


class Instrument(ABC, Generic[ConfigT]):
    """Base class for all instrument types.

    Provides common interface for instrument drivers with
    connection management and context manager support.
    """

    def __init__(self, resource: str, visa_library: str = ""):
        """Initialize instrument.

        Args:
            resource: VISA resource string
            visa_library: Path to VISA library or pyvisa-sim config
        """
        self.resource = resource
        self.visa_library = visa_library
        self._visa: VisaInstrument | None = None

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
