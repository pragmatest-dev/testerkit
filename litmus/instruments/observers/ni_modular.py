"""NI modular instrument observer for niscope, nidcpower, nidmm, nifgen, etc.

Session-based API: ``session.read()``, ``session.measure()``,
``session.configure_*()``, ``session.commit()``.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import DriverObserver, EventEmitter
from litmus.instruments.observers.generic import GenericObserver

_READ_METHODS = frozenset({
    "read", "read_waveform", "fetch", "fetch_waveform",
    "fetch_multiple_waveform", "measure",
})
_WRITE_METHODS = frozenset({"write_waveform", "send_software_trigger"})
_CONFIG_METHODS = frozenset({"commit", "abort", "self_test", "self_cal"})


class NiModularObserver(DriverObserver):
    """Session-based classification for NI modular instruments."""

    _silent_methods = frozenset({"initiate", "close", "reset"})

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: EventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
        self._generic = GenericObserver(driver_class, role, emit, yaml_overrides)

    def on_call(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any,
    ) -> None:
        if self._should_skip(name):
            return

        if name in _READ_METHODS:
            self.emit.read(f"{self.role}.{name}", result, method=name)
            return

        if name in _WRITE_METHODS:
            value = args[0] if args else None
            self.emit.set(f"{self.role}.{name}", value, attr=name)
            return

        if name.startswith("configure_"):
            self.emit.configure(name, kwargs)
            return

        if name in _CONFIG_METHODS:
            self.emit.configure(name, kwargs)
            return

        self._generic.on_call(name, args, kwargs, result)
