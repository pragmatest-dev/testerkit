"""QCodes observer for Parameter-based instruments.

QCodes Parameters are instance-level objects stored in ``instrument.parameters``.
They're callable: ``param()`` reads, ``param(val)`` sets. They also have
explicit ``.get()``/``.set()`` methods.

This observer uses ``driver_instance`` to discover parameter names and
class descriptors for property-based access.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import EventEmitter
from litmus.instruments.observers.descriptor import DescriptorObserver
from litmus.models.instrument import ChannelKind


class QCodesObserver(DescriptorObserver):
    """QCodes Parameter-aware observer."""

    observer_protocols = ["qcodes"]

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: EventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
        # Discover parameter names from instance if available
        self._parameter_names: set[str] = set()
        if driver_instance is not None and hasattr(driver_instance, "parameters"):
            params = driver_instance.parameters
            if isinstance(params, dict):
                self._parameter_names = set(params.keys())

    def on_getattr(self, name: str, value: Any) -> Any:
        # Parameter objects accessed by name — don't emit, user gets the object
        if name in self._parameter_names:
            return value
        # Duck-type fallback for Parameter-like objects not in the known set
        if hasattr(value, "get") and hasattr(value, "set") and callable(value.get):
            return value
        if name in self._channel_map:
            kind = self._channel_map[name]
            if kind in (ChannelKind.read, ChannelKind.control):
                self.emit.read(f"{self.role}.{name}", value, method=name)
        return value

    def on_call(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any,
    ) -> None:
        if self._should_skip(name):
            return
        if name == "snapshot":
            self.emit.configure("snapshot", kwargs)
            return
        self._fallback_call(name, args, kwargs, result)
