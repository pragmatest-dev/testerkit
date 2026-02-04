"""Generic mock factory for instrument drivers.

Mock instruments inherit from real drivers for isinstance() checks,
but ALL methods are no-ops unless explicitly configured with return values.

Example usage:
    from litmus.instruments import DMM, PSU
    from litmus.instruments.mocks import Mock

    # Create mocks - only configured methods return values
    dmm = Mock(DMM, measure_voltage=3.3, measure_current=0.1)

    dmm.measure_voltage()  # Returns 3.3
    dmm.measure_resistance()  # Returns None (not configured)
    dmm.configure_voltage_range(10)  # Does nothing (no-op)

    # Works with any class, including PyMeasure instruments
    from pymeasure.instruments.keithley import Keithley2400
    smu = Mock(Keithley2400, current=1.5e-6, voltage=5.0)
"""

from typing import Any, TypeVar

T = TypeVar("T")


def Mock(cls: type[T], **values: Any) -> T:
    """Create a mock of any class.

    The mock inherits from the real class (passes isinstance checks) but
    ALL methods are no-ops. Only explicitly configured values are returned.

    Args:
        cls: The class to mock (DMM, PSU, Keithley2400, any class)
        **values: Name-value pairs. Methods/properties return these values.

    Returns:
        Mock instance that passes isinstance(mock, cls)

    Example:
        dmm = Mock(DMM, measure_voltage=3.3)
        dmm.measure_voltage()  # Returns 3.3
        dmm.configure_range(10)  # No-op, returns None
        isinstance(dmm, DMM)  # True
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
            if name.startswith('_') or name in ('set_mock_value', 'mock_values', 'connect', 'disconnect'):
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
                            # Property - return value directly
                            return value
                        else:
                            # Method - return callable that returns value
                            return lambda *args, **kwargs: value
                # Not found in class, treat as method
                return lambda *args, **kwargs: value

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

    return MockClass()
