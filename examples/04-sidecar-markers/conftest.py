"""Same conditional-mock conftest as stages 2-3 — real ``PSU`` / ``DMM``
driver classes from ``drivers/``, mocked when ``--mock-instruments``
is set."""

from __future__ import annotations

import pytest
from drivers import DMM, PSU

from litmus.instruments import Mock


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
