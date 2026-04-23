"""Integration tests for the ``pytest_native`` plugin using ``pytester``."""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


def _write_sequence(
    pytester: pytest.Pytester,
    test_body: str,
    vectors_yaml: str,
    wrap_vectors: bool = True,
) -> None:
    """Write a conftest/test_seq pair into the pytester tmp dir.

    ``vectors_yaml`` is wrapped under a top-level ``vectors:`` block by
    default, matching the unified-sidecar shape. Pass
    ``wrap_vectors=False`` to inject the literal YAML (useful when the
    test is exercising a full ``vectors:/limits:/mocks:`` shape).
    """
    pytester.makeconftest("")
    pytester.makepyfile(test_seq=test_body)
    if wrap_vectors:
        body = "vectors:\n" + textwrap.indent(vectors_yaml, "  ")
    else:
        body = vectors_yaml
    (pytester.path / "test_seq.yaml").write_text(body)
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
"""
        )
    )


def test_method_level_vectors_parametrize_and_populate_context(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_uses_vin(self, context):
                    assert context.get_param("vin") in (4.5, 5.0)

                def test_uses_load(self, context):
                    assert context.get_param("load") in (0.1, 0.8)
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_uses_vin:
                list:
                  - {vin: 4.5}
                  - {vin: 5.0}
              test_uses_load:
                list:
                  - {load: 0.1}
                  - {load: 0.8}
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
                def test_a(self, context):
                    assert context.get_param("temp") in (25, 55)

                def test_b(self, context):
                    assert context.get_param("temp") in (25, 55)
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            class:
              list:
                - {temp: 25}
                - {temp: 55}
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
                def test_only_class(self, context):
                    assert context.get_param("temp") in (25, 55)

                def test_sweep(self, context):
                    assert context.get_param("temp") in (25, 55)
                    assert context.get_param("vin") in (3.3, 5.0)
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            class:
              list:
                - {temp: 25}
                - {temp: 55}
            methods:
              test_sweep:
                list:
                  - {vin: 3.3}
                  - {vin: 5.0}
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
            addopts = -p no:litmus -p litmus.execution.plugin
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
            addopts = -p no:litmus -p litmus.execution.plugin
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
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_direct_args:
                product:
                  vin: [4.5, 5.0]
                  load: [0.1, 0.8]
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
            addopts = -p no:litmus -p litmus.execution.plugin
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
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_mix:
                list:
                  - {vin: 4.5}
                  - {vin: 5.0}
            """
        ),
    )
    result = pytester.runpytest("-v")
    # sidecar (2) × decorator (2) = 4 iterations
    result.assert_outcomes(passed=4)


def test_prereq_chain_skips_subsequent_methods_on_failure(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_a(self, context):
                    assert False, "intentional failure"

                def test_b(self, context):
                    assert True

                def test_c(self, context):
                    assert True
            """
        )
    )
    result = pytester.runpytest("-v")
    # test_a fails; test_b skipped (prereq test_a); test_c skipped (prereq test_b).
    result.assert_outcomes(failed=1, skipped=2)


def test_prereq_chain_first_method_always_runs(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_a(self, context):
                    assert True

                def test_b(self, context):
                    assert True
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_prereq_chain_collapses_method_level_parametrize(pytester: pytest.Pytester) -> None:
    """A single parametrize case failure on ``test_a`` gates **all** of ``test_b``.

    ``_prereq_state_key`` intentionally omits ``_litmus_method_vec`` from
    its key so method-level cases share one pass/fail entry per (class,
    class-vector). This guards that contract: if ``test_a[4.5]`` fails
    and ``test_a[5.0]`` would pass, ``test_b`` still sees the prereq as
    failed and skips every one of its parametrize cases.
    """
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_a(self, context):
                    vin = context.get_param("vin")
                    assert vin != 4.5, "intentional failure at vin=4.5"

                def test_b(self, context):
                    assert True
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_a:
                list:
                  - {vin: 4.5}
                  - {vin: 5.0}
              test_b:
                list:
                  - {vin: 4.5}
                  - {vin: 5.0}
            """
        ),
    )
    result = pytester.runpytest("-v")
    # test_a[4.5] fails, test_a[5.0] passes. Prereq collapses across cases,
    # so both test_b[4.5] and test_b[5.0] skip.
    result.assert_outcomes(passed=1, failed=1, skipped=2)


def test_prereq_chain_independent_marker_opts_out(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            class TestSeq:
                def test_a(self, context):
                    assert False, "intentional failure"

                @pytest.mark.litmus_independent
                def test_b(self, context):
                    assert True
            """
        )
    )
    result = pytester.runpytest("-v")
    # test_a fails; test_b runs despite prereq failure because of @litmus_independent.
    result.assert_outcomes(failed=1, passed=1)


def test_method_vec_id_uses_param_values(pytester: pytest.Pytester) -> None:
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_foo(self, context):
                    assert True
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_foo:
                list:
                  - {vin: 5.0}
                  - {vin: 3.3}
            """
        ),
    )

    result = pytester.runpytest("--collect-only", "-q")
    out = result.stdout.str()
    assert "vin=5.0" in out
    assert "vin=3.3" in out


