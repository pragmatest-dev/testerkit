"""Stage 12 — multi-site parallel testing.

``fixtures/dual_site_bench.yaml`` declares 2 named sites (``left``,
``right``), so this fixture ``is_multi_site``. A bare ``pytest`` here
doesn't run this test once — it spawns two parallel site subprocesses
(the **orchestrator** dispatches, each **worker** runs the full test
session against its own site). Each worker gets its site's own
``connections`` (``fixture_config`` flattens ``sites[site_index]`` for
the resolved site — see ``pytest_plugin/__init__.py::fixture_config``),
so the same test body below runs unmodified on both sites even though
``left`` and ``right`` wire to different ``dmm`` / ``psu`` channels.

Same measure/verify style as examples/06-station-catalog: iterate
``context.connections`` (this site's wiring) and call the shared
station instruments (``psu``, ``dmm``) exactly as a single-UUT test
would — the site-specific channel routing lives in the fixture YAML,
never in the test body.
"""

from __future__ import annotations


def test_vout_within_spec(verify, psu, dmm, context) -> None:
    """5 V PSU input -> ~3.3 V DMM readback, on this site's connections."""
    psu.set_voltage(5.0)
    psu.set_current(0.5)
    for _ in context.connections:
        verify("vout", dmm.measure_dc_voltage())
