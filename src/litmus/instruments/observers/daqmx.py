"""NI-DAQmx observer for task-based data acquisition.

nidaqmx uses a Task-based API: configure channels, start, read/write, stop.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import DriverObserver, InstrumentEventBuilder
from litmus.instruments.observers.generic import GenericObserver

_CHANNEL_ADD_PREFIXES = (
    "add_ai_",
    "add_ao_",
    "add_di_",
    "add_do_",
    "add_ci_",
    "add_co_",
)


class DaqmxObserver(DriverObserver):
    """Task-based DAQ classification for nidaqmx."""

    observer_protocols = ["nidaqmx"]

    _silent_methods = frozenset({"start", "stop", "close", "wait_until_done"})

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventBuilder,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
        self._generic = GenericObserver(driver_class, role, emit, yaml_overrides)

    def on_call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        if self._should_skip(name):
            return

        if name == "read":
            self.emit.read(f"{self.role}.data", result, method=name)
            return
        if name == "write":
            value = args[0] if args else None
            self.emit.set(f"{self.role}.data", value, attr="data")
            return

        # Channel configuration methods
        for prefix in _CHANNEL_ADD_PREFIXES:
            if name.startswith(prefix):
                self.emit.configure(name, kwargs)
                return

        # Timing/trigger configuration
        if name.startswith("cfg_"):
            self.emit.configure(name, kwargs)
            return

        self._generic.on_call(name, args, kwargs, result)
