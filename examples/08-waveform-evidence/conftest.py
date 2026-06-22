"""Fixtures — PSU and Scope. Mocked by default; flip ``mock_instruments`` off to use real hardware.

The Scope mock uses a callable (``synthesize_psu_step_response``) so each
``scope.capture()`` returns a fresh waveform with realistic shape and small
per-call jitter. The PSU mock returns constants — its values aren't the
subject of the example.
"""

from __future__ import annotations

import pytest
from drivers import PSU, Scope, synthesize_psu_step_response

from litmus import Mock


@pytest.fixture(scope="session")
def psu(mock_instruments) -> PSU:
    if mock_instruments:
        return Mock(PSU, measure_voltage=5.0, measure_current=0.042)
    return PSU(resource="TCPIP::192.168.1.101::INSTR")


@pytest.fixture(scope="session")
def scope(mock_instruments) -> Scope:
    if mock_instruments:
        return Mock(Scope, capture=synthesize_psu_step_response)
    return Scope(resource="TCPIP::192.168.1.103::INSTR")
