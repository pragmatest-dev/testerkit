"""Observer registry for driver-library-specific event interpretation."""

from litmus.instruments.observers.registry import (
    detect_protocol,
    get_observer_class,
    register_observer,
)

__all__ = ["detect_protocol", "get_observer_class", "register_observer"]
