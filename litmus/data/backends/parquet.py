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
            # DUT traceability
            "dut_serial": [test_run.dut.serial],
            "dut_part_number": [test_run.dut.part_number],
            "dut_revision": [test_run.dut.revision],
            # Product traceability
            "product_id": [getattr(test_run, "product_id", None)],
            # Station traceability
            "station_id": [test_run.station_id],
            "station_type": [test_run.station_type],
            # Sequence traceability
            "test_sequence_id": [test_run.test_sequence_id],
            "test_phase": [test_run.test_phase],
            # Operator
            "operator": [test_run.operator],
            # Results
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
                        # Traceability - denormalized for query convenience
                        "dut_serial": test_run.dut.serial,
                        "product_id": getattr(test_run, "product_id", None),
                        "station_id": test_run.station_id,
                        "sequence_id": test_run.test_sequence_id,
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
                            # Identity
                            "test_run_id": str(test_run.id),
                            "test_vector_id": str(tv.id),
                            "step_name": step.name,
                            "vector_index": tv.index,
                            # Measurement data
                            "measurement_name": m.name,
                            "value": float(m.value) if m.value else None,
                            "units": m.units,
                            "low_limit": float(m.low_limit) if m.low_limit else None,
                            "high_limit": float(m.high_limit) if m.high_limit else None,
                            "nominal": float(m.nominal) if m.nominal else None,
                            "outcome": m.outcome.value if m.outcome else None,
                            "comparator": m.comparator,
                            "timestamp": m.timestamp,
                            # Spec traceability
                            "spec_ref": m.spec_ref,
                            # Product traceability
                            "product_id": getattr(test_run, "product_id", None),
                            # DUT traceability
                            "dut_serial": test_run.dut.serial,
                            # Station traceability
                            "station_id": test_run.station_id,
                            # Sequence traceability
                            "sequence_id": test_run.test_sequence_id,
                            # Signal path traceability (fixture → instrument)
                            "dut_pin": m.dut_pin,
                            "fixture_point": m.fixture_point,
                            "instrument_name": m.instrument_name,
                            "instrument_resource": m.instrument_resource,
                            "instrument_channel": m.instrument_channel,
                        }
                    )

        if rows:
            table = pa.Table.from_pylist(rows)
            path = meas_dir / f"{test_run.id}_measurements.parquet"
            pq.write_table(table, path)

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent test runs.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            List of test run records, most recent first.
        """
        runs = []
        runs_dir = self.results_dir / "test_runs"

        if not runs_dir.exists():
            return runs

        # Collect all parquet files across date directories
        parquet_files = sorted(runs_dir.rglob("*.parquet"), reverse=True)

        for pq_file in parquet_files[:limit]:
            table = pq.read_table(pq_file)
            for row in table.to_pylist():
                runs.append(row)
                if len(runs) >= limit:
                    break
            if len(runs) >= limit:
                break

        # Sort by started_at descending
        runs.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return runs[:limit]

    def get_run(self, run_id: str) -> dict | None:
        """Get a specific test run by ID.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            Test run record or None if not found.
        """
        runs_dir = self.results_dir / "test_runs"

        if not runs_dir.exists():
            return None

        # Search through parquet files
        for pq_file in runs_dir.rglob("*.parquet"):
            # Check if filename matches (run_id is in filename)
            if run_id in pq_file.stem:
                table = pq.read_table(pq_file)
                rows = table.to_pylist()
                if rows:
                    return rows[0]

        # Full scan if not found by filename
        for pq_file in runs_dir.rglob("*.parquet"):
            table = pq.read_table(pq_file)
            for row in table.to_pylist():
                if row.get("test_run_id", "").startswith(run_id):
                    return row

        return None

    def get_measurements(self, run_id: str) -> list[dict]:
        """Get measurements for a specific test run.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            List of measurement records for the run.
        """
        measurements = []
        meas_dir = self.results_dir / "measurements"

        if not meas_dir.exists():
            return measurements

        # Search for matching measurement files
        for pq_file in meas_dir.rglob("*.parquet"):
            # Check filename for run_id
            if run_id in pq_file.stem:
                table = pq.read_table(pq_file)
                measurements.extend(table.to_pylist())

        # If no matches by filename, do full scan
        if not measurements:
            for pq_file in meas_dir.rglob("*.parquet"):
                table = pq.read_table(pq_file)
                for row in table.to_pylist():
                    if row.get("test_run_id", "").startswith(run_id):
                        measurements.append(row)

        return measurements

    def get_vectors(self, run_id: str) -> list[dict]:
        """Get test vectors for a specific test run.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            List of vector records for the run.
        """
        vectors = []
        vec_dir = self.results_dir / "vectors"

        if not vec_dir.exists():
            return vectors

        # Search for matching vector files
        for pq_file in vec_dir.rglob("*.parquet"):
            if run_id in pq_file.stem:
                table = pq.read_table(pq_file)
                vectors.extend(table.to_pylist())

        # If no matches by filename, do full scan
        if not vectors:
            for pq_file in vec_dir.rglob("*.parquet"):
                table = pq.read_table(pq_file)
                for row in table.to_pylist():
                    if row.get("test_run_id", "").startswith(run_id):
                        vectors.append(row)

        return vectors
