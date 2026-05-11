"""``verify`` cascades the post-judgment outcome — not DONE.

Regression coverage for a bug where ``verify`` mutated
``measurement.outcome`` AFTER ``logger.measure`` had already cascaded
``step.outcome`` and emitted ``MeasurementRecorded`` with the recorder
default (DONE). The fix: ``verify`` resolves the limit upfront,
computes the outcome via ``_compute_outcome``, and passes it via
``logger.measure(outcome=...)`` so cascade and event fire ONCE with
the final value.

What we assert here:

* A passing ``verify`` produces ``measurement_outcome="passed"`` in
  the streaming parquet row, and ``step_outcome="passed"`` /
  ``run_outcome="passed"`` for the rolled-up rows.
* A failing ``verify`` produces ``"failed"`` everywhere (the streaming
  row in particular — failures are partially rescued elsewhere by the
  pytest hook escalating ``step.outcome``, but the row itself comes
  from the event and must carry the right outcome).
"""

from __future__ import annotations

import textwrap
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.pytest_plugin
    asyncio_default_fixture_loop_scope = function
    """
)


# Project-local results via repo ``litmus.yaml``.
_CANONICAL_RESULTS = resolve_data_dir()


def _read_measurement_row(serial: str, measurement_name: str, *, timeout: float = 15.0) -> dict:
    """Find the parquet row for ``serial`` + ``measurement_name`` under canonical.

    Polls because the runs daemon materializes parquets asynchronously
    after the subprocess exits.
    """
    import time

    deadline = time.monotonic() + timeout
    parquet = None
    while time.monotonic() < deadline:
        matches = [
            p
            for p in _CANONICAL_RESULTS.glob(f"runs/**/*_{serial}.parquet")
            if not p.stem.endswith("_steps")
        ]
        if matches:
            parquet = max(matches, key=lambda p: p.stat().st_mtime)
            break
        time.sleep(0.2)
    assert parquet is not None, f"no measurement parquet for serial={serial!r}"
    table = pq.read_table(parquet)
    rows = [r for r in table.to_pylist() if r.get("measurement_name") == measurement_name]
    assert rows, f"no row for {measurement_name!r} in {parquet}"
    return rows[0]


@pytest.mark.parametrize(
    "value,expected_outcome",
    [(3.3, "passed"), (5.0, "failed")],
    ids=["pass", "fail"],
)
def test_verify_cascade_to_streaming_row(
    pytester: pytest.Pytester, value: float, expected_outcome: str
) -> None:
    """Streaming parquet row carries the post-verify outcome — not DONE.

    Regression test. Before the cascade fix, a passing ``verify``
    produced ``measurement_outcome="done"`` because ``logger.measure``
    stamped DONE and emitted the event before ``verify._apply_outcome``
    overwrote the in-memory Measurement. The test asserts the
    post-verify outcome ("passed" / "failed") — would have FAILED
    pre-fix with the actual value being "done".
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            f"""
            from litmus.models.test_config import Limit

            def test_rail(verify):
                verify("v_rail", {value}, limit=Limit(low=3.0, high=3.6, units="V"))
            """
        )
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--dut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    if expected_outcome == "passed":
        result.assert_outcomes(passed=1)
    else:
        result.assert_outcomes(failed=1)

    row = _read_measurement_row(serial, "v_rail")

    # Bug-catch: pre-fix this was "done" for a passing verify; the
    # streaming subscriber materializes from MeasurementRecorded events,
    # which were emitted BEFORE verify mutated outcome.
    assert row["measurement_outcome"] != "done", (
        f"verify produced measurement_outcome='done' — the cascade fix regressed. "
        f"Expected {expected_outcome!r}."
    )
    assert row["measurement_outcome"] == expected_outcome
    assert row["run_outcome"] == expected_outcome


def test_logger_measure_alone_still_stamps_done(pytester: pytest.Pytester) -> None:
    """``logger.measure`` (no verify) keeps the recorder default DONE.

    The cascade fix is verify-specific. Plain ``logger.measure`` calls
    must continue to produce DONE rows (the recorder semantic — "ran,
    no judgment").
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(logger):
                logger.measure("v_rail", 3.3)
            """
        )
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--dut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    row = _read_measurement_row(serial, "v_rail")
    assert row["measurement_outcome"] == "done"


def test_in_test_vector_iteration_allows_repeat_name(pytester: pytest.Pytester) -> None:
    """``for v in vectors: verify(name, ...)`` works without allow_repeat.

    The ``vectors`` fixture runs the body inside one step and pushes a
    new TestVector per iteration (distinct ``index``). The dedup key
    includes ``vector_index`` so the same measurement name on different
    iterations is NOT a duplicate. Pre-fix, this pattern raised
    ``DuplicateMeasurementError`` on the second iteration.
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            from litmus.models.test_config import Limit

            def test_rails(vectors, verify):
                for v in vectors:
                    verify("v_rail", 3.3, limit=Limit(low=3.0, high=3.6, units="V"))
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_rails:
                sweeps:
                    - {vin: [4.5, 5.0, 5.5]}
            """
        )
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--dut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(passed=1)

    # Three vector rows, all with the same measurement name and outcome.
    # Poll because the runs daemon materializes parquets asynchronously
    # after the subprocess exits.
    import time

    deadline = time.monotonic() + 15.0
    parquet = None
    while time.monotonic() < deadline:
        matches = [
            p
            for p in _CANONICAL_RESULTS.glob(f"runs/**/*_{serial}.parquet")
            if not p.stem.endswith("_steps")
        ]
        if matches:
            parquet = max(matches, key=lambda p: p.stat().st_mtime)
            break
        time.sleep(0.2)
    assert parquet is not None, f"no parquet for {serial}"
    table = pq.read_table(parquet)
    rows = [r for r in table.to_pylist() if r.get("measurement_name") == "v_rail"]
    assert len(rows) == 3, f"expected 3 rows (one per vector), got {len(rows)}"
    assert {r["measurement_outcome"] for r in rows} == {"passed"}
