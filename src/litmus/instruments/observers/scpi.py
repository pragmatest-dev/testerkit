"""SCPI observer for RsInstrument and easy-scpi typed SCPI wrappers.

These libraries wrap SCPI with typed methods like ``query_float()``,
``write_int()``, carrying type information the raw VisaObserver doesn't have.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import DriverObserver, InstrumentEventEmitter
from litmus.instruments.observers.generic import GenericObserver
from litmus.instruments.observers.visa import parse_scpi

_QUERY_METHODS = frozenset(
    {
        "query_str",
        "query_float",
        "query_int",
        "query_bool",
        "query_str_with_opc",
        "query_float_with_opc",
        "query_int_with_opc",
        "query_bool_with_opc",
        "query_bin_block",
        "query_bin_or_ascii_float_list",
        "query",
    }
)
_WRITE_METHODS = frozenset(
    {
        "write_str",
        "write_int",
        "write_float",
        "write_bool",
        "write_str_with_opc",
        "write_int_with_opc",
        "write_float_with_opc",
        "write_bool_with_opc",
        "write_bin_block",
        "write",
    }
)


class ScpiObserver(DriverObserver):
    """Typed SCPI wrapper classification for RsInstrument / easy-scpi."""

    observer_protocols = ["rsinstrument", "easy_scpi"]

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventEmitter,
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

        if name in _QUERY_METHODS:
            if args:
                stem, _ = parse_scpi(str(args[0]))
                self.emit.read(f"{self.role}.{stem}", result, method=name)
            return

        if name in _WRITE_METHODS:
            if args:
                stem, _ = parse_scpi(str(args[0]))
                # For typed write methods, value is typically args[1]
                value = args[1] if len(args) > 1 else None
                if name == "write_bin_block":
                    value = "<binary>"
                self.emit.set(f"{self.role}.{stem}", value, attr=stem)
            return

        self._generic.on_call(name, args, kwargs, result)
