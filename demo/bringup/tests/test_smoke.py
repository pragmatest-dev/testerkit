"""Tier 0/1 — smoke tests for a brand-new board.

Three escalating patterns, all authored without any product/fixture
YAML:

* ``test_rail_inline`` — no sidecar at all. Inline ``Limit(...)`` via
  ``verify(..., limit=...)`` lets you write a complete checked test
  in one file.
* ``test_rail_sidecar`` — sidecar ``limits:`` block decouples the
  limit from the test source. Same test body, same row shape.
* ``test_current_draw`` — another measurement from the same sidecar
  showing how one YAML block covers every test in the file.

When you graduate to a station + product (Tier 2), you delete this
conftest, swap ``limits: {low/high}`` for
``limits: {characteristic: <id>, tolerance_pct: ...}``, and the test
bodies stay the same.

Run::

    cd demo/bringup
    uv run pytest -v
"""

from __future__ import annotations

from litmus.models.config import Limit


def test_rail_inline(dmm, verify) -> None:
    """No YAML. Limit lives in the test source."""
    verify(
        "v_rail",
        float(dmm.measure_dc_voltage()),
        limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    )


def test_rail_sidecar(dmm, verify) -> None:
    """Same measurement, limit now lives in ``test_smoke.yaml``."""
    verify("v_rail_sidecar", float(dmm.measure_dc_voltage()))


def test_current_draw(psu, verify) -> None:
    """A second measurement sharing the same sidecar."""
    verify("i_in", float(psu.measure_current()))
