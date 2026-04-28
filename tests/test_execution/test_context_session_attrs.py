"""Read-only session attributes on the ``context`` fixture.

``context.run`` / ``context.station`` / ``context.product`` are the
read-only ambient roll-up tests use without taking ``logger`` /
``station_config`` / ``product_context`` as fixture arguments. Each
delegates to a ContextVar getter in :mod:`litmus.execution._state`.

DUT identity intentionally lives at ``context.run.dut`` — a top-level
``context.dut`` would collide with the bare ``dut`` fixture (which is
the live DUT driver). Same reasoning skips ``context.instruments``;
take the ``instruments`` fixture as an argument.
"""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.pytest_plugin
    asyncio_default_fixture_loop_scope = function
    """
)


def test_context_run_returns_active_test_run(pytester: pytest.Pytester) -> None:
    """``context.run`` exposes the active :class:`TestRun`, including ``run.dut``."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_run_attr=textwrap.dedent(
            """
            def test_run_exposes_record(context):
                assert context.run is not None
                assert context.run.dut is not None
                assert context.run.dut.serial == "ABC123"
            """
        )
    )
    result = pytester.runpytest("-v", "--dut-serial=ABC123")
    result.assert_outcomes(passed=1)


def test_context_station_none_in_bringup(pytester: pytest.Pytester) -> None:
    """No station YAML loaded → ``context.station`` is ``None``."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_no_station=textwrap.dedent(
            """
            def test_station_none(context):
                assert context.station is None
            """
        )
    )
    result = pytester.runpytest("-v", "--dut-serial=test")
    result.assert_outcomes(passed=1)


def test_context_station_resolves_when_loaded(pytester: pytest.Pytester) -> None:
    """With ``--station-config``, ``context.station`` exposes the model."""
    pytester.makeini(_INI)
    (pytester.path / "stations").mkdir()
    (pytester.path / "stations" / "alpha.yaml").write_text(
        textwrap.dedent(
            """
            id: alpha
            name: Alpha Bench
            location: Lab A
            instruments: {}
            """
        )
    )
    pytester.makepyfile(
        test_station=textwrap.dedent(
            """
            def test_station_loaded(context):
                assert context.station is not None
                assert context.station.id == "alpha"
                assert context.station.name == "Alpha Bench"
            """
        )
    )
    result = pytester.runpytest("-v", "--dut-serial=test", "--station-config=stations/alpha.yaml")
    result.assert_outcomes(passed=1)


def test_context_product_none_when_no_yaml(pytester: pytest.Pytester) -> None:
    """No product YAML → ``context.product`` is ``None``."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_no_product=textwrap.dedent(
            """
            def test_product_none(context):
                assert context.product is None
            """
        )
    )
    result = pytester.runpytest("-v", "--dut-serial=test")
    result.assert_outcomes(passed=1)


def test_context_product_resolves_when_loaded(pytester: pytest.Pytester) -> None:
    """With ``--spec``, ``context.product`` exposes the :class:`ProductContext`."""
    pytester.makeini(_INI)
    (pytester.path / "products").mkdir()
    (pytester.path / "products" / "widget.yaml").write_text(
        textwrap.dedent(
            """
            id: widget
            name: Widget
            revision: A
            characteristics:
              v_rail:
                function: dc_voltage
                direction: output
                units: V
                pin: TP1
                bands:
                  - value: 3.3
            pins:
              TP1:
                name: TP1
            """
        )
    )
    pytester.makepyfile(
        test_product=textwrap.dedent(
            """
            def test_product_loaded(context):
                assert context.product is not None
                assert context.product.product.id == "widget"
            """
        )
    )
    result = pytester.runpytest("-v", "--dut-serial=test", "--spec=products/widget.yaml")
    result.assert_outcomes(passed=1)
