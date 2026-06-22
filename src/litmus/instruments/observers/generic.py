"""Generic observer using prefix-based method classification.

Works for DIY drivers and the Litmus naming convention
(``measure_*``, ``read_*``, ``set_*``, ``configure_*``).

Unrecognized names (no matching prefix, private, lifecycle) are silent.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import DriverObserver, InstrumentEventBuilder
from litmus.models.instrument import ChannelKind

_READ_PREFIXES = ("measure_", "read_", "get_", "query_", "fetch_")
_SET_PREFIXES = ("set_", "write_")
_CONFIGURE_PREFIXES = ("configure_", "setup_", "init_")


_PREFIX_TO_KIND: tuple[tuple[tuple[str, ...], ChannelKind], ...] = (
    (_READ_PREFIXES, ChannelKind.read),
    (_SET_PREFIXES, ChannelKind.set),
    (_CONFIGURE_PREFIXES, ChannelKind.configure),
)


def classify_by_prefix(name: str) -> ChannelKind | None:
    """Classify a method name by prefix, or None if unrecognized."""
    return next(
        (kind for prefixes, kind in _PREFIX_TO_KIND if any(name.startswith(p) for p in prefixes)),
        None,
    )


def strip_prefix(name: str, kind: ChannelKind) -> str:
    """Strip the classification prefix to derive a channel stem."""
    prefixes: dict[ChannelKind, tuple[str, ...]] = {
        ChannelKind.read: _READ_PREFIXES,
        ChannelKind.set: _SET_PREFIXES,
        ChannelKind.configure: _CONFIGURE_PREFIXES,
    }
    for prefix in prefixes.get(kind, ()):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


class GenericObserver(DriverObserver):
    """Prefix-based classification. Works for DIY drivers and Litmus convention."""

    observer_protocols = [
        "generic",
        "yaqc",
        "lakeshore",
        "epics",
        "seabreeze",
        "zhinst",
        "dwfpy",
        "picosdk",
        "pylablib",
    ]

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventBuilder,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)

    def on_call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        if self._should_skip(name):
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
