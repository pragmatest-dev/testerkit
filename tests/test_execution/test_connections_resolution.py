"""Integration tests for the three-layer spec → limit → traceability path.

Exercises ``sidecar.tests.<method>.characteristic`` / ``connections``
markers, ``MeasurementLimitConfig`` policy resolution, and the
``_active_connection_var`` → row traceability wiring.
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
                bands:
                  - value: 3.3
              dropout:
                function: dc_voltage
                direction: output
                units: V
                pins: [TP_VIN, TP_VOUT]
                bands:
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
            connections:
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

    Limit stamps on the row; ``dut_pin`` / ``fixture_connection`` /
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


def test_connections_marker_iterates_and_stamps_pin(pytester: pytest.Pytester) -> None:
    """``tests.<method>.connections: [name]`` → ``ctx.connections`` iterates and stamps."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify, context):
                seen = []
                for conn in context.connections:
                    from litmus.pytest_plugin import get_active_connection
                    seen.append(get_active_connection().name)
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
                connections: {connections: [vout_measure]}
                limits:
                  v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_characteristic_spec_derives_tolerance_limit(pytester: pytest.Pytester) -> None:
    """Test-level ``characteristic`` + per-label ``tolerance_pct`` → derived limit."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify, context, limits):
                for _ in context.connections:
                    assert 3.234 <= limits['v_rail'].low <= 3.234 + 1e-9
                    assert 3.366 - 1e-9 <= limits['v_rail'].high <= 3.366
                    assert limits['v_rail'].characteristic_id == 'rail_3v3'
                    verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                characteristics: [rail_3v3]
                limits:
                  v_rail: {tolerance_pct: 2}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_multi_pin_characteristic_iterates_all_connections(pytester: pytest.Pytester) -> None:
    """Multi-pin char → ``ctx.connections`` yields N connections, distinct pins stamped."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_dropout(verify, context):
                from litmus.pytest_plugin import get_active_connection
                seen = []
                for _ in context.connections:
                    seen.append(get_active_connection().dut_pin)
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
                characteristics: [dropout]
                limits:
                  v_drop: {tolerance_abs: 0.1}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_unconsumed_connections_iterator_fails_loudly(pytester: pytest.Pytester) -> None:
    """Declaring connections but skipping ``ctx.connections`` iteration → test fails."""
    pytester.makeini(_INI)
    _write_product(pytester)
    _write_fixture(pytester)

    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify):
                pass  # connections declared but ctx.connections not iterated
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                connections: {connections: [vout_measure]}
                limits:
                  v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1, errors=1)


