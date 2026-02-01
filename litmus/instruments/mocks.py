"""Generic mock factory for instrument drivers.

Mock instruments inherit from real drivers, ensuring interface consistency.
All measurement methods work automatically because they call query(), which
is overridden to return configured values.

Example usage:
    from litmus.instruments import DMM, PSU, ELoad
    from litmus.instruments.mocks import Mock

    # Create mocks with friendly parameter names
    dmm = Mock(DMM, measure_voltage=3.3, measure_current=0.1)
    psu = Mock(PSU, measure_voltage=5.0, measure_current=0.5)
    eload = Mock(ELoad, measure_voltage=12.0, measure_power=6.0)

    # Use exactly like real instruments
    with dmm:
        v = dmm.measure_voltage()  # Returns Decimal("3.3")

    # Update mock values dynamically
    dmm.set_mock_value("measure_voltage", 5.0)

    # Or provide raw SCPI responses for custom commands
    dmm = Mock(DMM, responses={"MEAS:VOLT:DC?": "3.3"})
"""

from typing import Any, ClassVar, TypeVar

from litmus.instruments.base import Instrument

T = TypeVar("T", bound=Instrument)

# SCPI command mappings for friendly parameter names
# Maps method name -> list of SCPI commands that should return that value
_SCPI_MAPPINGS: dict[type, dict[str, list[str]]] = {}


def _register_scpi_mapping(cls: type, mapping: dict[str, list[str]]) -> None:
    """Register SCPI mapping for an instrument class."""
    _SCPI_MAPPINGS[cls] = mapping


def _get_scpi_mapping(cls: type) -> dict[str, list[str]]:
    """Get SCPI mapping for an instrument class, checking parent classes."""
    for klass in cls.__mro__:
        if klass in _SCPI_MAPPINGS:
            return _SCPI_MAPPINGS[klass]
    return {}


# Register mappings for standard instruments
# Import here to avoid circular imports at module level
def _register_standard_mappings() -> None:
    """Register SCPI mappings for standard instrument types."""
    # Lazy import to avoid circular dependency
    from litmus.instruments.dmm import DMM
    from litmus.instruments.eload import ELoad
    from litmus.instruments.psu import PSU

    _register_scpi_mapping(
        DMM,
        {
            # Method names
            "measure_voltage": ["MEAS:VOLT:DC?", "MEAS:VOLT:AC?"],
            "measure_current": ["MEAS:CURR:DC?", "MEAS:CURR:AC?"],
            "measure_resistance": ["MEAS:RES?", "MEAS:FRES?"],
            "measure_frequency": ["MEAS:FREQ?"],
            "measure_period": ["MEAS:PER?"],
            # Aliases (for station config compatibility)
            "voltage": ["MEAS:VOLT:DC?", "MEAS:VOLT:AC?"],
            "current": ["MEAS:CURR:DC?", "MEAS:CURR:AC?"],
            "resistance": ["MEAS:RES?", "MEAS:FRES?"],
            "frequency": ["MEAS:FREQ?"],
        },
    )

    _register_scpi_mapping(
        PSU,
        {
            # Method names
            "measure_voltage": ["MEAS:VOLT?", "VOLT?"],
            "measure_current": ["MEAS:CURR?", "CURR?"],
            "measure_output_voltage": ["MEAS:VOLT?", "VOLT?"],
            "measure_output_current": ["MEAS:CURR?", "CURR?"],
            # Aliases (for station config compatibility)
            "voltage": ["MEAS:VOLT?", "VOLT?"],
            "current": ["MEAS:CURR?", "CURR?"],
        },
    )

    _register_scpi_mapping(
        ELoad,
        {
            # Method names
            "measure_voltage": ["MEAS:VOLT?"],
            "measure_current": ["MEAS:CURR?"],
            "measure_power": ["MEAS:POW?"],
            # Aliases (for station config compatibility)
            "voltage": ["MEAS:VOLT?"],
            "current": ["MEAS:CURR?"],
            "power": ["MEAS:POW?"],
        },
    )


