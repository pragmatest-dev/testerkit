"""Motion controller observer for thorlabs_apt, zaber-motion, pipython, pylablib.

Motion controllers share a common vocabulary: position, velocity, move, home.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import EventEmitter
from litmus.instruments.observers.descriptor import DescriptorObserver

_MOVE_METHODS = frozenset({
    "move_to", "move_absolute", "move_by", "move_relative", "move_home", "home",
})
_SILENT_METHODS = frozenset({"wait_move", "is_in_motion", "stop"})


class MotionObserver(DescriptorObserver):
    """Motion controller observer with position/velocity awareness."""

    observer_protocols = ["motion"]

    _silent_methods = _SILENT_METHODS

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: EventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)

    def on_call(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any,
    ) -> None:
        if self._should_skip(name):
            return

        if name in _MOVE_METHODS:
            value = args[0] if args else None
            self.emit.set(f"{self.role}.position", value, attr="position")
            return

        if name == "get_position":
            self.emit.read(f"{self.role}.position", result, method=name)
            return

        if name in ("set_velocity", "set_acceleration"):
            stem = name[4:]  # strip "set_"
            value = args[0] if args else None
            self.emit.set(f"{self.role}.{stem}", value, attr=stem)
            return

        self._fallback_call(name, args, kwargs, result)
