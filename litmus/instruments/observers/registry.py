"""Observer registry and auto-detection from driver module namespace."""

from __future__ import annotations

from litmus.instruments.observer import DriverObserver

# Maps driver module prefix → observer protocol name.
# Only protocols with a registered observer class belong here.
# Future observers (qcodes, nidaqmx, visa, etc.) are added here
# alongside their observer class in _register_builtins().
_PREFIX_MAP: dict[str, str] = {
    "pymeasure.": "pymeasure",
    "instruments.": "instrumentkit",
}

_observers: dict[str, type[DriverObserver]] = {}


def detect_protocol(driver_class: type) -> str:
    """Detect driver protocol from module namespace."""
    module = driver_class.__module__
    for prefix, protocol in _PREFIX_MAP.items():
        if module.startswith(prefix):
            return protocol
    return "generic"


def get_observer_class(protocol: str) -> type[DriverObserver]:
    """Get the observer class for a protocol. Falls back to generic."""
    return _observers.get(protocol, _observers["generic"])


def register_observer(protocol: str, cls: type[DriverObserver]) -> None:
    """Register an observer class for a protocol."""
    _observers[protocol] = cls


def _register_builtins() -> None:
    """Register built-in observers on first import."""
    from litmus.instruments.observers.generic import GenericObserver
    from litmus.instruments.observers.pymeasure import PyMeasureObserver

    register_observer("generic", GenericObserver)
    register_observer("pymeasure", PyMeasureObserver)
    register_observer("instrumentkit", PyMeasureObserver)


_register_builtins()
