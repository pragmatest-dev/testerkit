"""Tests for the inline list-builders in :mod:`litmus.expand`.

These are the Python counterparts to the YAML range expanders. Tests
verify they produce the same lists the YAML loader produces from the
equivalent dict-form expander, and that ``paired`` builds a valid
``litmus_vectors`` marker.
"""

from __future__ import annotations

import textwrap

import pytest

from litmus import arange, geomspace, linspace, logspace, paired, repeat
from litmus.config.expanders import expand_ranges

pytest_plugins = ["pytester"]


def test_linspace_matches_yaml_expander() -> None:
    inline = linspace(3.3, 5.5, 5)
    yaml_form = expand_ranges({"linspace": [3.3, 5.5, 5]})
    assert inline == yaml_form
    assert len(inline) == 5
    assert inline[0] == pytest.approx(3.3)
    assert inline[-1] == pytest.approx(5.5)


def test_arange_matches_yaml_expander() -> None:
    inline = arange(0.0, 1.0, 0.25)
    yaml_form = expand_ranges({"arange": [0.0, 1.0, 0.25]})
    assert inline == yaml_form
    assert inline == [0.0, 0.25, 0.5, 0.75]


def test_logspace_matches_yaml_expander() -> None:
    inline = logspace(1, 3, 3)
    yaml_form = expand_ranges({"logspace": [1, 3, 3]})
    assert inline == yaml_form


def test_geomspace_matches_yaml_expander() -> None:
    inline = geomspace(1, 1000, 4)
    yaml_form = expand_ranges({"geomspace": [1, 1000, 4]})
    assert inline == yaml_form


def test_repeat_matches_yaml_expander() -> None:
    inline = repeat(5.0, 4)
    yaml_form = expand_ranges({"repeat": [5.0, 4]})
    assert inline == yaml_form
    assert inline == [5.0, 5.0, 5.0, 5.0]


def test_paired_rejects_no_kwargs() -> None:
    with pytest.raises(ValueError, match="at least one keyword"):
        paired()


def test_paired_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        paired(vin=[3, 4], vout=[5, 6, 7])


def test_paired_runs_as_a_pytest_marker(pytester: pytest.Pytester) -> None:
    """End-to-end: ``@paired(...)`` decorates a test and produces zipped cases."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            from litmus import paired

            seen = []

            @paired(vin=[3, 4, 5], vout=[5, 7, 9])
            def test_pairs(vin, vout):
                seen.append((vin, vout))

            def test_seen_after():
                assert seen == [(3, 5), (4, 7), (5, 9)]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)  # 3 paired cases + 1 sentinel


def test_paired_stacks_with_litmus_vectors(pytester: pytest.Pytester) -> None:
    """``@paired`` (zip axis) stacks with ``@litmus_vectors`` (independent axis)."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus import paired

            seen = []

            @pytest.mark.litmus_vectors(temp=[25, 85])
            @paired(vin=[3, 4], vout=[5, 6])
            def test_stacked(vin, vout, temp):
                seen.append((vin, vout, temp))

            def test_after():
                # 2 paired × 2 temps = 4 cases.
                assert len(seen) == 4
                assert (3, 5, 25) in seen
                assert (4, 6, 85) in seen
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=5)  # 4 stacked cases + 1 sentinel
