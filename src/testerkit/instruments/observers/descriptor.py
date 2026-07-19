"""Base observer for descriptor-based driver libraries.

Provides shared ``on_getattr``/``on_setattr``/``on_call`` for observers
that use ``build_channel_map()`` (PyMeasure, Lantz, Motion, Tektronix, QCodes).
"""

from __future__ import annotations

from typing import Any

from testerkit.instruments.observer import DriverObserver, InstrumentEventBuilder
from testerkit.instruments.observers.generic import GenericObserver
from testerkit.instruments.observers.pymeasure import build_channel_map
from testerkit.models.instrument import ChannelKind


class DescriptorObserver(DriverObserver):
    """Descriptor introspection + prefix fallback.

    Subclasses can override ``on_call`` to handle library-specific methods
    before falling through to ``_fallback_call()`` for prefix classification.
    """

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventBuilder,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
        self._channel_map = build_channel_map(driver_class, yaml_overrides)
        self._generic = GenericObserver(driver_class, role, emit, yaml_overrides)

    def on_getattr(self, name: str, value: Any) -> Any:
        if name in self._channel_map:
            kind = self._channel_map[name]
            if kind in (ChannelKind.read, ChannelKind.control):
                self.emit.read(f"{self.role}.{name}", value, method=name)
        return value

    def on_setattr(self, name: str, value: Any) -> None:
        if name in self._channel_map:
            kind = self._channel_map[name]
            if kind in (ChannelKind.set, ChannelKind.control):
                self.emit.set(f"{self.role}.{name}", value, attr=name)

    def on_call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        if self._should_skip(name):
            return
        if name not in self._channel_map:
            self._fallback_call(name, args, kwargs, result)

    def _fallback_call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        """Delegate unrecognized methods to prefix-based classification."""
        self._generic.on_call(name, args, kwargs, result)
