"""Integration tests for the three-layer binding → limit → traceability path.

Exercises ``sidecar.tests.<method>.characteristic`` / ``fixturepoints``
bindings, ``MeasurementLimitConfig`` policy resolution, and the
``_active_point_var`` → row traceability wiring.
"""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.execution.plugin
    """
)


def _write_product(pytester: pytest.Pytester) -> None:
    """Minimal product YAML with one single-pin and one multi-pin char."""
    (pytester.path / "products").mkdir()
    (pytester.path / "products" / "mini.yaml").write_text(
        textwrap.dedent(
            """
            id: mini
            name: Mini Product
            revision: A
            characteristics:
              rail_3v3:
                function: dc_voltage
                direction: output
                units: V
                pin: TP_VOUT
                specs:
                  - value: 3.3
              dropout:
                function: dc_voltage
                direction: output
                units: V
                pins: [TP_VIN, TP_VOUT]
                specs:
                  - value: 1.1
            pins:
              TP_VIN:
                name: TP1
                net: VIN_5V
              TP_VOUT:
                name: TP2
                net: VOUT_3V3
            """
        )
    )


def _write_fixture(pytester: pytest.Pytester) -> None:
    """Minimal fixture wiring both pins to a DMM on distinct channels."""
    (pytester.path / "fixtures").mkdir()
    (pytester.path / "fixtures" / "mini.yaml").write_text(
        textwrap.dedent(
            """
            id: mini_fixture
            name: Mini Fixture
            product_id: mini
            points:
              vin_measure:
                name: vin_measure
                dut_pin: TP_VIN
                net: VIN_5V
                instrument: dmm
                instrument_channel: '1'
                instrument_terminal: hi
              vout_measure:
                name: vout_measure
                dut_pin: TP_VOUT
                net: VOUT_3V3
                instrument: dmm
                instrument_channel: '2'
                instrument_terminal: hi
            """
        )
    )


def test_simple_path_absolute_limits_no_product(pytester: pytest.Pytester) -> None:
    """Sidecar with only absolute ``low``/``high`` — no product, no fixture.

    Limit stamps on the row; ``dut_pin`` / ``fixture_point`` /
    ``spec_ref`` stay null. Demonstrates the "Layer 1 + Layer 2 only"
    simple path from the plan.
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify, context):
                verify("v_rail", 3.31)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
              v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_fixturepoints_binding_iterates_and_stamps_pin(pytester: pytest.Pytester) -> None:
    """``tests.<method>.fixturepoints: [name]`` → ``ctx.points`` iterates and stamps."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify, context):
                seen = []
                for point in context.points:
                    from litmus.execution.plugin import get_active_point
                    seen.append(get_active_point().name)
                    verify("v_rail", 3.30)
                assert seen == ["vout_measure"]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                fixturepoints: [vout_measure]
                limits:
                  v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_characteristic_binding_derives_tolerance_limit(pytester: pytest.Pytester) -> None:
    """Test-level ``characteristic`` + per-label ``tolerance_pct`` → derived limit."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify, context, limits):
                for _ in context.points:
                    assert 3.234 <= limits['v_rail'].low <= 3.234 + 1e-9
                    assert 3.366 - 1e-9 <= limits['v_rail'].high <= 3.366
                    assert limits['v_rail'].spec_id == 'rail_3v3'
                    verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                characteristic: rail_3v3
                limits:
                  v_rail: {tolerance_pct: 2}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_multi_pin_characteristic_iterates_all_points(pytester: pytest.Pytester) -> None:
    """Multi-pin char → ``ctx.points`` yields N points, distinct pins stamped."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_dropout(verify, context):
                from litmus.execution.plugin import get_active_point
                seen = []
                for _ in context.points:
                    seen.append(get_active_point().dut_pin)
                    verify("v_drop", 1.1)
                assert sorted(seen) == ["TP_VIN", "TP_VOUT"]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_dropout:
                characteristic: dropout
                limits:
                  v_drop: {tolerance_abs: 0.1}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_unconsumed_binding_iterator_fails_loudly(pytester: pytest.Pytester) -> None:
    """Declaring a binding but skipping ``ctx.points`` iteration → test fails."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify):
                pass  # binding declared but ctx.points not iterated
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                fixturepoints: [vout_measure]
                limits:
                  v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1, errors=1)


def test_no_binding_ctx_points_is_none(pytester: pytest.Pytester) -> None:
    """Without a binding, ``ctx.points`` stays ``None`` and the test runs normally."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(context, verify):
                assert context.points is None
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
              v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)
