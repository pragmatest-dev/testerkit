"""Integration tests for the ``pytest_native`` plugin using ``pytester``."""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


def _write_sequence(
    pytester: pytest.Pytester,
    test_body: str,
    sidecar_yaml: str,
) -> None:
    """Write a conftest/test_seq pair into the pytester tmp dir.

    ``sidecar_yaml`` is written verbatim — callers pass the full markers
    sidecar shape (``markers:`` / ``tests:``).
    """
    pytester.makeconftest("")
    pytester.makepyfile(test_seq=test_body)
    (pytester.path / "test_seq.yaml").write_text(sidecar_yaml)
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
"""
        )
    )


def test_method_level_vectors_parametrize_and_populate_context(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_uses_vin(self, vin, context):
                    assert context.get_param("vin") in (4.5, 5.0)

                def test_uses_load(self, load, context):
                    assert context.get_param("load") in (0.1, 0.8)
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq.test_uses_vin:
                runner:
                  markers:
                    - parametrize: ["vin", [4.5, 5.0]]
              TestSeq.test_uses_load:
                runner:
                  markers:
                    - parametrize: ["load", [0.1, 0.8]]
            """
        ),
    )

    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_class_level_vectors_rerun_whole_class(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_a(self, temp, context):
                    assert context.get_param("temp") in (25, 55)

                def test_b(self, temp, context):
                    assert context.get_param("temp") in (25, 55)
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq:
                runner:
                  markers:
                    - parametrize: ["temp", [25, 55]]
            """
        ),
    )

    result = pytester.runpytest("-v")
    # 2 methods × 2 class vectors = 4 total
    result.assert_outcomes(passed=4)


def test_class_and_method_vectors_mix(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_only_class(self, temp, context):
                    assert context.get_param("temp") in (25, 55)

                def test_sweep(self, temp, vin, context):
                    assert context.get_param("temp") in (25, 55)
                    assert context.get_param("vin") in (3.3, 5.0)
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq:
                runner:
                  markers:
                    - parametrize: ["temp", [25, 55]]
                tests:
                  test_sweep:
                    runner:
                      markers:
                        - parametrize: ["vin", [3.3, 5.0]]
            """
        ),
    )

    result = pytester.runpytest("-v")
    # test_only_class: 2 class vectors × 1 = 2
    # test_sweep: 2 class vectors × 2 method vectors = 4
    result.assert_outcomes(passed=6)


