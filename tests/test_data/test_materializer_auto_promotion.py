"""Build items 9 + 10 — auto-promotion rule + type-stable out_<name>.

Two materialization paths in the codebase:

- **offline path** — :meth:`ParquetBackend._build_measurement_rows`
  walks a pre-built :class:`TestRun` (e.g. from ``LitmusClient``)
  and emits rows from ``vector.measurements`` directly.
- **live path** — :class:`EventAccumulator` projects an event
  stream; :func:`materialize_run_to_parquet` writes its state out
  at ``RunEnded``. Used by the runs daemon for real pytest runs.

Item 9 (auto-promotion): per vector at materialization —

| Vector contained | Row emission |
|---|---|
| ≥1 verify | verify rows only; observations ride as ``out_*`` |
| 0 verify, ≥1 observe | each observation → DONE row (value=NULL, outcome=DONE) |
| 0 of either | no row |

Item 10 (kind stability): the first observation of a name pins
its kind; subsequent observations of the same name must match. A
mismatch raises ``ValueError`` at materialization rather than
letting the parquet column carry mixed types.

Both rules apply to **both paths** — covered here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.data.backends._event_accumulator import EventAccumulator
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
)
from litmus.data.models import (
    DUT,
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
        dut=DUT(serial="SN001"),
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


# --------------------------------------------------------------------- #
# Item 9 — offline path                                                  #
# --------------------------------------------------------------------- #


class TestAutoPromotionOffline:
    def test_observation_only_vector_promotes_each_observation(self, tmp_path: Path) -> None:
        """Per spec §7: 0 verify + ≥1 observe → each observation → DONE row."""
        run = _run_with_vector(
            observations={"temperature": 23.5, "humidity": 45.0},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        assert len(rows) == 2

        names = sorted(r["measurement_name"] for r in rows)
        assert names == ["humidity", "temperature"]

        # Each promoted row: value=None, outcome=DONE
        for r in rows:
            assert r["measurement_value"] is None
            assert r["measurement_outcome"] == "done"

        # And the observation values ride as out_* on each row
        out_keys = {k for r in rows for k in r if k.startswith("out_")}
        assert "out_temperature" in out_keys
        assert "out_humidity" in out_keys

    def test_verify_present_no_done_promotion(self, tmp_path: Path) -> None:
        """≥1 verify → verify rows only; observations ride along."""
        run = _run_with_vector(
            measurements=[
                Measurement(name="vout", value=3.3, outcome=Outcome.PASSED),
            ],
            observations={"temperature": 23.5},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        # Only the verify row, no DONE promotion for the observation
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["measurement_outcome"] == "passed"
        # The observation still rides on the row as out_temperature
        assert rows[0]["out_temperature"] == 23.5

    def test_empty_vector_emits_no_measurement_rows(self, tmp_path: Path) -> None:
        """0 verify + 0 observe → no measurement rows."""
        run = _run_with_vector()
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        assert rows == []

    def test_underscore_observation_keys_skipped(self, tmp_path: Path) -> None:
        """Internal keys (``_started_at`` etc.) don't promote to DONE rows."""
        run = _run_with_vector(
            observations={"_internal": "skip me", "temperature": 23.5},
        )
        backend = ParquetBackend(data_dir=tmp_path)
        parquet_path = backend.save_test_run(run)

        rows = _read_measurement_rows(parquet_path)
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "temperature"


# --------------------------------------------------------------------- #
# Item 10 — offline path                                                 #
# --------------------------------------------------------------------- #


class TestKindStabilityOffline:
    def test_mismatched_kind_across_vectors_raises(self, tmp_path: Path) -> None:
        """First observation registers kind; mismatch raises ValueError."""
        run = TestRun(
            id=uuid4(),
            started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
            dut=DUT(serial="SN001"),
            outcome=Outcome.PASSED,
            steps=[
                TestStep(
                    name="test_x",
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
        with pytest.raises(ValueError, match="out_voltage kind mismatch"):
            backend.save_test_run(run)

    def test_consistent_kind_across_vectors_ok(self, tmp_path: Path) -> None:
        """Same kind across vectors → no error."""
        run = TestRun(
            id=uuid4(),
            started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
            dut=DUT(serial="SN001"),
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
            dut_serial="SN001",
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
    def test_observation_only_vector_promotes_to_done_row(self, tmp_path: Path) -> None:
        """Live path mirrors offline: observation-only vector → DONE rows."""
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
        rows = _read_measurement_rows(parquet_path)

        assert len(rows) == 1
        row = rows[0]
        assert row["measurement_name"] == "temperature"
        assert row["measurement_value"] is None
        assert row["measurement_outcome"] == "done"
        # observation value rides as out_temperature
        assert row["out_temperature"] == 23.5

    def test_verify_present_no_done_promotion_live(self, tmp_path: Path) -> None:
        """≥1 verify in a vector → no DONE promotion for the observation."""
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

        # Only the verify row; no DONE promotion
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "vout"
        assert rows[0]["measurement_outcome"] == "passed"
        # observation still rides as out_*
        assert rows[0]["out_temperature"] == 23.5

    def test_multiple_observations_each_promote_to_own_row(self, tmp_path: Path) -> None:
        """Two observations in a verify-less vector → two DONE rows."""
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
        rows = _read_measurement_rows(parquet_path)

        assert len(rows) == 2
        names = sorted(r["measurement_name"] for r in rows)
        assert names == ["humidity", "temperature"]
        # Each DONE row carries both observations in out_*
        for r in rows:
            assert r["out_temperature"] == 23.5
            assert r["out_humidity"] == 45.0
            assert r["measurement_outcome"] == "done"


# --------------------------------------------------------------------- #
# Item 10 — live path                                                    #
# --------------------------------------------------------------------- #


class TestKindStabilityLive:
    def test_mismatched_kind_across_observation_events_raises(self, tmp_path: Path) -> None:
        """Two observation events with same name + different kinds → ValueError."""
        acc, ctx = _seeded_accumulator()
        # First obs: float
        acc.on_event(
            Observation(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_path=ctx["step_path"],
                vector_index=0,
                name="voltage",
                value=3.31,
                occurred_at=datetime(2026, 5, 31, 12, 0, 0, 200000, tzinfo=UTC),
            )
        )
        # Second obs same name, different kind (list)
        acc.on_event(
            Observation(
                session_id=ctx["session_id"],
                run_id=ctx["run_id"],
                step_name=ctx["step_name"],
                step_path=ctx["step_path"],
                vector_index=1,
                name="voltage",
                value=[1, 2, 3],
                occurred_at=datetime(2026, 5, 31, 12, 0, 0, 300000, tzinfo=UTC),
            )
        )
        _finalize(acc, ctx)

        with pytest.raises(ValueError, match="out_voltage kind mismatch"):
            materialize_run_to_parquet(acc, tmp_path / "results", outcome="passed")

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
        "value, expected",
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
    def test_classifier(self, value, expected) -> None:
        from litmus.data.backends._row_helpers import observation_kind

        assert observation_kind(value) == expected

    def test_bool_classified_before_int(self) -> None:
        """``True`` is also int in Python; bool branch must come first."""
        from litmus.data.backends._row_helpers import observation_kind

        assert observation_kind(True) == "scalar:bool"
        assert observation_kind(False) == "scalar:bool"
