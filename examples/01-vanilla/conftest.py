"""PSU and DMM fixtures — real driver classes, optionally mocked.

Tests are written against the real ``DMM`` / ``PSU`` driver classes
in ``drivers/``. When ``--mock-instruments`` is set (or
``mock_instruments: true`` in ``litmus.yaml``), Litmus's ``Mock``
factory wraps the real driver class with explicit return values so
the tests run end-to-end without a bench attached. Without the
flag, the fixtures connect to the configured VISA resources.

This is the same conditional shape the Litmus plugin uses
internally (``litmus/pytest_plugin.py``) — the test code calls
the real driver methods regardless. Stage 5 lifts this conditional
out of ``conftest.py`` into station YAML.
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