def test_sequence_without_sidecar_runs_once(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
"""
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_defaults(self, context):
                    assert context.get_param("missing", "dflt") == "dflt"
            """
        )
    )

    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_plain_tests_ignored_by_plugin(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
"""
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_plain():
                assert True

            class TestPlain:
                def test_member(self):
                    assert True
            """
        )
    )

    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_sidecar_keys_bind_to_method_signature(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_direct_args(self, vin, load, context):
                    # Values injected as fixture args AND readable via context.
                    assert vin in (4.5, 5.0)
                    assert load in (0.1, 0.8)
                    assert context.get_param("vin") == vin
                    assert context.get_param("load") == load
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq.test_direct_args:
                runner:
                  markers:
                    - parametrize: ["vin", [4.5, 5.0]]
                    - parametrize: ["load", [0.1, 0.8]]
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_decorator_parametrize_populates_context(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            class TestSeq:
                @pytest.mark.parametrize("vin", [4.5, 5.0])
                def test_decorator(self, vin, context):
                    assert context.get_param("vin") == vin
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_sidecar_and_decorator_mix(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            import pytest
            class TestSeq:
                @pytest.mark.parametrize("trial", [1, 2])
                def test_mix(self, trial, vin, context):
                    # vin from sidecar, trial from decorator — both in context.
                    assert vin in (4.5, 5.0)
                    assert trial in (1, 2)
                    assert context.get_param("vin") == vin
                    assert context.get_param("trial") == trial
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq.test_mix:
                runner:
                  markers:
                    - parametrize: ["vin", [4.5, 5.0]]
            """
        ),
    )
    result = pytester.runpytest("-v")
    # sidecar (2) × decorator (2) = 4 iterations
    result.assert_outcomes(passed=4)


def test_method_vec_id_uses_param_values(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_foo(self, vin, context):
                    assert True
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq.test_foo:
                runner:
                  markers:
                    - parametrize: ["vin", [5.0, 3.3]]
            """
        ),
    )

    result = pytester.runpytest("--collect-only", "-q")
    out = result.stdout.str()
    assert "5.0" in out
    assert "3.3" in out


# ---------------------------------------------------------------------------
# logger.measure(...) + spec.check(...) integration
# ---------------------------------------------------------------------------


_MEASURE_CONFTEST = textwrap.dedent(
    """
    import pytest
    from litmus.execution._state import get_current_logger, set_current_logger
    from litmus.execution.logger import TestRunLogger

    # Session-scoped so the main plugin's session-scoped fixtures that
    # depend on ``logger`` (e.g. ``instruments``) can resolve it without
    # a ScopeMismatch.
    @pytest.fixture(scope="session", autouse=True)
    def _active_logger():
        prev = get_current_logger()
        _logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        set_current_logger(_logger)
        try:
            yield _logger
        finally:
            set_current_logger(prev)

    @pytest.fixture(scope="session")
    def logger(_active_logger):
        return _active_logger
    """
)


def _write_measure_test(
    pytester: pytest.Pytester,
    test_body: str,
    sidecar: str | None = None,
) -> None:
    """Write a pytester project with an active TestRunLogger for measure() calls."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(test_seq=test_body)
    if sidecar is not None:
        (pytester.path / "test_seq.yaml").write_text(sidecar)


def test_measure_records_outcome_without_raising(pytester: pytest.Pytester) -> None:
    """``logger.measure`` records and stamps DONE — never raises on out-of-limit.

    Judgment lives on ``verify`` (the one verb that judges, raises, and
    cascades FAILED). ``logger.measure`` is the recorder: the row gets
    ``Outcome.DONE`` so it shows up in analytics as "ran, no judgment."
    """
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            from litmus.data.models import Outcome
            from litmus.models.test_config import Limit

            class TestSeq:
                def test_records(self, logger):
                    m = logger.measure(
                        "v_out", 3.5,
                        limit=Limit(low=3.2, high=3.4, units="V", nominal=3.3),
                    )
                    # Recorder, not judge: outcome is DONE (recorded), and
                    # limit fields are stamped on the row for analysis.
                    assert m.outcome == Outcome.DONE
                    assert m.limit_low == 3.2
                    assert m.limit_high == 3.4
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_verify_raises_on_fail(pytester: pytest.Pytester) -> None:
    """``verify(name, value, limit=...)`` raises LimitFailure on FAIL."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            from litmus.models.test_config import Limit

            class TestSeq:
                def test_fails(self, verify):
                    verify("v_out", 3.5, Limit(low=3.2, high=3.4, units="V"))
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=1)


