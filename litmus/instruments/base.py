"""Base classes for instrument drivers.

Architecture:
    Instrument (ABC)
    ├── simulate: bool - universal simulation contract
    ├── sim_config: dict - simulation parameters
    └── connect() / disconnect() - lifecycle

    VisaInstrument(Instrument)  [in visa.py]
    ├── Uses pyvisa-sim when simulate=True
    └── Real PyVISA when simulate=False

    Concrete drivers extend protocol base + implement capabilities:
    DMM(VisaInstrument, VoltageInput, CurrentInput, ResistanceInput)
"""

from abc import ABC, abstractmethod
from typing import Any


class Instrument(ABC):
    """Abstract base class for all instruments.

    Every instrument driver inherits from this and supports simulation
    via the universal `simulate=True` contract. How simulation is
    implemented is up to the protocol-specific subclass.

    Protocol families:
    - VisaInstrument: SCPI instruments via pyvisa-sim
    - DaqmxInstrument: NI DAQmx with simulated devices (future)
    - SerialInstrument: Serial devices with custom _sim_* methods (future)
    """

    def __init__(
        self,
        resource: str = "",
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
    ):
        """Initialize instrument.

        Args:
            resource: Connection string (VISA address, COM port, etc.)
            simulate: If True, use simulation backend instead of real hardware
            sim_config: Configuration for simulation (values, noise, etc.)
        """
        self.resource = resource
        self.simulate = simulate
        self.sim_config = sim_config or {}
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        """Connect to the instrument.

        For simulated instruments, this may set up simulation resources.
        For real instruments, this opens the hardware connection.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the instrument.

        Releases any resources held by the connection.
        """
        ...

    def __enter__(self) -> "Instrument":
        """Context manager entry - connects to instrument."""
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit - disconnects from instrument."""
        self.disconnect()
