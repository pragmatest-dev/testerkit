"""Verify-less vectors + mixed-type observation lanes (vector-grained model).

Two materialization paths in the codebase:

- **offline path** — :meth:`ParquetBackend._build_measurement_rows`
  walks a pre-built :class:`TestRun` (e.g. from ``LitmusClient``)
  and emits one row per ``vector.measurements`` entry.
- **live path** — :class:`EventAccumulator` projects an event
  stream; :func:`materialize_run_to_parquet` writes its state out
  at ``RunEnded``. Used by the runs daemon for real pytest runs.

The vector-grained model — per vector at materialization —

| Vector contained | Measurement rows |
|---|---|
| ≥1 verify | one per verify; observations ride on the step/vector record's ``outputs`` lanes |
| 0 verify, ≥1 observe | NONE — observations ride on the step/vector record's ``outputs`` lanes |
| 0 of either | none |

There is no synthesized DONE measurement row, and mixed-type
observations no longer raise — each value routes to its own ``value_*``
lane (the lane absorbs the type difference). Both rules apply to both
paths — covered here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends._row_helpers import decode_lane_structs
from litmus.data.backends.parquet import (
    ParquetBackend,
    materialize_run_to_parquet,
)
from litmus.data.events import (
    MeasurementRecorded,
    Observation,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    VectorEnded,
    VectorStarted,
)
from litmus.data.models import (
    UUT,
    Measurement,
    Outcome,
    TestRun,
    TestStep,
    TestVector,
)

# --------------------------------------------------------------------- #
# helpers — offline path                                                #
# --------------------------------------------------------------------- #


def _run_with_vector(
    *,
    measurements: list[Measurement] | None = None,
    observations: dict | None = None,
) -> TestRun:
    """Build a minimal TestRun with a single step containing one vector."""
    return TestRun(
        id=uuid4(),
        started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
        uut=UUT(serial="SN001"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name="test_capture",
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        measurements=measurements or [],
                        observations=observations or {},
                    )
                ],
            )
        ],
    )


def _read_measurement_rows(parquet_path: Path) -> list[dict]:
    table = pq.read_table(parquet_path)
    return [r for r in table.to_pylist() if r.get("record_type") == "measurement"]


def _read_step_rows(parquet_path: Path) -> list[dict]:
    table = pq.read_table(parquet_path)
    return [r for r in table.to_pylist() if r.get("record_type") == "step"]


# --------------------------------------------------------------------- #
# Item 9 — offline path                                                  #
# --------------------------------------------------------------------- #


class TestAutoPromotionOffline:
    def test_observation_only_vector_emits_no_measurement_row(self, tmp_path: Path) -> None:
        """0 verify + ≥1 observe → NO measurement row; observations ride on
        the step record's outputs lanes (no fabricated DONE row)."""
        run = _run_with_vector(
            observations={"temperature": 23.5, "humidity": 45.0},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        assert _read_measurement_rows(parquet_path) == []

        step_rows = _read_step_rows(parquet_path)
        assert len(step_rows) == 1
        outputs = decode_lane_structs(step_rows[0]["outputs"])
        assert outputs == {"temperature": 23.5, "humidity": 45.0}

    def test_verify_present_no_done_promotion(self, tmp_path: Path) -> None:
        """≥1 verify → verify rows only; observations ride on the step record."""
        run = _run_with_vector(
            measurements=[
                Measurement(name="vout", value=3.3, outcome=Outcome.PASSED),
            ],
            observations={"temperature": 23.5},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["measurement_outcome"] == "passed"
        # The observation rides on the step record's outputs lanes.
        step_rows = _read_step_rows(parquet_path)
        assert decode_lane_structs(step_rows[0]["outputs"]) == {"temperature": 23.5}

    def test_empty_vector_emits_no_measurement_rows(self, tmp_path: Path) -> None:
        """0 verify + 0 observe → no measurement rows."""
        run = _run_with_vector()
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        assert rows == []

    def test_underscore_observation_keys_skipped(self, tmp_path: Path) -> None:
        """Internal keys (``_started_at`` etc.) don't ride on the outputs lanes."""
        run = _run_with_vector(
            observations={"_internal": "skip me", "temperature": 23.5},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        assert _read_measurement_rows(parquet_path) == []
        outputs = decode_lane_structs(_read_step_rows(parquet_path)[0]["outputs"])
        assert outputs == {"temperature": 23.5}
        assert "_internal" not in outputs


# --------------------------------------------------------------------- #
# Item 10 — offline path                                                 #
# --------------------------------------------------------------------- #


class TestKindStabilityOffline:
    def test_mismatched_kind_across_vectors_does_not_raise(self, tmp_path: Path) -> None:
        """Same name, different kinds across vectors → no raise; each value
        routes to its own value_* lane."""
        run = TestRun(
            id=uuid4(),
            started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
            uut=UUT(serial="SN001"),
            outcome=Outcome.PASSED,
            steps=[
                TestStep(
                    name="test_x",
                    step_path="test_x",
                    outcome=Outcome.PASSED,
                    vectors=[
                        TestVector(
                            index=0,
                            outcome=Outcome.PASSED,
                            observations={"voltage": 3.31},  # scalar:float
                        ),
                        TestVector(
                            index=1,
                            outcome=Outcome.PASSED,
                            observations={"voltage": [1, 2, 3]},  # list
                        ),
                    ],
                )
            ],
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)  # does not raise
        step_rows = _read_step_rows(parquet_path)
        outputs_by_vec = {r["vector_index"]: decode_lane_structs(r["outputs"]) for r in step_rows}
        assert outputs_by_vec[0] == {"voltage": 3.31}
        assert outputs_by_vec[1] == {"voltage": [1, 2, 3]}

    def test_consistent_kind_across_vectors_ok(self, tmp_path: Path) -> None:
        """Same kind across vectors → no error."""
        run = TestRun(
            id=uuid4(),
            started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
            uut=UUT(serial="SN001"),
            outcome=Outcome.PASSED,
            steps=[
                TestStep(
                    name="test_x",
                    outcome=Outcome.PASSED,
                    vectors=[
                        TestVector(
                            index=0,
                            outcome=Outcome.PASSED,
                            observations={"voltage": 3.31},
                        ),
                        TestVector(
                            index=1,
                            outcome=Outcome.PASSED,
                            observations={"voltage": 3.32},
                        ),
                    ],
                )
            ],
        )
        backend = ParquetBackend(data_dir=tmp_path)
        # Should not raise — both float, same kind
        parquet_path = backend.save_test_run(run)
        assert parquet_path.exists()


# --------------------------------------------------------------------- #
# helpers — live path                                                    #
# --------------------------------------------------------------------- #


def _seeded_accumulator() -> tuple[EventAccumulator, dict]:
    """An accumulator with RunStarted + StepStarted already in.

    Returns the accumulator and a context dict for downstream events
    so each test can emit Observation / MeasurementRecorded / StepEnded
    against a consistent run/step identity.
    """
    acc = EventAccumulator()
    ctx = {
        "run_id": uuid4(),
        "session_id": uuid4(),
        "step_path": "test_capture",
        "step_name": "test_capture",
    }
    acc.on_event(
        RunStarted(
            session_id=ctx["session_id"],
            run_id=ctx["run_id"],
            station_id="st1",
            uut_serial="SN001",
            occurred_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        )
    )
    acc.on_event(
        StepStarted(
            session_id=ctx["session_id"],
            run_id=ctx["run_id"],
            step_name=ctx["step_name"],
            step_index=0,
            step_path=ctx["step_path"],
            inputs={"vin": 5.0},
            occurred_at=datetime(2026, 5, 31, 12, 0, 0, 100000, tzinfo=UTC),
        )
    )
    return acc, ctx


def _finalize(acc: EventAccumulator, ctx: dict, outputs: dict | None = None) -> None:
    """Close out the step + run."""
    acc.on_event(
        StepEnded(
            session_id=ctx["session_id"],
            run_id=ctx["run_id"],
            step_name=ctx["step_name"],
            step_index=0,
            step_path=ctx["step_path"],
            inputs={"vin": 5.0},
            outputs=outputs or {},
            outcome="passed",
            occurred_at=datetime(2026, 5, 31, 12, 0, 0, 900000, tzinfo=UTC),
        )
    )
    acc.on_event(
        RunEnded(
            session_id=ctx["session_id"],
            run_id=ctx["run_id"],
            outcome="passed",
            occurred_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
        )
    )


# --------------------------------------------------------------------- #
# Item 9 — live path                                                     #
# --------------------------------------------------------------------- #


class TestAutoPromotionLive:
    def test_observation_only_vector_emits_no_measurement_row(self, tmp_path: Path) -> None:
        """Live path mirrors offline: observation-only vector → NO measurement
        row; the observation rides on the step record's outputs lanes."""
        acc, ctx = _seeded_accumulator()
        acc.on_event(
            Observation(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_path=ctx["step_path"],
                vector_index=0,
                name="temperature",
                value=23.5,
                occurred_at=datetime(2026, 5, 31, 12, 0, 0, 500000, tzinfo=UTC),
            )
        )
        _finalize(acc, ctx, outputs={"temperature": 23.5})

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert parquet_path is not None
        assert _read_measurement_rows(parquet_path) == []
        step_rows = _read_step_rows(parquet_path)
        assert decode_lane_structs(step_rows[0]["outputs"]) == {"temperature": 23.5}

    def test_verify_present_no_done_promotion_live(self, tmp_path: Path) -> None:
        """≥1 verify in a vector → no DONE row for the observation."""
        acc, ctx = _seeded_accumulator()
        acc.on_event(
            Observation(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_path=ctx["step_path"],
                vector_index=0,
                name="temperature",
                value=23.5,
                occurred_at=datetime(2026, 5, 31, 12, 0, 0, 400000, tzinfo=UTC),
            )
        )
        acc.on_event(
            MeasurementRecorded(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_index=0,
                step_path=ctx["step_path"],
                measurement_name="vout",
                value=3.3,
                outcome="passed",
                outputs={"temperature": 23.5},
                occurred_at=datetime(2026, 5, 31, 12, 0, 0, 500000, tzinfo=UTC),
            )
        )
        _finalize(acc, ctx, outputs={"temperature": 23.5})

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert parquet_path is not None
        rows = _read_measurement_rows(parquet_path)

        # Only the verify row; no DONE row
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["measurement_outcome"] == "passed"
        # observation rides on the measurement row's outputs lanes
        assert decode_lane_structs(rows[0]["outputs"]) == {"temperature": 23.5}

    def test_multiple_observations_ride_on_step_record(self, tmp_path: Path) -> None:
        """Two observations in a verify-less vector → NO measurement row; both
        ride on the step record's outputs lanes."""
        acc, ctx = _seeded_accumulator()
        for name, value, micros in (
            ("temperature", 23.5, 300000),
            ("humidity", 45.0, 400000),
        ):
            acc.on_event(
                Observation(
                    session_id=ctx["session_id"],
                    run_id=ctx["run_id"],
                    step_name=ctx["step_name"],
                    step_path=ctx["step_path"],
                    vector_index=0,
                    name=name,
                    value=value,
                    occurred_at=datetime(2026, 5, 31, 12, 0, 0, micros, tzinfo=UTC),
                )
            )
        _finalize(acc, ctx, outputs={"temperature": 23.5, "humidity": 45.0})

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert parquet_path is not None
        assert _read_measurement_rows(parquet_path) == []
        outputs = decode_lane_structs(_read_step_rows(parquet_path)[0]["outputs"])
        assert outputs == {"temperature": 23.5, "humidity": 45.0}


# --------------------------------------------------------------------- #
# Item 10 — live path                                                    #
# --------------------------------------------------------------------- #


class TestKindStabilityLive:
    def test_mismatched_kind_across_observation_events_does_not_raise(self, tmp_path: Path) -> None:
        """Two observation events with same name + different kinds → no raise;
        each value routes to its own value_* lane."""
        acc, ctx = _seeded_accumulator()
        # First obs (vector 0): float
        acc.on_event(
            VectorStarted(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_index=0,
                step_path=ctx["step_path"],
                vector_index=0,
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_index=0,
                step_path=ctx["step_path"],
                vector_index=0,
                outcome="done",
                outputs={"voltage": 3.31},
            )
        )
        # Second obs (vector 1): list — different kind, same name
        acc.on_event(
            VectorStarted(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_index=0,
                step_path=ctx["step_path"],
                vector_index=1,
            )
        )
        acc.on_event(
            VectorEnded(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_index=0,
                step_path=ctx["step_path"],
                vector_index=1,
                outcome="done",
                outputs={"voltage": [1, 2, 3]},
            )
        )
        _finalize(acc, ctx)

        parquet_path = materialize_run_to_parquet(
            acc, tmp_path / "results", outcome="passed"
        )  # does not raise
        assert parquet_path is not None
        vrows = {
            r["vector_index"]: decode_lane_structs(r["outputs"])
            for r in pq.read_table(parquet_path).to_pylist()
            if r["record_type"] == "vector"
        }
        assert vrows[0] == {"voltage": 3.31}
        assert vrows[1] == {"voltage": [1, 2, 3]}

    def test_consistent_kind_across_observations_ok(self, tmp_path: Path) -> None:
        """Same kind across observation events → no error."""
        acc, ctx = _seeded_accumulator()
        for vi, value in enumerate([3.31, 3.32, 3.33]):
            acc.on_event(
                Observation(
                    session_id=ctx["session_id"],
                    run_id=ctx["run_id"],
                    step_name=ctx["step_name"],
                    step_path=ctx["step_path"],
                    vector_index=vi,
                    name="voltage",
                    value=value,
                    occurred_at=datetime(2026, 5, 31, 12, 0, 0, 200000 + vi * 100, tzinfo=UTC),
                )
            )
        _finalize(acc, ctx, outputs={"voltage": 3.33})

        parquet_path = materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")
        assert parquet_path is not None  # didn't raise


# --------------------------------------------------------------------- #
# observation_kind helper                                                #
# --------------------------------------------------------------------- #


class TestObservationKind:
    """Direct coverage of the kind classifier used by item 10."""

    @pytest.mark.parametrize(
        # Rename parameter from ``value`` to ``payload`` to avoid the
        # pytest plugin stamping mixed-type values into a single
        # ``in_value`` column at runtime — Arrow refuses mixed-type
        # columns during materialization. The param name ``value`` is
        # particularly poisonous because it collides with the canonical
        # ChannelStore row column. Other parametrize-with-mixed-types
        # tests should follow the same convention.
        "payload, expected",
        [
            ("channel://x?session=abc", "uri"),
            ("file://2026-05-31/abc/x.npz", "uri"),
            (True, "scalar:bool"),
            (False, "scalar:bool"),
            (42, "scalar:int"),
            (3.14, "scalar:float"),
            ("hello", "scalar:str"),
            ([1, 2, 3], "list"),
            ({"a": 1}, "dict"),
        ],
    )
    def test_classifier(self, payload, expected) -> None:
        from litmus.data.backends._row_helpers import observation_kind

        assert observation_kind(payload) == expected

    def test_bool_classified_before_int(self) -> None:
        """``True`` is also int in Python; bool branch must come first."""
        from litmus.data.backends._row_helpers import observation_kind

        assert observation_kind(True) == "scalar:bool"
        assert observation_kind(False) == "scalar:bool"