class MockMixin:
    """Mixin that provides mock behavior for any VisaInstrument subclass.

    Overrides connect/disconnect to be no-ops and query/write to use mock data.
    """

    _mock_responses: dict[str, str]
    _mock_write_log: list[str]
    _mock_state: dict[str, Any]
    _scpi_mapping: ClassVar[dict[str, list[str]]]

    def _init_mock_state(self, **kwargs: Any) -> None:
        """Initialize mock state from keyword arguments."""
        self._mock_responses = {}
        self._mock_write_log = []
        self._mock_state = {}
        self._connected = False

        # Get SCPI mapping for this class
        scpi_map = _get_scpi_mapping(type(self))

        # Handle raw SCPI responses
        if "responses" in kwargs:
            for cmd, value in kwargs.pop("responses").items():
                self._mock_responses[cmd] = str(value)

        # Map friendly names to SCPI commands
        for name, value in kwargs.items():
            self.set_mock_value(name, value)

        # Store initial responses for reset
        self._initial_mock_responses = dict(self._mock_responses)

    def connect(self) -> None:
        """No-op connect for mock."""
        self._connected = True

    def disconnect(self) -> None:
        """No-op disconnect for mock."""
        self._connected = False

    def query(self, command: str) -> str:
        """Return mock response for SCPI query."""
        return self._mock_responses.get(command, "0.0")

    def write(self, command: str) -> None:
        """Track SCPI write commands and update state."""
        self._mock_write_log.append(command)

        # Parse common state-changing commands
        if command.startswith("VOLT "):
            self._mock_state["voltage_setpoint"] = command.split()[1]
        elif command.startswith("CURR "):
            self._mock_state["current_setpoint"] = command.split()[1]
        elif command == "OUTP ON":
            self._mock_state["output_enabled"] = True
        elif command == "OUTP OFF":
            self._mock_state["output_enabled"] = False
        elif command == "INP ON":
            self._mock_state["load_enabled"] = True
        elif command == "INP OFF":
            self._mock_state["load_enabled"] = False
        elif command.startswith("MODE "):
            self._mock_state["mode"] = command.split()[1]

    def set_mock_value(self, name: str, value: Any) -> None:
        """Set mock return value for a method or SCPI command.

        Args:
            name: Method name (e.g., "measure_voltage") or SCPI command
            value: Value to return (will be converted to string for SCPI)
        """
        scpi_map = _get_scpi_mapping(type(self))

        if name in scpi_map:
            # Map method name to SCPI commands
            for cmd in scpi_map[name]:
                self._mock_responses[cmd] = str(value)
        else:
            # Treat as raw SCPI command
            self._mock_responses[name] = str(value)

    def reset_mock_state(self) -> None:
        """Reset mock to initial state.

        Restores mock responses to values from initial creation,
        clears write log, and clears tracked state.
        """
        self._mock_responses = dict(self._initial_mock_responses)
        self._mock_write_log.clear()
        self._mock_state.clear()

    @property
    def mock_write_log(self) -> list[str]:
        """Return list of SCPI commands that were written."""
        return self._mock_write_log

    @property
    def mock_state(self) -> dict[str, Any]:
        """Return tracked mock state (voltage setpoint, output enabled, etc.)."""
        return self._mock_state


def Mock(instrument_class: type[T], **kwargs: Any) -> T:
    """Create a mock instance of any instrument class.

    The mock inherits from the real instrument class, so it has all the same
    methods and passes isinstance() checks. SCPI query() calls return
    configured mock values instead of communicating with hardware.

    Args:
        instrument_class: The instrument class to mock (e.g., DMM, PSU, ELoad)
        **kwargs: Mock values. Use method names for friendly API:
            - measure_voltage=3.3
            - measure_current=0.1
            Or use 'responses' dict for raw SCPI:
            - responses={"MEAS:VOLT:DC?": "3.3"}

    Returns:
        Mock instance that behaves like the real instrument

    Example:
        dmm = Mock(DMM, measure_voltage=3.3)
        with dmm:
            v = dmm.measure_voltage()  # Returns Decimal("3.3")
            assert isinstance(dmm, DMM)  # True
            assert isinstance(dmm, VoltageInput)  # True
    """
    # Ensure mappings are registered
    if not _SCPI_MAPPINGS:
        _register_standard_mappings()

    # Create a dynamic subclass that mixes in mock behavior
    class_name = f"Mock{instrument_class.__name__}"

    class MockInstrument(MockMixin, instrument_class):  # type: ignore[valid-type,misc]
        def __init__(self, **init_kwargs: Any) -> None:
            # Skip VisaInstrument.__init__ which tries to set up pyvisa
            # Just call Instrument.__init__ directly
            Instrument.__init__(
                self,
                resource=f"MOCK::{instrument_class.__name__}",
                simulate=True,
            )
            self._init_mock_state(**init_kwargs)

    MockInstrument.__name__ = class_name
    MockInstrument.__qualname__ = class_name

    return MockInstrument(**kwargs)


