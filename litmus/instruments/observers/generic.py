"""Generic observer using prefix-based method classification.

Works for DIY drivers and the Litmus naming convention
(``measure_*``, ``read_*``, ``set_*``, ``configure_*``).

Unrecognized names (no matching prefix, private, lifecycle) are silent.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.models import ChannelKind
from litmus.instruments.observer import LIFECYCLE_METHODS, DriverObserver, EventEmitter

_READ_PREFIXES = ("measure_", "read_", "get_", "query_", "fetch_")
_SET_PREFIXES = ("set_", "write_")
_CONFIGURE_PREFIXES = ("configure_", "setup_", "init_")


def classify_by_prefix(name: str) -> ChannelKind | None:
    """Classify a method name by prefix, or None if unrecognized."""
    for prefix in _READ_PREFIXES:
        if name.startswith(prefix):
            return ChannelKind.read
    for prefix in _SET_PREFIXES:
        if name.startswith(prefix):
            return ChannelKind.set
    for prefix in _CONFIGURE_PREFIXES:
        if name.startswith(prefix):
            return ChannelKind.configure
    return None


def strip_prefix(name: str, kind: ChannelKind) -> str:
    """Strip the classification prefix to derive a channel stem."""
    prefixes: dict[ChannelKind, tuple[str, ...]] = {
        ChannelKind.read: _READ_PREFIXES,
        ChannelKind.set: _SET_PREFIXES,
        ChannelKind.configure: _CONFIGURE_PREFIXES,
    }
    for prefix in prefixes.get(kind, ()):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


class GenericObserver(DriverObserver):
    """Prefix-based classification. Works for DIY drivers and Litmus convention."""

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: EventEmitter,
        yaml_overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides)

    def on_call(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any,
    ) -> None:
        if name.startswith("_") or name in LIFECYCLE_METHODS:
            return
        kind = classify_by_prefix(name)
        if kind == ChannelKind.read:
            channel = f"{self.role}.{strip_prefix(name, kind)}"
            self.emit.read(channel, result, method=name)
        elif kind == ChannelKind.set:
            channel = f"{self.role}.{strip_prefix(name, kind)}"
            value = args[0] if args else kwargs.get("value")
            self.emit.set(channel, value, attr=strip_prefix(name, kind))
        elif kind == ChannelKind.configure:
            self.emit.configure(name, kwargs)
