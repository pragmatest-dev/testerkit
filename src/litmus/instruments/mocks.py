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

from typing import Any, Protocol, TypeVar, cast, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class MockCtrl(Protocol):
    """Mock-specific control surface added by :func:`Mock` on top of the
    real class's interface. Use ``as_mock(instance)`` to access these from
    tests; the factory return type stays as ``T`` so normal driver methods
    keep their real signatures.
    """

    _connected: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def set_mock_value(self, name: str, value: Any) -> None: ...
    @property
    def mock_values(self) -> dict[str, Any]: ...
    def __enter__(self) -> "MockCtrl": ...
    def __exit__(self, *args: Any) -> None: ...


def as_mock(instance: object) -> MockCtrl:
    """Narrow a :func:`Mock` instance to its mock-control surface.

    Tests use this to reach ``set_mock_value`` / ``__enter__`` / ``_connected``
    etc. without fighting the driver's declared type. Raises ``TypeError`` if
    the object isn't a mock (missing ``set_mock_value``).
    """
    if not isinstance(instance, MockCtrl):
        raise TypeError(
            f"{type(instance).__name__} is not a Mock instance "
            "(missing set_mock_value); was it created via Mock(cls, ...)?"
        )
    return instance


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

    def _is_property(name: str) -> bool:
        """Check if name is a property on the mocked class."""
        for klass in cls.__mro__:
            if name in klass.__dict__:
                return isinstance(klass.__dict__[name], property)
        return False

    def _is_class_attr(name: str) -> bool:
        """Check if name exists on the mocked class."""
        return any(name in klass.__dict__ for klass in cls.__mro__)

    def _resolve_configured(name: str, value: Any) -> Any:
        """Return configured mock value, wrapping as method if needed."""
        if _is_property(name):
            if callable(value):
                return value()
            return value
        return _make_mock_method(value)

    _NOOP = lambda *args, **kwargs: None  # noqa: E731
    _SENTINEL = object()

    def _resolve_unconfigured(name: str) -> Any:
        """Return fallback for unconfigured attribute access."""
        if _is_class_attr(name):
            return None if _is_property(name) else _NOOP
        # When cls is object (generic mock), return _NOOP for any attribute
        # not on object itself, so mock.set_voltage() etc. silently succeed
        if cls is object and name not in dir(object):
            return _NOOP
        return _SENTINEL  # Signal caller to use object.__getattribute__

    _PASSTHROUGH = frozenset({"set_mock_value", "mock_values", "connect", "disconnect"})

    class MockClass(cls):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            # Don't call parent __init__ - no hardware
            self._mock_values = dict(mock_values)
            self._connected = False

        def __getattribute__(self, name: str) -> Any:
            if name.startswith("_") or name in _PASSTHROUGH:
                return object.__getattribute__(self, name)

            mock_vals = object.__getattribute__(self, "_mock_values")

            if name in mock_vals:
                return _resolve_configured(name, mock_vals[name])

            result = _resolve_unconfigured(name)
            if result is _SENTINEL:
                return object.__getattribute__(self, name)
            return result

        def __setattr__(self, name: str, value: Any) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
            else:
                self._mock_values[name] = value

        def __delattr__(self, name: str) -> None:
            if name.startswith("_"):
                object.__delattr__(self, name)
            else:
                self._mock_values.pop(name, None)

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