def test_no_markers_ctx_connections_is_none(pytester: pytest.Pytester) -> None:
    """Without spec/connections markers, ``ctx.connections`` stays ``None``; test runs normally."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(context, verify):
                assert context.connections is None
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


# ---------------------------------------------------------------------------
# Multi-characteristic relax (cardinality > 1)
# ---------------------------------------------------------------------------


def _write_two_char_product(pytester: pytest.Pytester) -> None:
    """Product with two single-pin chars (rail_3v3 + idle_current)."""
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
                bands:
                  - value: 3.3
              idle_current:
                function: dc_current
                direction: input
                units: A
                pin: TP_VIN
                bands:
                  - value: 0.05
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


def test_multi_char_marker_iterates_union(pytester: pytest.Pytester) -> None:
    """``litmus_characteristics: [a, b]`` → ``ctx.connections`` iterates the union."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                seen = [conn.dut_pin for conn in context.connections]
                assert seen == ["TP_VOUT", "TP_VIN"]   # marker order
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3, idle_current]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_multi_char_default_iterator_stamps_per_connection_char(
    pytester: pytest.Pytester,
) -> None:
    """Default iteration stamps the right ``characteristic_id`` per row."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                seen = []
                for conn in context.connections:
                    if conn.dut_pin == "TP_VOUT":
                        m = verify("v_rail", 3.30)
                    else:
                        m = verify("i_idle", 0.05)
                    seen.append((m.name, m.characteristic_id))
                assert seen == [("v_rail", "rail_3v3"), ("i_idle", "idle_current")]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3, idle_current]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_for_characteristic_narrows_and_pushes_active_char(
    pytester: pytest.Pytester,
) -> None:
    """``for_characteristic(id)`` yields only that char's connections + stamps id."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                for conn in context.connections.for_characteristic("rail_3v3"):
                    assert conn.dut_pin == "TP_VOUT"
                    m = verify("v_rail", 3.30)
                    assert m.characteristic_id == "rail_3v3"
                for conn in context.connections.for_characteristic("idle_current"):
                    assert conn.dut_pin == "TP_VIN"
                    m = verify("i_idle", 0.05)
                    assert m.characteristic_id == "idle_current"
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3, idle_current]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_per_limit_char_not_in_marker_errors(pytester: pytest.Pytester) -> None:
    """Limit names a char not in the marker's list → UsageError at fixture setup."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3]
                limits:
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.stdout.fnmatch_lines(
        ["*UsageError*idle_current*not declared in litmus_characteristics*"]
    )


def test_marker_absent_scope_derived_from_limit_chars(
    pytester: pytest.Pytester,
) -> None:
    """No ``characteristics:`` marker → scope is the union of per-limit chars."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                # Marker is absent; limits self-declare via characteristic:.
                assert sorted(context.characteristics) == ["idle_current", "rail_3v3"]
                seen = []
                for conn in context.connections:
                    seen.append(conn.dut_pin)
                # Order follows limit-listing order.
                assert seen == ["TP_VOUT", "TP_VIN"]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_verify_explicit_characteristic_override(
    pytester: pytest.Pytester,
) -> None:
    """``verify(..., characteristic=<id>)`` stamps that char regardless of ContextVar."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                # Iterate rail_3v3 connections, but explicitly stamp idle_current
                # on one verify to prove the kwarg overrides the ContextVar.
                for conn in context.connections.for_characteristic("rail_3v3"):
                    m = verify("i_idle", 0.05, characteristic="idle_current")
                    assert m.characteristic_id == "idle_current"
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3, idle_current]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_ctx_limits_for_characteristic_filter(pytester: pytest.Pytester) -> None:
    """``ctx.limits.for_characteristic(id)`` returns only that char's labelled limits."""
    pytester.makeini(_INI)
    _write_two_char_product(pytester)
    _write_fixture(pytester)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, verify):
                rail_only = context.limits.for_characteristic("rail_3v3")
                assert set(rail_only.keys()) == {"v_rail"}
                idle_only = context.limits.for_characteristic("idle_current")
                assert set(idle_only.keys()) == {"i_idle"}
                # Default for_characteristic on full ctx.connections to keep the
                # consume-or-fail check happy.
                for _ in context.connections:
                    pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                characteristics: [rail_3v3, idle_current]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  i_idle: {characteristic: idle_current, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_per_function_matching_routes_to_correct_connection(
    pytester: pytest.Pytester,
) -> None:
    """Two connections on the same pin (DC + AC) → resolver picks by ``(pin, function)``."""
    pytester.makeini(_INI)
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
                bands:
                  - value: 3.3
              rail_3v3_ripple:
                function: ac_voltage
                direction: output
                units: V
                pin: TP_VOUT
                bands:
                  - value: 0.05
            pins:
              TP_VOUT:
                name: TP1
                net: VOUT_3V3
            """
        )
    )
    (pytester.path / "fixtures").mkdir()
    (pytester.path / "fixtures" / "mini.yaml").write_text(
        textwrap.dedent(
            """
            id: mini_fixture
            name: Mini Fixture
            product_id: mini
            connections:
              vout_dc:
                name: vout_dc
                dut_pin: TP_VOUT
                instrument: dmm
                instrument_channel: '1'
                function: dc_voltage
              vout_ac:
                name: vout_ac
                dut_pin: TP_VOUT
                instrument: scope
                instrument_channel: '1'
                function: ac_voltage
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(context, verify):
                # rail_3v3 (function=dc_voltage) → vout_dc on dmm
                dc_conns = list(context.connections.for_characteristic("rail_3v3"))
                assert [c.name for c in dc_conns] == ["vout_dc"]
                assert dc_conns[0].instrument == "dmm"
                # rail_3v3_ripple (function=ac_voltage) → vout_ac on scope
                ac_conns = list(context.connections.for_characteristic("rail_3v3_ripple"))
                assert [c.name for c in ac_conns] == ["vout_ac"]
                assert ac_conns[0].instrument == "scope"
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                characteristics: [rail_3v3, rail_3v3_ripple]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
                  ripple: {characteristic: rail_3v3_ripple, tolerance_pct: 20}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)


def test_function_unset_connection_is_fallback(
    pytester: pytest.Pytester,
) -> None:
    """A connection without ``function:`` is the fallback when no functioned alternative exists."""
    pytester.makeini(_INI)
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
                bands:
                  - value: 3.3
            pins:
              TP_VOUT:
                name: TP1
                net: VOUT_3V3
            """
        )
    )
    (pytester.path / "fixtures").mkdir()
    (pytester.path / "fixtures" / "mini.yaml").write_text(
        textwrap.dedent(
            """
            id: mini_fixture
            name: Mini Fixture
            product_id: mini
            connections:
              vout_legacy:
                name: vout_legacy
                dut_pin: TP_VOUT
                instrument: dmm
                instrument_channel: '1'
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(context, verify):
                conns = list(context.connections)
                # Function-unset connection is the fallback when there's
                # no function-specific alternative for the char.
                assert [c.name for c in conns] == ["vout_legacy"]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                characteristics: [rail_3v3]
                limits:
                  v_rail: {characteristic: rail_3v3, tolerance_pct: 2}
            """
        )
    )
    result = pytester.runpytest("-v", "--fixture-config", "fixtures/mini.yaml")
    result.assert_outcomes(passed=1)
