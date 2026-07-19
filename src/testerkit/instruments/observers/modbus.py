"""Modbus observer for pymodbus and minimalmodbus.

Register-based I/O with typed read/write methods.
"""

from __future__ import annotations

from typing import Any

from testerkit.instruments.observer import DriverObserver, InstrumentEventBuilder
from testerkit.instruments.observers.generic import GenericObserver

_BULK_READ_METHODS = frozenset(
    {
        "read_coils",
        "read_discrete_inputs",
        "read_holding_registers",
        "read_input_registers",
    }
)
_SINGLE_READ_METHODS = frozenset(
    {
        "read_register",
        "read_float",
        "read_long",
        "read_string",
    }
)
_BULK_WRITE_METHODS = frozenset(
    {
        "write_coil",
        "write_coils",
        "write_register",
        "write_registers",
    }
)
_SINGLE_WRITE_METHODS = frozenset(
    {
        "write_float",
        "write_long",
        "write_string",
    }
)


class ModbusObserver(DriverObserver):
    """Register-based classification for pymodbus / minimalmodbus."""

    observer_protocols = ["modbus"]

    _silent_methods = frozenset({"connect", "close"})

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

        if name in _BULK_READ_METHODS:
            addr = args[0] if args else "?"
            self.emit.read(f"{self.role}.{name}", result, method=f"{name}@{addr}")
            return

        if name in _SINGLE_READ_METHODS:
            addr = args[0] if args else "?"
            self.emit.read(f"{self.role}.reg_{addr}", result, method=name)
            return

        if name in _BULK_WRITE_METHODS:
            addr = args[0] if args else "?"
            value = args[1] if len(args) > 1 else None
            self.emit.set(f"{self.role}.{name}", value, attr=f"{name}@{addr}")
            return

        if name in _SINGLE_WRITE_METHODS:
            addr = args[0] if args else "?"
            value = args[1] if len(args) > 1 else None
            self.emit.set(f"{self.role}.reg_{addr}", value, attr=name)
            return

        self._generic.on_call(name, args, kwargs, result)
