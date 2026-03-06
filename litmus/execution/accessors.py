"""Instrument accessors for test functions."""

from __future__ import annotations

from typing import Any

from litmus.instruments.models import InstrumentRecord


def _instrument_not_found(alias: str, target: str, instruments: dict[str, Any]) -> KeyError:
    """Build a KeyError for a missing instrument, listing available roles."""
    available = ", ".join(sorted(instruments)) or "(none)"
    if alias != target:
        return KeyError(
            f"Alias '{alias}' targets '{target}' which is not in "
            f"station instruments. Available: {available}"
        )
    return KeyError(f"No instrument with role '{alias}'. Available: {available}")


class InstrumentAccessor:
    """Callable accessor for instruments by role, with type-based grouping."""

    def __init__(self, instruments: dict[str, Any], records: dict[str, InstrumentRecord]):
        self._instruments = instruments
        self._records = records

    def _current_aliases(self) -> dict[str, str]:
        """Get current step aliases from plugin module."""
        from litmus.execution.plugin import get_current_step_aliases

        return get_current_step_aliases()

    def __call__(self, role: str) -> Any:
        """Get instrument by role name, resolving aliases. Raises KeyError with available roles."""
        aliases = self._current_aliases()
        resolved = aliases.get(role, role)
        if resolved not in self._instruments:
            raise _instrument_not_found(role, resolved, self._instruments)
        return self._instruments[resolved]

    def by_type(self, driver_path: str) -> dict[str, Any]:
        """Get all instruments matching a driver class import path."""
        return {
            role: self._instruments[role]
            for role, record in self._records.items()
            if record.driver == driver_path and role in self._instruments
        }

    def roles(self) -> list[str]:
        """List available instrument role names, including active aliases."""
        aliases = self._current_aliases()
        names = set(self._instruments.keys())
        names.update(aliases.keys())
        return sorted(names)
