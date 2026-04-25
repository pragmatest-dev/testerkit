"""Fixtures shared across the vanilla-stage tests.

A ``conftest.py`` at the project root is pytest's mechanism for
sharing fixtures. Anything defined here is available to every test
file without an import. The ``dut`` fixture below is session-scoped,
so one ``FakeDut`` instance is built per ``pytest`` run and reused
across tests — the same pattern you'd use with a real bench.
"""

from __future__ import annotations

import pytest


class FakeDut:
    """Stand-in for a 3.3 V buck converter on a bench.

    Real tests would talk to instruments (PSU + DMM) over VISA.
    This fake returns plausible numbers so the example runs with no
    hardware; later stages replace it with a station + instrument
    catalog.
    """

    def __init__(self) -> None:
        self._vin: float | None = None

    def set_input(self, vin: float) -> None:
        self._vin = vin

    def read_voltage(self) -> float:
        return 3.31

    def read_current(self) -> float:
        return 0.042


@pytest.fixture(scope="session")
def dut() -> FakeDut:
    return FakeDut()
