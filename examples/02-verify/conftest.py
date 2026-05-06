"""PSU and DMM fixtures — real driver classes, optionally mocked.

Tests call ``psu.set_voltage(...)`` / ``dmm.measure_dc_voltage()`` on
the real ``PSU`` / ``DMM`` driver classes from ``drivers/``. When
``--mock-instruments`` is set, Litmus's ``Mock`` factory wraps each
driver class with explicit return values so the suite runs without
a bench. Without the flag, the fixtures connect to the configured
VISA resources.

Same conditional shape Litmus uses internally (see
``litmus/pytest_plugin.py``). Stage 5 lifts this conditional out
of ``conftest.py`` and into station YAML.
"""

from __future__ import annotations

import pytest
from drivers import DMM, PSU

from litmus.instruments.mocks import Mock


@pytest.fixture(scope="session")
def psu(mock_instruments) -> PSU:
    if mock_instruments:
        return Mock(PSU, measure_voltage=5.0, measure_current=0.042)
    return PSU(resource="TCPIP::192.168.1.101::INSTR")


@pytest.fixture(scope="session")
def dmm(mock_instruments) -> DMM:
    if mock_instruments:
        return Mock(DMM, measure_dc_voltage=3.31, measure_dc_current=0.042)
    return DMM(resource="TCPIP::192.168.1.102::INSTR")
