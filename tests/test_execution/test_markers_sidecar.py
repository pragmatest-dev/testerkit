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
            tests:
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
            tests:
              TestRails:
                markers:
                  - litmus_limits:
                      v_rail: {low: 3.0, high: 3.6, units: V}
                tests:
                  test_strict:
                    markers:
                      - litmus_limits:
                          v_rail: {low: 3.2, high: 3.4, units: V}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_per_test_mock_tightens_file_level(pytester: pytest.Pytester) -> None:
    """Per-test ``litmus_mock`` for the same target overrides file-level."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            class FakeDmm:
                def read(self):
                    return 0.0

            @pytest.fixture
            def dmm():
                return FakeDmm()

            def test_file_level(dmm):
                # File-level mock returns 1.1.
                assert dmm.read() == 1.1

            def test_per_test(dmm):
                # Per-test mock overrides file-level; returns 2.2.
                assert dmm.read() == 2.2
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            markers:
              - litmus_mock: {target: "dmm.read", return_value: 1.1}
            tests:
              test_per_test:
                markers:
                  - litmus_mock: {target: "dmm.read", return_value: 2.2}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_litmus_mock_forwards_side_effect(pytester: pytest.Pytester) -> None:
    """``side_effect`` and other ``mocker.patch.object`` kwargs forward verbatim."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            class FakeDmm:
                def read(self):
                    return 0.0

            @pytest.fixture
            def dmm():
                return FakeDmm()

            def test_side_effect_iterable(dmm):
                # side_effect as iterable yields values per call (one-shot).
                assert dmm.read() == 1.0
                assert dmm.read() == 2.0
                assert dmm.read() == 3.0

            def test_return_value_list_is_returned_as_list(dmm):
                # return_value is verbatim — a list returns as a list.
                assert dmm.read() == [1.0, 2.0, 3.0]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_side_effect_iterable:
                markers:
                  - litmus_mock: {target: "dmm.read", side_effect: [1.0, 2.0, 3.0]}
              test_return_value_list_is_returned_as_list:
                markers:
                  - litmus_mock: {target: "dmm.read", return_value: [1.0, 2.0, 3.0]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


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
