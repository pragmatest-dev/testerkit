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
from collections.abc import Iterator
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore

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


@pytest.fixture(scope="module", autouse=True)
def _runs_daemon_for_verify_cascade() -> Iterator[None]:
    """Keep the runs daemon alive for this module's pytester-subprocess tests.

    These tests run pytest in a subprocess, which emits events to the
    canonical events tree but doesn't directly spawn the runs daemon
    (the subprocess doesn't construct a RunStore that would acquire
    it). The materialization the tests poll for happens *inside* the
    runs daemon — so without an acquire-on-this-process, the daemon
    never spawns and no parquets get materialized.

    Running the test file in isolation surfaces the gap; running the
    full suite hides it because some earlier test acquires a RunStore.
    This module-scoped fixture pins the daemon as a ref so it stays
    alive throughout, regardless of which order the file runs in.
    """
    store = RunStore()
    try:
        yield
    finally:
        store.close()


def _read_measurement_row(serial: str, measurement_name: str, *, timeout: float = 15.0) -> dict:
    """Find the parquet row for ``serial`` + ``measurement_name`` under canonical.

    Polls because the runs daemon materializes parquets asynchronously
    after the subprocess exits.
    """
    import time

    deadline = time.monotonic() + timeout
    parquet = None
    while time.monotonic() < deadline:
        matches = list(_CANONICAL_RESULTS.glob(f"runs/**/*_{serial}.parquet"))
        if matches:
            parquet = max(matches, key=lambda p: p.stat().st_mtime)
            break
        time.sleep(0.2)
    assert parquet is not None, f"no measurement parquet for serial={serial!r}"
    table = pq.read_table(parquet)
    # Measurements are nested on their carrier — the step row (step-scope, e.g.
    # a top-level verify) or the vector row (vector-scope). Return the carrier
    # context merged with the flat ``measurement_*`` keys (the fact callers use).
    for r in table.to_pylist():
        if r.get("record_type") not in ("step", "vector"):
            continue
        for m in r.get("measurements") or []:
            if m["name"] == measurement_name:
                return {
                    **r,
                    "measurement_name": m["name"],
                    "measurement_value": m["value"],
                    "measurement_outcome": m["outcome"],
                }
    raise AssertionError(f"no measurement {measurement_name!r} in {parquet}")


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
                verify("v_rail", {value}, limit=Limit(low=3.0, high=3.6, unit="V"))
            """
        )
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
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


def test_measure_alone_still_stamps_done(pytester: pytest.Pytester) -> None:
    """``measure`` (no verify) keeps the recorder default DONE.

    The cascade fix is verify-specific. Plain ``measure`` calls
    must continue to produce DONE rows (the recorder semantic — "ran,
    no judgment").
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_rail(measure):
                measure("v_rail", 3.3)
            """
        )
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
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
                    verify("v_rail", 3.3, limit=Limit(low=3.0, high=3.6, unit="V"))
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
        f"--uut-serial={serial}",
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
        matches = list(_CANONICAL_RESULTS.glob(f"runs/**/*_{serial}.parquet"))
        if matches:
            parquet = max(matches, key=lambda p: p.stat().st_mtime)
            break
        time.sleep(0.2)
    assert parquet is not None, f"no parquet for {serial}"
    table = pq.read_table(parquet)
    rows = [
        m
        for r in table.to_pylist()
        if r.get("record_type") == "vector"
        for m in (r.get("measurements") or [])
        if m["name"] == "v_rail"
    ]
    assert len(rows) == 3, f"expected 3 measurements (one per vector), got {len(rows)}"
    assert {r["outcome"] for r in rows} == {"passed"}
