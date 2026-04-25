"""Same ``FakeDut`` fixture as stage 1.

Nothing has changed here. The DUT is the same 3.3 V buck converter;
the only difference from stage 1 is how the test *records* what it
measured, not how it talks to the DUT.
"""

from __future__ import annotations

import pytest


class FakeDut:
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
