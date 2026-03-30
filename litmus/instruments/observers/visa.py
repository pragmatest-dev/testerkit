"""VISA/SCPI observer for PyVISA and python-vxi11 raw resources.

Parses SCPI command strings from ``query()``/``write()`` calls to derive
channel names. ``"MEAS:VOLT:DC?"`` → channel stem ``meas_volt_dc``.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import DriverObserver, EventEmitter
from litmus.instruments.observers.generic import GenericObserver


def parse_scpi(cmd: str) -> tuple[str, bool]:
    """Parse a SCPI command string into (channel_stem, is_query).

    >>> parse_scpi("MEAS:VOLT:DC?")
    ('meas_volt_dc', True)
    >>> parse_scpi("VOLT 3.3")
    ('volt', False)
    >>> parse_scpi("*RST")
    ('rst', False)
    """
    parts = cmd.strip().split(None, 1)
    mnemonic = parts[0] if parts else cmd.strip()
    is_query = mnemonic.endswith("?")
    if is_query:
        mnemonic = mnemonic[:-1]
    # Strip leading * for common commands like *RST, *IDN
    if mnemonic.startswith("*"):
        mnemonic = mnemonic[1:]
    stem = mnemonic.replace(":", "_").lower()
    return stem, is_query


_QUERY_METHODS = frozenset({
    "query", "query_ascii_values", "query_binary_values", "ask",
})
_WRITE_METHODS = frozenset({
    "write", "write_ascii_values", "write_binary_values",
})
_RAW_READ_METHODS = frozenset({"read", "read_raw", "read_bytes"})


class VisaObserver(DriverObserver):
    """SCPI string parsing from query()/write() calls."""

    observer_protocols = ["visa"]

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: EventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
        self._generic = GenericObserver(driver_class, role, emit, yaml_overrides)

    def on_call(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any,
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
                stem, is_query = parse_scpi(str(args[0]))
                if is_query:
                    self.emit.read(f"{self.role}.{stem}", result, method=name)
                else:
                    # Value is the second arg or rest of SCPI string
                    parts = str(args[0]).strip().split(None, 1)
                    value = args[1] if len(args) > 1 else (parts[1] if len(parts) > 1 else None)
                    self.emit.set(f"{self.role}.{stem}", value, attr=stem)
            return

        if name in _RAW_READ_METHODS:
            self.emit.read(f"{self.role}.raw_read", result, method=name)
            return

        self._generic.on_call(name, args, kwargs, result)