def test_duplicate_measurement_name_in_step_errors(
    pytester: pytest.Pytester,
) -> None:
    """Two writes with the same name in one step raise DuplicateMeasurementError."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            from litmus.models.test_config import Limit

            class TestSeq:
                def test_dup(self, logger):
                    lim = Limit(low=3.2, high=3.4, units="V")
                    logger.measure("v_out", 3.3, limit=lim)
                    logger.measure("v_out", 3.35, limit=lim)
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=1)
    out = result.stdout.str()
    assert "already recorded" in out


def test_allow_repeat_streams_same_name(pytester: pytest.Pytester) -> None:
    """Inner-loop streaming requires allow_repeat on every call."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            from litmus.models.test_config import Limit

            class TestSeq:
                def test_stream(self, logger):
                    lim = Limit(low=3.2, high=3.4, units="V")
                    for _ in range(10):
                        logger.measure("v_sample", 3.3, limit=lim, allow_repeat=True)
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_sidecar_limits_auto_resolve(pytester: pytest.Pytester) -> None:
    """`logger.measure(name, value)` picks up the sidecar limit by name."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            class TestSeq:
                def test_resolves(self, logger):
                    logger.measure("v_out", 3.25)  # no inline limit
            """
        ),
        sidecar=textwrap.dedent(
            """
            limits:
              v_out:
                low: 3.2
                high: 3.4
                units: V
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_changed_chains_across_parametrize_cases(pytester: pytest.Pytester) -> None:
    """``context.changed("vin")`` returns False when the current parametrize
    case has the same ``vin`` as the previous case, True otherwise.

    Verifies the class-scoped ``_PREV_CONTEXTS`` tracker wires each case's
    ``Context._prev`` to the prior case so change-detection still works
    across adjacent pytest parametrize cases.
    """
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_sweep(self, vin, expect_changed, context):
                    # First case: _prev is None → everything changed.
                    # Second case (vin=5.0 vs 4.5): vin changed.
                    # Third case (vin=5.0 vs 5.0): vin did NOT change.
                    assert context.changed("vin") is expect_changed, \\
                        f"vin={vin} expected_changed={expect_changed}"
            """
        ),
        sidecar_yaml=textwrap.dedent(
            """
            tests:
              TestSeq.test_sweep:
                runner:
                  markers:
                    - parametrize:
                        ["vin,expect_changed", [[4.5, true], [5.0, true], [5.0, false]]]
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=3)


def test_pure_pytest_assert_no_litmus_machinery(pytester: pytest.Pytester) -> None:
    """A sequence can fall back to plain ``assert`` when no sidecar / spec exists."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_asserts(self):
                    val = 3.3
                    assert 3.2 <= val <= 3.4
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_litmus_limits_marker_on_method_resolves(pytester: pytest.Pytester) -> None:
    """Method-level ``@pytest.mark.litmus_limits`` feeds ``verify`` resolution."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            class TestSeq:
                @pytest.mark.litmus_limits(
                    output_voltage={"low": 3.2, "high": 3.4, "units": "V"},
                )
                def test_passes(self, verify):
                    verify("output_voltage", 3.3)

                @pytest.mark.litmus_limits(
                    output_voltage={"low": 3.2, "high": 3.4, "units": "V"},
                )
                def test_fails(self, verify):
                    verify("output_voltage", 3.5)
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_litmus_limits_marker_method_overrides_class(pytester: pytest.Pytester) -> None:
    """Method-level ``litmus_limits`` overrides class-level for the same name."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_limits(
                rail={"low": 3.2, "high": 3.4, "units": "V"},  # tight (class default)
            )
            class TestSeq:
                def test_tight_class_limit(self, verify):
                    verify("rail", 3.5)  # fails tight class limit

                @pytest.mark.litmus_limits(
                    rail={"low": 3.0, "high": 3.6, "units": "V"},  # loose override
                )
                def test_loose_method_limit(self, verify):
                    verify("rail", 3.5)  # passes loose method limit
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)


def test_limits_fixture_destructured_access(pytester: pytest.Pytester) -> None:
    """Top-level ``limits`` fixture exposes the resolved limit map."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            class TestSeq:
                @pytest.mark.litmus_limits(
                    rail={"low": 3.2, "high": 3.4, "units": "V"},
                )
                def test_reads_limits(self, limits):
                    assert "rail" in limits
                    assert limits["rail"].low == 3.2
                    assert limits["rail"].high == 3.4
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_context_last_returns_prior_param(pytester: pytest.Pytester) -> None:
    """``context.last(key)`` returns the previous parametrize case's value."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.pytest_plugin
            asyncio_default_fixture_loop_scope = function
            """
        )
    )
    pytester.makeconftest("")
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            SEEN = []

            class TestSeq:
                @pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
                def test_chain(self, vin, context):
                    SEEN.append((context.get_param("vin"), context.last("vin")))

            def test_check_order():
                # First case: no prior, last() returns None
                # Second case: last() == 4.5
                # Third case: last() == 5.0
                assert SEEN[0] == (4.5, None)
                assert SEEN[1] == (5.0, 4.5)
                assert SEEN[2] == (5.5, 5.0)
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)
