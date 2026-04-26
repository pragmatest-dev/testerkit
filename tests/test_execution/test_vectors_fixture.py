"""Self-loop mode — the ``vectors`` fixture.

When a test function takes ``vectors`` in its signature, pytest collects
a single test case regardless of how many parametrize rows or sidecar
vectors are declared. The fixture yields an iterator over the
consolidated matrix; iterating pushes active params + index per row so
``context.get_param`` / ``.changed`` / ``.last`` and
``logger.measure`` / ``verify`` behave identically to normal mode.
"""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.execution.plugin
    asyncio_default_fixture_loop_scope = function
    """
)


def test_vectors_fixture_sidecar_single_case_iterates_matrix(
    pytester: pytest.Pytester,
) -> None:
    """Sidecar vectors + ``vectors`` fixture → one test case, N iterations."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, vectors):
                seen = []
                for v in vectors:
                    seen.append(v["vin"])
                    assert context.get_param("vin") == v["vin"]
                assert seen == [4.5, 5.0, 5.5]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [4.5, 5.0, 5.5]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_consumes_native_parametrize(pytester: pytest.Pytester) -> None:
    """Native ``@pytest.mark.parametrize`` gets consumed into the matrix."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [3.3, 5.0, 12.0])
            def test_rails(context, vectors):
                seen = []
                for v in vectors:
                    seen.append(v["vin"])
                assert seen == [3.3, 5.0, 12.0]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_change_tracking_across_iterations(
    pytester: pytest.Pytester,
) -> None:
    """``context.changed()`` flips appropriately inside the self-loop."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(context, vectors):
                seen_changed = []
                for _ in vectors:
                    seen_changed.append(context.changed("vin"))
                # First iteration: no prev row → True.
                # Subsequent iterations: differs from prior row → True.
                assert seen_changed == [True, True, True]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [3.3, 5.0, 12.0]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_vector_index_increments(pytester: pytest.Pytester) -> None:
    """Active-vector-index ContextVar increments per iteration."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            from litmus.execution.plugin import get_active_vector_index

            def test_rails(vectors):
                seen = []
                for _ in vectors:
                    seen.append(get_active_vector_index())
                assert seen == [0, 1, 2]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [3.3, 5.0, 12.0]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_active_params_pushed_per_iteration(
    pytester: pytest.Pytester,
) -> None:
    """``get_active_vector_params`` mirrors the current row inside the loop."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            from litmus.execution.plugin import get_active_vector_params

            def test_rails(vectors):
                seen = []
                for _ in vectors:
                    seen.append(dict(get_active_vector_params()))
                assert seen == [
                    {"vin": 3.3, "load": 0.1},
                    {"vin": 5.0, "load": 0.8},
                ]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [3.3, 5.0], load: [0.1, 0.8]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_empty_matrix_runs_zero_iterations_ok(
    pytester: pytest.Pytester,
) -> None:
    """Empty matrix + zero iterations → test passes (vacuously empty is fine)."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(vectors):
                count = 0
                for _ in vectors:
                    count += 1
                assert count == 0
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_vectors_fixture_non_empty_matrix_unused_fails(pytester: pytest.Pytester) -> None:
    """Non-empty matrix + body never iterates → framework fails the test.

    The ``vectors`` fixture teardown calls :func:`pytest.fail`, so pytest
    surfaces this as a teardown error (non-zero exit) even when the test
    body itself did not raise.
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rails(vectors):
                # Body never iterates — silent skip that should fail.
                pass
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [3.3, 5.0]}
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*``vectors`` fixture was not iterated*"])


def test_vectors_fixture_rejects_class_level_parametrize(
    pytester: pytest.Pytester,
) -> None:
    """Class-level @pytest.mark.parametrize + ``vectors`` → clean UsageError."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("vin", [5.0, 12.0])
            class TestRails:
                def test_one(self, vectors, vin):
                    for _ in vectors:
                        pass
            """
        )
    )
    result = pytester.runpytest("-v")
    assert result.ret != 0


def test_vectors_fixture_crosses_parametrize_and_sidecar(
    pytester: pytest.Pytester,
) -> None:
    """Parametrize rows × sidecar rows → cross product in the matrix."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("load", [0.1, 0.8])
            def test_rails(vectors):
                pairs = []
                for v in vectors:
                    pairs.append((v["vin"], v["load"]))
                assert pairs == [
                    (3.3, 0.1), (5.0, 0.1),
                    (3.3, 0.8), (5.0, 0.8),
                ]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                config:
                  - litmus_vectors:
                      - {vin: [3.3, 5.0]}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)
