"""Condition-indexed limit bands — ``when:`` matching at measurement time.

Covers the ``bands:`` shape: each measurement value is a dict whose
top-level fields are defaults; an optional ``bands:`` list holds
``{when: ..., <override fields>}`` entries that override the defaults
when their ``when:`` clause matches the active vector params (mirroring
``SpecBand.when``). At measurement time the logger picks the first band
whose ``when:`` matches; no match → ``pytest.UsageError``.
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


def test_single_band_empty_when_always_matches(pytester: pytest.Pytester) -> None:
    """A single band with ``when: {}`` (or omitted) matches any params."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify):
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  units: V
                  bands:
                    - {when: {}, low: 3.2, high: 3.4}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_multi_band_selects_matching_by_parametrize(pytester: pytest.Pytester) -> None:
    """Multi-band list — the band matching the active parametrize row applies."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [5.0, 3.3])
            def test_rail(verify, vin):
                # Under vin=5.0: band1 applies (low=3.2 high=3.4) → 3.30 passes.
                # Under vin=3.3: band2 applies (low=3.1 high=3.5) → 3.30 passes.
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  units: V
                  bands:
                    - {when: {vin: 5.0}, low: 3.2, high: 3.4}
                    - {when: {vin: 3.3}, low: 3.1, high: 3.5}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_multi_band_bounds_differ_per_row(pytester: pytest.Pytester) -> None:
    """Distinct bands impose distinct bounds — one row passes, the other fails."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [5.0, 3.3])
            def test_rail(verify, vin):
                # Reading 3.30 fits the 5.0 band (3.2–3.4) but not
                # the 3.3 band (3.35–3.40).
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  bands:
                    - {when: {vin: 5.0}, low: 3.2, high: 3.4}
                    - {when: {vin: 3.3}, low: 3.35, high: 3.40}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_multi_band_two_keys_anded(pytester: pytest.Pytester) -> None:
    """``when:`` keys are ANDed — both must match for the band to apply."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin,load", [(5.0, 0.1), (5.0, 0.8)])
            def test_rail(verify, vin, load):
                # vin=5.0,load=0.1 → band1 (3.2–3.4): 3.30 passes.
                # vin=5.0,load=0.8 → band2 (2.9–3.1): 3.30 fails.
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  bands:
                    - {when: {vin: 5.0, load: 0.1}, low: 3.2, high: 3.4}
                    - {when: {vin: 5.0, load: 0.8}, low: 2.9, high: 3.1}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_no_band_matches_falls_back_to_siblings(pytester: pytest.Pytester) -> None:
    """No band matches active params → sibling values act as catch-all.

    The siblings to ``bands:`` *are* the catch-all by design of
    :class:`MeasurementLimitConfig`. With ``low/high`` set at the
    parent level, the measurement resolves against those when no
    band matches. (vin=12.0 misses both declared bands → catch-all
    3.0–3.6 applies → 3.30 passes.)
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [12.0])
            def test_rail(verify, vin):
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  low: 3.0
                  high: 3.6
                  units: V
                  bands:
                    - {when: {vin: 5.0}, low: 3.2, high: 3.4}
                    - {when: {vin: 3.3}, low: 3.1, high: 3.5}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_no_band_matches_no_siblings_records_unchecked(pytester: pytest.Pytester) -> None:
    """No band matches AND no sibling fallback → measurement records unchecked.

    Without parent values to fall back to, the resolver returns
    ``None`` so the measurement records in characterization mode
    (``outcome=DONE``) instead of raising. Authors who want strict
    matching declare a sibling catch-all.
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [12.0])
            def test_rail(verify, vin):
                verify("v_rail", 99.0)  # would fail any declared band
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  bands:
                    - {when: {vin: 5.0}, low: 3.2, high: 3.4}
                    - {when: {vin: 3.3}, low: 3.1, high: 3.5}
"""
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_scalar_dict_shape_still_resolves(pytester: pytest.Pytester) -> None:
    """A flat ``limits.<name>: {low, high}`` mapping (not a list) still works."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(verify):
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


def test_band_with_tolerance_pct_and_characteristic(pytester: pytest.Pytester) -> None:
    """Band using ``tolerance_pct`` against a product characteristic.

    The characteristic provides the nominal (3.3 V); the band applies
    ±2% at vin=5.0, ±5% at vin=3.3. A reading of 3.30 passes at vin=5.0
    (within 2%) but fails at vin=3.3 only when the reading strays —
    with 3.30 == nominal, both rows pass.
    """
    pytester.makeini(_INI)
    (pytester.path / "products").mkdir()
    (pytester.path / "products" / "mini.yaml").write_text(
        textwrap.dedent(
            """
            id: mini
            name: Mini
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
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [5.0, 3.3])
            def test_rail(verify, vin):
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
                v_rail:
                  characteristic: rail_3v3
                  bands:
                    - {when: {vin: 5.0}, tolerance_pct: 2}
                    - {when: {vin: 3.3}, tolerance_pct: 5}
"""
        )
    )
    result = pytester.runpytest("-v", "--spec=products/mini.yaml")
    result.assert_outcomes(passed=2)
