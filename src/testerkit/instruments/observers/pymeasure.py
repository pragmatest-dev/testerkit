"""PyMeasure/InstrumentKit observer using descriptor introspection.

Builds a channel map at construction time by walking the driver class
MRO for properties and descriptors. Emits typed events on property
get/set. Falls back to prefix classification for plain methods.
"""

from __future__ import annotations

from typing import Any

from testerkit.instruments.observer import LIFECYCLE_METHODS, DriverObserver, InstrumentEventBuilder
from testerkit.instruments.observers.generic import GenericObserver
from testerkit.models.instrument import ChannelKind


def _classify_descriptor(attr: Any) -> ChannelKind | None:
    """Classify a single class attribute as a channel kind, or None."""
    if isinstance(attr, property):
        has_get = attr.fget is not None
        has_set = attr.fset is not None
        if has_get and has_set:
            return ChannelKind.control
        if has_get:
            return ChannelKind.read
        if has_set:
            return ChannelKind.set
        return None

    # Skip regular callables (methods, staticmethod, classmethod)
    if callable(attr) or isinstance(attr, (staticmethod, classmethod)):
        return None

    # Duck-typed descriptor protocol (e.g., PyMeasure control/measurement/setting)
    has_get = hasattr(attr, "__get__")
    has_set = hasattr(attr, "__set__")

    if has_get and has_set:
        return ChannelKind.control
    if has_get:
        return ChannelKind.read
    if has_set:
        return ChannelKind.set

    return None


def build_channel_map(
    driver_class: type,
    yaml_overrides: dict[str, str] | None = None,
) -> dict[str, ChannelKind]:
    """Build a channel map from driver class introspection + YAML overrides.

    Walks ``driver_class.__mro__`` and inspects each class's ``__dict__``
    for properties and descriptors.
    """
    channel_map: dict[str, ChannelKind] = {}

    for klass in driver_class.__mro__:
        if klass is object:
            continue
        for name, attr in klass.__dict__.items():
            if name.startswith("_") or name in LIFECYCLE_METHODS or name in channel_map:
                continue
            kind = _classify_descriptor(attr)
            if kind is not None:
                channel_map[name] = kind

    if yaml_overrides:
        for name, kind_str in yaml_overrides.items():
            channel_map[name] = ChannelKind(kind_str)

    return channel_map


class PyMeasureObserver(DriverObserver):
    """Descriptor-based classification for PyMeasure and InstrumentKit.

    Kept as a thin subclass. The shared descriptor logic lives in
    ``DescriptorObserver``; PyMeasureObserver inherits from ``DriverObserver``
    directly to avoid circular imports (``DescriptorObserver`` imports
    ``build_channel_map`` from this module). Behavior is identical.
    """

    observer_protocols = ["pymeasure", "instrumentkit", "instrumental", "ivi", "slave"]

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
        if name not in self._channel_map:
            self._generic.on_call(name, args, kwargs, result)
