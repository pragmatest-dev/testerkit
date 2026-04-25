"""Same ``FakeDut`` fixture as earlier stages."""

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
