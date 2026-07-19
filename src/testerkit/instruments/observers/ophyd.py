"""Ophyd observer for EPICS-backed devices.

``read()`` returns ``OrderedDict[str, {"value": v, "timestamp": t}]``.
``set()`` returns a Status object.
"""

from __future__ import annotations

from typing import Any

from testerkit.instruments.observer import DriverObserver, InstrumentEventBuilder
from testerkit.instruments.observers.generic import GenericObserver


class OphydObserver(DriverObserver):
    """Ophyd device observer with dict-unpacking read()."""

    observer_protocols = ["ophyd"]

    _silent_methods = frozenset(
        {
            "describe",
            "read_configuration",
            "describe_configuration",
            "summary",
        }
    )

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
            if isinstance(result, dict):
                for key, entry in result.items():
                    val = entry.get("value", entry) if isinstance(entry, dict) else entry
                    self.emit.read(f"{self.role}.{key}", val, method=name)
            else:
                self.emit.read(f"{self.role}.reading", result, method=name)
            return

        if name == "get":
            self.emit.read(f"{self.role}.value", result, method=name)
            return

        if name in ("set", "put"):
            value = args[0] if args else kwargs.get("value")
            self.emit.set(f"{self.role}.setpoint", value, attr="setpoint")
            return

        if name == "trigger":
            self.emit.configure("trigger", kwargs)
            return

        if name == "configure":
            self.emit.configure("configure", kwargs)
            return

        if name in ("stage", "unstage"):
            self.emit.configure(name, {})
            return

        self._generic.on_call(name, args, kwargs, result)
