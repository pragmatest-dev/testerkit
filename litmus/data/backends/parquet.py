"""Parquet storage backend for test results."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.models import Outcome, TestRun


class ParquetBackend:
    """Save test results to Parquet files.

    Stores three types of records:
    1. Test runs - one row per test execution
    2. Test vectors - one row per vector (parameter combination) per step
    3. Measurements - one row per measurement, referencing vector

    The vector params are stored once per vector, not duplicated on each
    measurement. Measurements reference vectors by ID.
    """

    def __init__(self, results_dir: Path | str = "results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_test_run(self, test_run: TestRun) -> Path:
        """Save test run to Parquet, return path."""
        date_str = test_run.started_at.strftime("%Y-%m-%d")
        run_dir = self.results_dir / "test_runs" / date_str
        run_dir.mkdir(parents=True, exist_ok=True)

        # Count total vectors across all steps
        total_vectors = sum(len(step.vectors) for step in test_run.steps)
        failed_vectors = sum(
            sum(1 for v in step.vectors if v.outcome != Outcome.PASS) for step in test_run.steps
        )

        # Build test run record
        run_data = {
            "test_run_id": [str(test_run.id)],
            "started_at": [test_run.started_at],
            "ended_at": [test_run.ended_at],
            "dut_serial": [test_run.dut.serial],
            "station_id": [test_run.station_id],
            "test_sequence_id": [test_run.test_sequence_id],
            "test_phase": [test_run.test_phase],
            "outcome": [test_run.outcome.value],
            "total_steps": [len(test_run.steps)],
            "failed_steps": [sum(1 for s in test_run.steps if s.outcome != Outcome.PASS)],
            "total_vectors": [total_vectors],
            "failed_vectors": [failed_vectors],
        }

        run_table = pa.Table.from_pydict(run_data)
        run_path = run_dir / f"{test_run.id}.parquet"
        pq.write_table(run_table, run_path)

        # Save vectors and measurements
        self._save_vectors(test_run, date_str)
        self._save_measurements(test_run, date_str)

        return run_path

    def _save_vectors(self, test_run: TestRun, date_str: str):
        """Save test vectors table.

        Each row represents one parameter combination execution.
        Params are stored as JSON string for flexibility.
        """
        vec_dir = self.results_dir / "vectors" / date_str
        vec_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for step in test_run.steps:
            for tv in step.vectors:
                rows.append(
                    {
                        "test_run_id": str(test_run.id),
                        "test_vector_id": str(tv.id),
                        "test_step_id": str(step.id),
                        "step_name": step.name,
                        "index": tv.index,
                        "params": json.dumps(tv.params),  # Store as JSON string
                        "attempt": tv.attempt,
                        "max_attempts": tv.max_attempts,
                        "outcome": tv.outcome.value,
                        "started_at": tv.started_at,
                        "ended_at": tv.ended_at,
                        "error_message": tv.error_message,
                        "dut_serial": test_run.dut.serial,
                        "station_id": test_run.station_id,
                    }
                )

        if rows:
            table = pa.Table.from_pylist(rows)
            path = vec_dir / f"{test_run.id}_vectors.parquet"
            pq.write_table(table, path)

    def _save_measurements(self, test_run: TestRun, date_str: str):
        """Save flattened measurements table.

        Measurements reference their parent vector by ID. Vector params
        are NOT duplicated here - join with vectors table for full context.
        """
        meas_dir = self.results_dir / "measurements" / date_str
        meas_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for step in test_run.steps:
            # Handle measurements from vectors (new pattern)
            for tv in step.vectors:
                for m in tv.measurements:
                    rows.append(
                        {
                            "test_run_id": str(test_run.id),
                            "test_vector_id": str(tv.id),
                            "step_name": step.name,
                            "vector_index": tv.index,
                            "measurement_name": m.name,
                            "value": float(m.value) if m.value else None,
                            "units": m.units,
                            "low_limit": float(m.low_limit) if m.low_limit else None,
                            "high_limit": float(m.high_limit) if m.high_limit else None,
                            "nominal": float(m.nominal) if m.nominal else None,
                            "outcome": m.outcome.value if m.outcome else None,
                            "spec_ref": m.spec_ref,
                            "timestamp": m.timestamp,
                            "dut_serial": test_run.dut.serial,
                            "station_id": test_run.station_id,
                        }
                    )

            # Handle legacy direct measurements on step (backward compat)
            for m in step.measurements:
                rows.append(
                    {
                        "test_run_id": str(test_run.id),
                        "test_vector_id": None,  # No vector for legacy measurements
                        "step_name": step.name,
                        "vector_index": None,
                        "measurement_name": m.name,
                        "value": float(m.value) if m.value else None,
                        "units": m.units,
                        "low_limit": float(m.low_limit) if m.low_limit else None,
                        "high_limit": float(m.high_limit) if m.high_limit else None,
                        "nominal": float(m.nominal) if m.nominal else None,
                        "outcome": m.outcome.value if m.outcome else None,
                        "spec_ref": m.spec_ref,
                        "timestamp": m.timestamp,
                        "dut_serial": test_run.dut.serial,
                        "station_id": test_run.station_id,
                    }
                )

        if rows:
            table = pa.Table.from_pylist(rows)
            path = meas_dir / f"{test_run.id}_measurements.parquet"
            pq.write_table(table, path)
