"""Bench-bringup conftest — defines instrument fixtures directly.

This is the Tier 1 escape hatch: skip station/catalog YAML entirely and
stand up instrument fixtures in five lines. When you're ready for real
traceability (Tier 2), move the driver resolution into a station YAML
and delete these fixtures — the tests don't change.

Real bench use: swap ``MagicMock`` for your PyVISA / PyMeasure driver.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def dmm() -> MagicMock:
    """Bench DMM reading the 3V3 rail. Replace with a real driver."""
    inst = MagicMock()
    inst.measure_dc_voltage.return_value = 3.305
    inst.measure_dc_current.return_value = 0.042
    return inst


@pytest.fixture
def psu() -> MagicMock:
    """Bench PSU providing 5 V input. Replace with a real driver."""
    inst = MagicMock()
    inst.measure_voltage.return_value = 5.01
    inst.measure_current.return_value = 0.12
    return inst