# ---------------------------------------------------------------------------
# logger.measure(...) + spec.check(...) integration
# ---------------------------------------------------------------------------


_MEASURE_CONFTEST = textwrap.dedent(
    """
    import pytest
    from litmus.execution.decorators import get_current_logger, set_current_logger
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
            test_sequence_id="seq",
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
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(test_seq=test_body)
    if sidecar is not None:
        (pytester.path / "test_seq.yaml").write_text(sidecar)


def test_measure_records_outcome_without_raising(pytester: pytest.Pytester) -> None:
    """``logger.measure`` records FAIL outcome but does not raise —
    judgment lives on ``spec.check`` / explicit ``assert``."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            from litmus.data.models import Outcome

            class TestSeq:
                def test_records(self, logger):
                    m = logger.measure(
                        "v_out", 3.5, low=3.2, high=3.4, units="V", nominal=3.3
                    )
                    assert m.outcome == Outcome.FAIL
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_explicit_assert_fails_pytest_node(pytester: pytest.Pytester) -> None:
    """Test authors who want fail-fast on ad-hoc limits use plain assert."""
    _write_measure_test(
        pytester,
        textwrap.dedent(
            """
            class TestSeq:
                def test_fails(self, logger):
                    v = 3.5
                    logger.measure("v_out", v, low=3.2, high=3.4, units="V")
                    assert 3.2 <= v <= 3.4
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
            class TestSeq:
                def test_dup(self, logger):
                    logger.measure("v_out", 3.3, low=3.2, high=3.4, units="V")
                    logger.measure("v_out", 3.35, low=3.2, high=3.4, units="V")
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
            class TestSeq:
                def test_stream(self, logger):
                    for _ in range(10):
                        logger.measure(
                            "v_sample", 3.3,
                            low=3.2, high=3.4, units="V",
                            allow_repeat=True,
                        )
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
    ``Context._prev`` to the prior case so ``@litmus_test``'s vector-loop
    change-detection behavior still works under pytest parametrize.
    """
    _write_sequence(
        pytester,
        test_body=textwrap.dedent(
            """
            class TestSeq:
                def test_sweep(self, context):
                    vin = context.get_param("vin")
                    # First case: _prev is None → everything changed.
                    # Second case (vin=5.0 vs 4.5): vin changed.
                    # Third case (vin=5.0 vs 5.0): vin did NOT change.
                    expected = context.get_param("expect_changed")
                    assert context.changed("vin") is expected, \\
                        f"vin={vin} expected_changed={expected}"
            """
        ),
        vectors_yaml=textwrap.dedent(
            """
            methods:
              test_sweep:
                list:
                  - {vin: 4.5, expect_changed: true}
                  - {vin: 5.0, expect_changed: true}
                  - {vin: 5.0, expect_changed: false}
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
            addopts = -p no:litmus -p litmus.execution.plugin
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


def _write_markerless_sequence(pytester, test_body):
    """Write a pytester project without any sidecar YAML — marker-driven only."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest("")
    pytester.makepyfile(test_seq=test_body)


def test_litmus_vectors_marker_on_method_parametrizes(pytester: pytest.Pytester) -> None:
    """Method-level ``@pytest.mark.litmus_vectors(**kwargs)`` compiles to parametrize."""
    _write_markerless_sequence(
        pytester,
        textwrap.dedent(
            """
            import pytest

            class TestSeq:
                @pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5])
                def test_sweeps(self, context):
                    assert context.get_param("vin") in (4.5, 5.0, 5.5)
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=3)


def test_litmus_vectors_marker_on_class_parametrizes(pytester: pytest.Pytester) -> None:
    """Class-level ``@pytest.mark.litmus_vectors`` applies to every method."""
    _write_markerless_sequence(
        pytester,
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_vectors(vin=[4.5, 5.0])
            class TestSeq:
                def test_a(self, context):
                    assert context.get_param("vin") in (4.5, 5.0)

                def test_b(self, context):
                    assert context.get_param("vin") in (4.5, 5.0)
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_litmus_vectors_marker_class_and_method_cross_product(
    pytester: pytest.Pytester,
) -> None:
    """Class-level and method-level vector markers cross-product."""
    _write_markerless_sequence(
        pytester,
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_vectors(vin=[4.5, 5.0])
            class TestSeq:
                @pytest.mark.litmus_vectors(load=[0.1, 0.8])
                def test_matrix(self, context):
                    assert context.get_param("vin") in (4.5, 5.0)
                    assert context.get_param("load") in (0.1, 0.8)
            """
        ),
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=4)


def test_litmus_limits_marker_on_method_resolves(pytester: pytest.Pytester) -> None:
    """Method-level ``@pytest.mark.litmus_limits`` feeds ``logger.measure`` auto-resolution."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus.data.models import Outcome

            class TestSeq:
                @pytest.mark.litmus_limits(
                    output_voltage={"low": 3.2, "high": 3.4, "units": "V"},
                )
                def test_passes(self, logger):
                    m = logger.measure("output_voltage", 3.3)
                    assert m.outcome == Outcome.PASS

                @pytest.mark.litmus_limits(
                    output_voltage={"low": 3.2, "high": 3.4, "units": "V"},
                )
                def test_fails(self, logger):
                    m = logger.measure("output_voltage", 3.5)
                    assert m.outcome == Outcome.FAIL
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_litmus_limits_marker_method_overrides_class(pytester: pytest.Pytester) -> None:
    """Method-level ``litmus_limits`` overrides class-level for the same name."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus.data.models import Outcome

            @pytest.mark.litmus_limits(
                rail={"low": 3.2, "high": 3.4, "units": "V"},  # tight (class default)
            )
            class TestSeq:
                def test_tight_class_limit(self, logger):
                    m = logger.measure("rail", 3.5)
                    assert m.outcome == Outcome.FAIL

                @pytest.mark.litmus_limits(
                    rail={"low": 3.0, "high": 3.6, "units": "V"},  # loose override
                )
                def test_loose_method_limit(self, logger):
                    m = logger.measure("rail", 3.5)
                    assert m.outcome == Outcome.PASS
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_litmus_spec_marker_scopes_product(pytester: pytest.Pytester) -> None:
    """``@pytest.mark.litmus_spec(product=...)`` loads a SpecContext for the test."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    # Minimal product YAML: one characteristic with an inline limit.
    products = pytester.mkdir("products")
    (products / "test_product.yaml").write_text(
        textwrap.dedent(
            """
            id: test_product
            name: Test Product
            revision: '1.0'
            characteristics:
              output_voltage:
                function: dc_voltage
                direction: output
                units: V
                pins: [VOUT]
                specs:
                  - value: 3.3
                    accuracy:
                      pct_reading: 3.0
            pins:
              VOUT:
                name: TP1
            """
        )
    )
    pytester.makeconftest(_MEASURE_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus.execution.plugin import get_active_spec_context

            @pytest.mark.litmus_spec(product="test_product")
            class TestSeq:
                def test_spec_active(self):
                    ctx = get_active_spec_context()
                    assert ctx is not None
                    limit = ctx.get_limit("output_voltage")
                    # 3.3 ± 3% = [3.201, 3.399]
                    assert abs(limit.nominal - 3.3) < 1e-9
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_limits_fixture_destructured_access(pytester: pytest.Pytester) -> None:
    """Top-level ``limits`` fixture exposes the resolved limit map."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
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


_MOCKS_CONFTEST = textwrap.dedent(
    """
    import pytest

    class _Dmm:
        def measure_dc_voltage(self):
            return 999.0

    @pytest.fixture
    def dmm():
        return _Dmm()
    """
)


def test_litmus_mocks_marker_patches_fixture_method(pytester: pytest.Pytester) -> None:
    """``@pytest.mark.litmus_mocks`` patches <fixture>.<attr> for the test body."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest(_MOCKS_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            class TestSeq:
                @pytest.mark.litmus_mocks({"dmm.measure_dc_voltage": 3.3})
                def test_patched(self, dmm):
                    assert dmm.measure_dc_voltage() == 3.3

                def test_unpatched(self, dmm):
                    assert dmm.measure_dc_voltage() == 999.0
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_litmus_mocks_marker_class_level_and_method_override(
    pytester: pytest.Pytester,
) -> None:
    """Class-level and method-level ``litmus_mocks`` merge; method wins on key collision."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
            """
        )
    )
    pytester.makeconftest(_MOCKS_CONFTEST)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_mocks({"dmm.measure_dc_voltage": 1.1})
            class TestSeq:
                def test_class_value(self, dmm):
                    assert dmm.measure_dc_voltage() == 1.1

                @pytest.mark.litmus_mocks({"dmm.measure_dc_voltage": 2.2})
                def test_method_overrides(self, dmm):
                    assert dmm.measure_dc_voltage() == 2.2
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)


def test_context_last_returns_prior_param(pytester: pytest.Pytester) -> None:
    """``context.last(key)`` returns the previous parametrize case's value."""
    pytester.makeini(
        textwrap.dedent(
            """
            [pytest]
            addopts = -p no:litmus -p litmus.execution.plugin
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
                @pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5])
                def test_chain(self, context):
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
