"""Tests for inline list-builders in :mod:`litmus.expand`, the YAML range
expanders, and the list-of-dicts shape of ``@pytest.mark.litmus_sweeps``."""

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


def test_yaml_zipped_axes_via_multi_key(pytester: pytest.Pytester) -> None:
    """YAML zip via multi-key sweep dict, each key using its own range expander."""
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
                  - litmus_sweeps:
                      - vin: {linspace: [3.3, 5.5, 5]}
                        vout: {linspace: [3.30, 3.32, 5]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=6)


def test_litmus_sweeps_multi_key_zips(pytester: pytest.Pytester) -> None:
    """Multi-key dict in one sweep entry zips paired axes."""
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

            @pytest.mark.litmus_sweeps([{"vin": [3, 4, 5], "vout": [5, 7, 9]}])
            def test_pairs(vin, vout):
                seen.append((vin, vout))

            def test_seen_after():
                assert seen == [(3, 5), (4, 7), (5, 9)]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_litmus_sweeps_zip_dim_mismatch_raises(pytester: pytest.Pytester) -> None:
    """Multi-key sweep dict with mismatched lengths raises at decoration time."""
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

            @pytest.mark.litmus_sweeps([{"vin": [3, 4], "vout": [5, 6, 7]}])
            def test_x(vin, vout): pass
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "same length" in combined


def test_litmus_sweeps_stacked_top_is_outer(pytester: pytest.Pytester) -> None:
    """Stacked litmus_sweeps: TOP decorator = outer (slow); BOTTOM = inner (fast).

    Inverts pytest's parametrize convention. Reads top-to-bottom as
    outer-to-inner, the same direction as a nested ``for`` loop. Same
    direction as the list-of-dicts payload within one decorator (first
    list entry = outer).
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

            @pytest.mark.litmus_sweeps([{"temp": [25, 85]}])  # TOP    → outer (slowest)
            @pytest.mark.litmus_sweeps([{"vin": [3, 5]}])      # BOTTOM → inner (fastest)
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


def test_litmus_sweeps_rejects_kwargs_form(pytester: pytest.Pytester) -> None:
    """Old kwargs shape ``(vin=[...])`` raises — list-of-dicts is canonical."""
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

            @pytest.mark.litmus_sweeps(vin=[3, 4])
            def test_x(vin): pass
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "does not accept keyword arguments" in combined
