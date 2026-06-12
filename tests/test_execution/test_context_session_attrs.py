"""Read-only session attributes on the ``context`` fixture.

``context.run`` / ``context.station`` / ``context.part`` are the
read-only ambient roll-up tests use without taking ``logger`` /
``station_config`` / ``part_context`` as fixture arguments. Each
delegates to a ContextVar getter in :mod:`litmus.execution._state`.

UUT identity intentionally lives at ``context.run.uut`` — a top-level
``context.uut`` would collide with the bare ``uut`` fixture (which is
the live UUT driver). Same reasoning skips ``context.instruments``;
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
    """``context.run`` exposes the active :class:`TestRun`, including ``run.uut``."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_run_attr=textwrap.dedent(
            """
            def test_run_exposes_record(context):
                assert context.run is not None
                assert context.run.uut is not None
                assert context.run.uut.serial == "ABC123"
            """
        )
    )
    result = pytester.runpytest("-v", "--uut-serial=ABC123")
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
    result = pytester.runpytest("-v", "--uut-serial=test")
    result.assert_outcomes(passed=1)


def test_context_station_resolves_when_loaded(pytester: pytest.Pytester) -> None:
    """With ``--station``, ``context.station`` exposes the model."""
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
    result = pytester.runpytest("-v", "--uut-serial=test", "--station=stations/alpha.yaml")
    result.assert_outcomes(passed=1)


def test_context_part_none_when_no_yaml(pytester: pytest.Pytester) -> None:
    """No part YAML → ``context.part`` is ``None``."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_no_part=textwrap.dedent(
            """
            def test_part_none(context):
                assert context.part is None
            """
        )
    )
    result = pytester.runpytest("-v", "--uut-serial=test")
    result.assert_outcomes(passed=1)


def test_context_part_resolves_when_loaded(pytester: pytest.Pytester) -> None:
    """With ``--part``, ``context.part`` exposes the :class:`PartContext`."""
    pytester.makeini(_INI)
    (pytester.path / "parts").mkdir()
    (pytester.path / "parts" / "widget.yaml").write_text(
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
        test_part=textwrap.dedent(
            """
            def test_part_loaded(context):
                assert context.part is not None
                assert context.part.part.id == "widget"
            """
        )
    )
    result = pytester.runpytest("-v", "--uut-serial=test", "--part=parts/widget.yaml")
    result.assert_outcomes(passed=1)
