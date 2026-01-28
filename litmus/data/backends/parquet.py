"""Parquet storage backend for test results."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.models import PassFail, TestRun


class ParquetBackend:
    """Save test results to Parquet files."""

    def __init__(self, results_dir: Path | str = "results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_test_run(self, test_run: TestRun) -> Path:
        """Save test run to Parquet, return path."""
        date_str = test_run.started_at.strftime("%Y-%m-%d")
        run_dir = self.results_dir / "test_runs" / date_str
        run_dir.mkdir(parents=True, exist_ok=True)

        # Build test run record
        run_data = {
            "test_run_id": [str(test_run.id)],
            "started_at": [test_run.started_at],
            "ended_at": [test_run.ended_at],
            "dut_serial": [test_run.dut.serial],
            "station_id": [test_run.station_id],
            "test_sequence_id": [test_run.test_sequence_id],
            "test_phase": [test_run.test_phase],
            "pass_fail": [test_run.pass_fail.value],
            "total_steps": [len(test_run.steps)],
            "failed_steps": [sum(1 for s in test_run.steps if s.pass_fail != PassFail.PASS)],
        }

        run_table = pa.Table.from_pydict(run_data)
        run_path = run_dir / f"{test_run.id}.parquet"
        pq.write_table(run_table, run_path)

        # Build denormalized measurements
        self._save_measurements(test_run, date_str)

        return run_path

    def _save_measurements(self, test_run: TestRun, date_str: str):
        """Save flattened measurements table."""
        meas_dir = self.results_dir / "measurements" / date_str
        meas_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for step in test_run.steps:
            for m in step.measurements:
                rows.append(
                    {
                        "test_run_id": str(test_run.id),
                        "step_name": step.name,
                        "measurement_name": m.name,
                        "value": float(m.value) if m.value else None,
                        "units": m.units,
                        "low_limit": float(m.low_limit) if m.low_limit else None,
                        "high_limit": float(m.high_limit) if m.high_limit else None,
                        "pass_fail": m.pass_fail.value if m.pass_fail else None,
                        "timestamp": m.timestamp,
                        "dut_serial": test_run.dut.serial,
                        "station_id": test_run.station_id,
                    }
                )

        if rows:
            table = pa.Table.from_pylist(rows)
            path = meas_dir / f"{test_run.id}_measurements.parquet"
            pq.write_table(table, path)
