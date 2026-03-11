"""Tektronix observer for tm_devices.

tm_devices wraps SCPI with typed methods and also has a ``.commands.*``
property hierarchy. This observer combines descriptor introspection
with VISA-style SCPI parsing.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import EventEmitter
from litmus.instruments.observers.descriptor import DescriptorObserver
from litmus.instruments.observers.visa import parse_scpi


class TektronixObserver(DescriptorObserver):
    """Hybrid descriptor + SCPI observer for tm_devices."""

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

        if name == "query" and args:
            stem, _ = parse_scpi(str(args[0]))
            self.emit.read(f"{self.role}.{stem}", result, method=name)
            return

        if name == "write" and args:
            stem, is_query = parse_scpi(str(args[0]))
            if is_query:
                self.emit.read(f"{self.role}.{stem}", result, method=name)
            else:
                parts = str(args[0]).strip().split(None, 1)
                value = args[1] if len(args) > 1 else (parts[1] if len(parts) > 1 else None)
                self.emit.set(f"{self.role}.{stem}", value, attr=stem)
            return

        self._fallback_call(name, args, kwargs, result)
