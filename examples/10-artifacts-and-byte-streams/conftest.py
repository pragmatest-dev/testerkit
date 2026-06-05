"""PSU fixture — concrete driver, no mock infrastructure."""

from __future__ import annotations

import pytest
from drivers import PSU


@pytest.fixture(scope="session")
def psu() -> PSU:
    return PSU(resource="TCPIP::192.168.1.101::INSTR")
