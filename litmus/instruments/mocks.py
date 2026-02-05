"""Generic mock factory for instrument drivers.

Mock instruments inherit from real drivers for isinstance() checks,
but ALL methods are no-ops unless explicitly configured with return values.

Example usage:
    # Works with any class - define your own instrument interfaces
    class DMM:
        def measure_voltage(self) -> float: pass

    from litmus.instruments import Mock
    dmm = Mock(DMM, measure_voltage=3.3)
    dmm.measure_voltage()  # Returns 3.3

    # Dict case - argument-based lookup (great for SCPI mocking)
    from pyvisa import Resource
    inst = Mock(Resource, query={
        "MEAS:VOLT:DC?": "3.3",
        "MEAS:CURR:DC?": "0.1",
        "*IDN?": "Keithley,2400,SN123,1.0",
    })
    inst.query("MEAS:VOLT:DC?")  # Returns "3.3"

    # Callable case - full control
    inst = Mock(Resource, query=lambda cmd: "3.3" if "VOLT" in cmd else "0.0")

    # Works with PyMeasure instruments
    from pymeasure.instruments.keithley import Keithley2400
    smu = Mock(Keithley2400, voltage=5.0, current=1.5e-6)
"""

from typing import Any, TypeVar, cast

T = TypeVar("T")


def _make_mock_method(value: Any):
    """Create a mock method that handles different value types.

    Args:
        value: The configured mock value. Can be:
            - callable: Called with the method arguments
            - dict: First positional argument used as lookup key
            - other: Returned directly regardless of arguments

    Returns:
        A callable that returns the appropriate value.
    """
    if callable(value):
        # Callable - pass through args
        return value
    elif isinstance(value, dict):
        # Dict - lookup by first argument
        def dict_lookup(*args, **kwargs):
            key = args[0] if args else None
            return value.get(key)
        return dict_lookup
    else:
        # Simple value - ignore args
        return lambda *args, **kwargs: value


def Mock(cls: type[T], **values: Any) -> T:
    """Create a mock of any class.

    The mock inherits from the real class (passes isinstance checks) but
    ALL methods are no-ops. Only explicitly configured values are returned.

    Args:
        cls: The class to mock (DMM, PSU, Keithley2400, any class)
        **values: Name-value pairs for methods/properties. Values can be:
            - Simple value: Always returned (e.g., measure_voltage=3.3)
            - Dict: First arg is lookup key (e.g., query={"*IDN?": "..."})
            - Callable: Called with method args (e.g., query=lambda cmd: ...)

    Returns:
        Mock instance that passes isinstance(mock, cls)

    Example:
        # Simple values
        dmm = Mock(DMM, measure_voltage=3.3)
        dmm.measure_voltage()  # Returns 3.3

        # Dict for SCPI mocking
        inst = Mock(Resource, query={"MEAS:VOLT?": "3.3", "MEAS:CURR?": "0.1"})
        inst.query("MEAS:VOLT?")  # Returns "3.3"

        # Callable for complex logic
        inst = Mock(Resource, query=lambda cmd: "3.3" if "VOLT" in cmd else "0")
    """
    mock_values = dict(values)
    class_name = f"Mock{cls.__name__}"

    class MockClass(cls):  # type: ignore[valid-type,misc]

        def __init__(self) -> None:
            # Don't call parent __init__ - no hardware
            self._mock_values = dict(mock_values)
            self._connected = False

        def __getattribute__(self, name: str) -> Any:
            # Let internal attributes through
            passthrough = ('set_mock_value', 'mock_values', 'connect', 'disconnect')
            if name.startswith('_') or name in passthrough:
                return object.__getattribute__(self, name)

            mock_vals = object.__getattribute__(self, '_mock_values')

            # Check if it's a configured value
            if name in mock_vals:
                value = mock_vals[name]
                # Check if original is a property or method
                for klass in cls.__mro__:
                    if name in klass.__dict__:
                        attr = klass.__dict__[name]
                        if isinstance(attr, property):
                            # Property - return value directly (no args)
                            if callable(value):
                                return value()
                            elif isinstance(value, dict):
                                return value  # Return the dict itself for properties
                            return value
                        else:
                            # Method - return callable that handles value type
                            return _make_mock_method(value)
                # Not found in class, treat as method
                return _make_mock_method(value)

            # Not configured - return no-op for methods, None for properties
            for klass in cls.__mro__:
                if name in klass.__dict__:
                    attr = klass.__dict__[name]
                    if isinstance(attr, property):
                        return None
                    elif callable(attr):
                        return lambda *args, **kwargs: None

            # Fall back to parent (for things like __class__, etc)
            return object.__getattribute__(self, name)

        def __setattr__(self, name: str, value: Any) -> None:
            if name.startswith('_'):
                object.__setattr__(self, name, value)
            else:
                # Setting any attribute stores in mock_values
                if hasattr(self, '_mock_values'):
                    self._mock_values[name] = value
                else:
                    object.__setattr__(self, name, value)

        def connect(self) -> None:
            self._connected = True

        def disconnect(self) -> None:
            self._connected = False

        def __enter__(self) -> "MockClass":
            self.connect()
            return self

        def __exit__(self, *args: Any) -> None:
            self.disconnect()

        def set_mock_value(self, name: str, value: Any) -> None:
            """Update a mock value after creation."""
            self._mock_values[name] = value

        @property
        def mock_values(self) -> dict[str, Any]:
            """Access all configured mock values."""
            return self._mock_values

    MockClass.__name__ = class_name
    MockClass.__qualname__ = class_name

    return cast(T, MockClass())
