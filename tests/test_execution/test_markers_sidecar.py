"""End-to-end coverage of the sidecar ``markers:`` shape.

File-level / class-level / per-test scopes, marker merging across
scopes, stacked parametrize cross-product, and collection-time errors
for obviously-wrong sidecars.
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


def test_stacked_parametrize_cross_products(pytester: pytest.Pytester) -> None:
    """Two ``parametrize:`` entries with distinct argnames cross-product."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(vin, load, context):
                pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                markers:
                  - parametrize: ["vin", [3.3, 5.0]]
                  - parametrize: ["load", [0.1, 0.8]]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_overlapping_argnames_is_an_error(pytester: pytest.Pytester) -> None:
    """Two ``parametrize:`` entries sharing an argname fail at collection."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(vin, context):
                pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rail:
                markers:
                  - parametrize: ["vin", [3.3, 5.0]]
                  - parametrize: ["vin", [6.0, 7.0]]
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0


def test_file_level_markers_apply_to_every_test(pytester: pytest.Pytester) -> None:
    """A file-root ``markers:`` list applies to every test in the module."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_one(verify):
                verify("v_rail", 3.30)

            def test_two(verify):
                verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            markers:
              - litmus_limits:
                  v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_per_test_marker_tightens_file_level(pytester: pytest.Pytester) -> None:
    """Per-test ``litmus_limits`` for the same name overrides file-level."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_loose(verify):
                # File band (3.0–3.6) accepts 3.50.
                verify("v_rail", 3.50)

            def test_tight(verify):
                # Per-test band (3.2–3.4) rejects 3.50 → FAIL.
                verify("v_rail", 3.50)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            markers:
              - litmus_limits:
                  v_rail: {low: 3.0, high: 3.6, units: V}
            tests:
              test_tight:
                markers:
                  - litmus_limits:
                      v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_class_scoped_markers_apply_to_every_method(pytester: pytest.Pytester) -> None:
    """``classes.TestX.markers`` applies to every method of TestX."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestRails:
                def test_a(self, verify):
                    verify("v_rail", 3.30)

                def test_b(self, verify):
                    verify("v_rail", 3.30)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            classes:
              TestRails:
                markers:
                  - litmus_limits:
                      v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_qualified_test_entry_tightens_class_level(pytester: pytest.Pytester) -> None:
    """``tests.TestRails.test_strict`` overrides the class-level band."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestRails:
                def test_loose(self, verify):
                    verify("v_rail", 3.50)

                def test_strict(self, verify):
                    verify("v_rail", 3.50)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            classes:
              TestRails:
                markers:
                  - litmus_limits:
                      v_rail: {low: 3.0, high: 3.6, units: V}
            tests:
              TestRails.test_strict:
                markers:
                  - litmus_limits:
                      v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_range_expander_in_parametrize_argvalues(pytester: pytest.Pytester) -> None:
    """``{linspace: [...]}`` in argvalues expands to a plain list."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_sweep(vin, context):
                pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_sweep:
                markers:
                  - parametrize: ["vin", {linspace: [4.5, 5.5, 11]}]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=11)
