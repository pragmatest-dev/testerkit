"""Tests for inline list-builders in :mod:`litmus.expand`, the YAML range
expanders, and the kwargs-only shape of ``@pytest.mark.litmus_vectors``."""

from __future__ import annotations

import textwrap

import pytest

from litmus import arange, geomspace, linspace, logspace, repeat
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


def test_yaml_zipped_axis_via_multi_kwarg(pytester: pytest.Pytester) -> None:
    """YAML zip via multi-kwarg with each axis using its own range expander."""
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

            seen = []

            def test_pairs(vin, vout):
                seen.append((round(vin, 4), round(vout, 4)))

            def test_seen_after():
                # 5 zipped pairs from two linspace expanders.
                assert len(seen) == 5
                assert seen[0] == (3.3, 3.3)
                assert seen[-1] == (5.5, 3.32)
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_pairs:
                config:
                  - litmus_vectors:
                      - vin: {linspace: [3.3, 5.5, 5]}
                        vout: {linspace: [3.30, 3.32, 5]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=6)


def test_litmus_vectors_multi_kwarg_zips(pytester: pytest.Pytester) -> None:
    """Multi-kwarg in one decorator zips paired axes (no comma-keys needed)."""
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

            seen = []

            @pytest.mark.litmus_vectors(vin=[3, 4, 5], vout=[5, 7, 9])
            def test_pairs(vin, vout):
                seen.append((vin, vout))

            def test_seen_after():
                assert seen == [(3, 5), (4, 7), (5, 9)]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_litmus_vectors_zip_dim_mismatch_raises(pytester: pytest.Pytester) -> None:
    """Multi-kwarg with mismatched lengths raises at decoration time."""
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

            @pytest.mark.litmus_vectors(vin=[3, 4], vout=[5, 6, 7])
            def test_x(vin, vout): pass
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "same length" in combined


def test_litmus_vectors_stacked_top_is_outer(pytester: pytest.Pytester) -> None:
    """Stacked litmus_vectors: TOP decorator = outer (slow); BOTTOM = inner (fast).

    Inverts pytest's parametrize convention. Reads top-to-bottom as
    outer-to-inner, the same direction as a nested ``for`` loop.
    """
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

            seen = []

            @pytest.mark.litmus_vectors(temp=[25, 85])      # TOP    → outer (slowest)
            @pytest.mark.litmus_vectors(vin=[3, 5])          # BOTTOM → inner (fastest)
            def test_x(temp, vin):
                seen.append((temp, vin))

            def test_order_after():
                # Outer-to-inner reading: temp changes slowest, vin fastest.
                assert seen == [
                    (25, 3),
                    (25, 5),       # vin advanced (inner)
                    (85, 3),       # temp advanced (outer); vin reset
                    (85, 5),       # vin advanced
                ]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=5)


def test_litmus_vectors_rejects_string_positional(pytester: pytest.Pytester) -> None:
    """Old parametrize-style ``("vin", [...])`` raises a clear error.

    Each positional arg must be an axis-group dict (the YAML list-of-dicts
    shape). A bare argname string is the parametrize positional shape,
    which ``litmus_vectors`` deliberately doesn't accept inline (kwargs
    are canonical inline; YAML uses list-of-dicts).
    """
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

            @pytest.mark.litmus_vectors("vin", [3, 4])
            def test_x(vin): pass
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "axis-group must be a dict" in combined
