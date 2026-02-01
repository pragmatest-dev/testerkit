"""Parquet storage backend for test results.

Implements an analysis-ready schema with one row per measurement and all
metadata denormalized for easy querying with DuckDB, Spark, Polars, etc.

Directory structure:
    results/runs/{date}/
    ├── {timestamp}_{serial}.parquet     # With serial (production)
    ├── {timestamp}.parquet              # Without serial (dev/debug)
    └── {timestamp}_{serial}_ref/        # Reference data (waveforms, images)

All timestamps are UTC for consistent cross-timezone analysis.

Schema design:
- One row per measurement
- All metadata denormalized onto each row
- Dynamic in_* columns for stimulus conditions
- Dynamic out_* columns for observations (scalars inline, large data in _ref/)
- Config snapshots in Parquet file-level metadata
"""

import json
import pickle
import shutil
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.models import TestRun, Waveform

# Prefix for path references in columns
REF_PATH_PREFIX = "_ref/"


class ParquetBackend:
    """Save test results to Parquet files with analysis-ready schema.

    Key design principles:
    1. One row per measurement - enables flexible queries
    2. All metadata denormalized - no joins needed
    3. Dynamic schema - in_* columns vary per test
    4. Config snapshots in file metadata - full reconstruction possible
    """

    def __init__(self, results_dir: Path | str = "results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_test_run(self, test_run: TestRun) -> Path:
        """Save test run to Parquet with analysis-ready schema.

        Creates files:
            results/runs/{date}/{timestamp}_{serial}.parquet  (with serial)
            results/runs/{date}/{timestamp}.parquet           (without serial)

        All timestamps are UTC for consistent cross-timezone analysis.

        Args:
            test_run: Complete TestRun with steps, vectors, and measurements.

        Returns:
            Path to the Parquet file.
        """
        # UTC timestamp for filename (compact ISO 8601 basic format)
        timestamp = test_run.started_at.strftime("%Y%m%dT%H%M%SZ")
        date_str = test_run.started_at.strftime("%Y-%m-%d")
        dut_serial = test_run.dut.serial.strip() if test_run.dut.serial else ""

        # Create date directory
        date_dir = self.results_dir / "runs" / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # Filename: timestamp first, serial if present
        if dut_serial:
            filename = f"{timestamp}_{dut_serial}.parquet"
        else:
            filename = f"{timestamp}.parquet"

        # Determine parquet path for _ref/ directory creation
        parquet_path = date_dir / filename

        # Build measurement rows (may create _ref/ directory for large data)
        rows = self._build_measurement_rows(test_run, parquet_path)

        if not rows:
            # No measurements - create empty file with minimal schema
            rows = [self._build_empty_row(test_run)]

        # Convert to PyArrow table
        table = pa.Table.from_pylist(rows)

        # Add file-level metadata (config snapshots)
        metadata = self._build_file_metadata(test_run)
        table = table.replace_schema_metadata(metadata)

        # Write to Parquet
        pq.write_table(table, parquet_path)

        return parquet_path

    def _build_measurement_rows(
        self, test_run: TestRun, parquet_path: Path
    ) -> list[dict[str, Any]]:
        """Build one row per measurement with all metadata denormalized."""
        rows = []

        # Compute run outcome
        run_outcome = test_run.outcome.value

        for step_idx, step in enumerate(test_run.steps):
            for vector in step.vectors:
                vector_outcome = vector.outcome.value if vector.outcome else None
                stimulus_cols = self._build_stimulus_columns(vector)
                observation_cols = self._build_observation_columns(
                    vector, parquet_path, str(vector.id)
                )

                for measurement in vector.measurements:
                    # Limit values are already float, no conversion needed
                    low = measurement.low_limit
                    high = measurement.high_limit
                    nom = measurement.nominal
                    val = measurement.value

                    row = {
                        # IDENTITY & TIMING
                        "run_id": str(test_run.id),
                        "run_started_at": test_run.started_at,
                        "run_ended_at": test_run.ended_at,
                        "step_name": step.name,
                        "step_index": step_idx,
                        "vector_index": vector.index,
                        "attempt": vector.attempt,
                        "vector_started_at": vector.started_at,
                        "vector_ended_at": vector.ended_at,
                        # WHO - Operator
                        "operator_id": test_run.operator_id,
                        "operator_name": test_run.operator_name,
                        # WHAT - DUT
                        "dut_serial": test_run.dut.serial,
                        "dut_part_number": test_run.dut.part_number,
                        "dut_revision": test_run.dut.revision,
                        "dut_lot_number": test_run.dut.lot_number,
                        # WHAT - Product
                        "product_id": test_run.product_id,
                        "product_name": test_run.product_name,
                        "product_revision": test_run.product_revision,
                        # WHERE - Station
                        "station_id": test_run.station_id,
                        "station_type": test_run.station_type,
                        "station_location": test_run.station_location,
                        # WHERE - Fixture
                        "fixture_id": test_run.fixture_id,
                        # WHAT - Test Context
                        "sequence_id": test_run.test_sequence_id,
                        "test_phase": test_run.test_phase,
                        "git_commit": test_run.git_commit,
                        # MEASUREMENT - Core
                        "measurement_name": measurement.name,
                        "measurement_timestamp": measurement.timestamp,
                        "value": val,
                        "units": measurement.units,
                        "outcome": measurement.outcome.value if measurement.outcome else None,
                        # Limits
                        "low_limit": low,
                        "high_limit": high,
                        "nominal": nom,
                        "comparator": measurement.comparator,
                        # Spec traceability
                        "spec_id": measurement.spec_id,
                        "spec_ref": measurement.spec_ref,
                        # ═══════════════════════════════════════════════════════════════
                        # MEASUREMENT SIGNAL PATH - How value was captured
                        # ═══════════════════════════════════════════════════════════════
                        "meas_dut_pin": measurement.dut_pin,
                        "meas_fixture_point": measurement.fixture_point,
                        "meas_instrument": measurement.instrument_name,
                        "meas_instrument_resource": measurement.instrument_resource,
                        "meas_instrument_channel": measurement.instrument_channel,
                        # ═══════════════════════════════════════════════════════════════
                        # ROLLUP OUTCOMES
                        # ═══════════════════════════════════════════════════════════════
                        "vector_outcome": vector_outcome,
                        "run_outcome": run_outcome,
                    }

                    # Add stimulus columns (dynamic in_* columns)
                    row.update(stimulus_cols)

                    # Add observation columns (dynamic out_* columns)
                    row.update(observation_cols)

                    # Add custom metadata columns
                    for key, value in test_run.custom_metadata.items():
                        row[key] = value

                    rows.append(row)

        return rows

    def _build_stimulus_columns(self, vector) -> dict[str, Any]:
        """Build dynamic in_* columns from vector params and stimulus records.

        For each input parameter, creates columns:
        - in_{param}: The value
        - in_{param}_instrument: Instrument name
        - in_{param}_resource: VISA address
        - in_{param}_channel: Channel
        - in_{param}_dut_pin: DUT pin driven
        - in_{param}_fixture_point: Fixture routing point
        """
        cols: dict[str, Any] = {}

        # First, add all vector params as in_{param} columns
        for param, value in vector.params.items():
            if param.startswith("_"):
                continue  # Skip internal params like _index
            col_name = f"in_{param}"
            cols[col_name] = value

        # Then, overlay stimulus signal path info
        for stim in vector.stimulus:
            param = stim.param
            prefix = f"in_{param}"

            # Value (may override from params, but stimulus is more authoritative)
            if stim.value is not None:
                cols[prefix] = stim.value

            # Signal path info
            if stim.instrument:
                cols[f"{prefix}_instrument"] = stim.instrument
            if stim.resource:
                cols[f"{prefix}_resource"] = stim.resource
            if stim.channel:
                cols[f"{prefix}_channel"] = stim.channel
            if stim.dut_pin:
                cols[f"{prefix}_dut_pin"] = stim.dut_pin
            if stim.fixture_point:
                cols[f"{prefix}_fixture_point"] = stim.fixture_point

        return cols

    def _build_observation_columns(
        self, vector, parquet_path: Path, vector_id: str
    ) -> dict[str, Any]:
        """Build dynamic out_* columns from vector observations.

        For each observation, creates columns:
        - out_{key}: The observed value (scalar) or path reference (large data)

        Observations are measured context (not commanded values):
        - Environmental readings (temperature, humidity)
        - Raw data (waveforms, images)
        - Actual readback values from instruments

        Storage by type:
        - Scalars (float, int, str, bool) → inline
        - Waveform → serialize to _ref/*.npz
        - ndarray → serialize to _ref/*.npy
        - Path → copy file to _ref/, preserve extension
        - Pydantic model → serialize to _ref/*.json
        - bytes → serialize to _ref/*.bin
        """
        cols: dict[str, Any] = {}

        if not hasattr(vector, "observations"):
            return cols

        for key, value in vector.observations.items():
            if key.startswith("_"):
                continue  # Skip internal keys

            col_name = f"out_{key}"

            # Scalars → inline
            if isinstance(value, (int, float, str, bool, type(None))):
                cols[col_name] = value

            # Structured types → serialize to _ref/
            elif isinstance(value, Path):
                cols[col_name] = self._save_file(parquet_path, vector_id, key, value)
            elif isinstance(value, Waveform):
                cols[col_name] = self._save_file(parquet_path, vector_id, key, value)
            elif isinstance(value, bytes):
                cols[col_name] = self._save_file(parquet_path, vector_id, key, value)
            elif hasattr(value, "tolist"):
                # numpy array - save to _ref/
                cols[col_name] = self._save_file(parquet_path, vector_id, key, value)
            elif hasattr(value, "model_dump"):
                # Pydantic model - save to _ref/
                cols[col_name] = self._save_file(parquet_path, vector_id, key, value)
            elif isinstance(value, (list, dict)):
                # Small lists/dicts → inline (will be serialized to JSON by PyArrow)
                cols[col_name] = value
            else:
                # Unknown → inline (may fail on non-serializable types)
                cols[col_name] = value

        return cols

    def _get_ref_dir(self, parquet_path: Path) -> Path:
        """Get or create the _ref directory for a parquet file."""
        # Replace .parquet with _ref
        ref_dir = parquet_path.parent / (parquet_path.stem + "_ref")
        ref_dir.mkdir(parents=True, exist_ok=True)
        return ref_dir

    def _save_file(
        self, parquet_path: Path, vector_id: str, key: str, value: Any
    ) -> str:
        """Save file in format appropriate for the data type.

        Returns:
            Path reference string like "_ref/abc123_scope_waveform.npz"
        """
        ref_dir = self._get_ref_dir(parquet_path)
        prefix = f"{vector_id[:8]}_{key}"

        if isinstance(value, Path):
            # File reference → copy, preserve extension
            ext = value.suffix or ".bin"
            filename = f"{prefix}{ext}"
            shutil.copy(value, ref_dir / filename)

        elif isinstance(value, Waveform):
            # Waveform → .npz with structure preserved
            filename = f"{prefix}.npz"
            try:
                import numpy as np

                np.savez(
                    ref_dir / filename,
                    Y=value.Y,
                    t0=value.t0,
                    dt=value.dt,
                    **value.attrs,
                )
            except ImportError:
                # Fallback to JSON if numpy not available
                filename = f"{prefix}.json"
                (ref_dir / filename).write_text(value.model_dump_json())

        elif isinstance(value, bytes):
            # Raw bytes → .bin
            filename = f"{prefix}.bin"
            (ref_dir / filename).write_bytes(value)

        elif hasattr(value, "model_dump"):
            # Pydantic model → .json
            filename = f"{prefix}.json"
            (ref_dir / filename).write_text(value.model_dump_json())

        elif hasattr(value, "tolist"):
            # numpy array → .npy
            filename = f"{prefix}.npy"
            try:
                import numpy as np

                np.save(ref_dir / filename, value)
            except ImportError:
                # Fallback to JSON if numpy not available
                filename = f"{prefix}.json"
                (ref_dir / filename).write_text(json.dumps(value.tolist()))

        else:
            # Fallback: pickle
            filename = f"{prefix}.pkl"
            with open(ref_dir / filename, "wb") as f:
                pickle.dump(value, f)

        return f"{REF_PATH_PREFIX}{filename}"

    def _build_empty_row(self, test_run: TestRun) -> dict[str, Any]:
        """Build a placeholder row when no measurements exist."""
        return {
            "run_id": str(test_run.id),
            "run_started_at": test_run.started_at,
            "run_ended_at": test_run.ended_at,
            "step_name": None,
            "step_index": None,
            "vector_index": None,
            "attempt": None,
            "vector_started_at": None,
            "vector_ended_at": None,
            "operator_id": test_run.operator_id,
            "operator_name": test_run.operator_name,
            "dut_serial": test_run.dut.serial,
            "dut_part_number": test_run.dut.part_number,
            "dut_revision": test_run.dut.revision,
            "dut_lot_number": test_run.dut.lot_number,
            "product_id": test_run.product_id,
            "product_name": test_run.product_name,
            "product_revision": test_run.product_revision,
            "station_id": test_run.station_id,
            "station_type": test_run.station_type,
            "station_location": test_run.station_location,
            "fixture_id": test_run.fixture_id,
            "sequence_id": test_run.test_sequence_id,
            "test_phase": test_run.test_phase,
            "git_commit": test_run.git_commit,
            "measurement_name": None,
            "measurement_timestamp": None,
            "value": None,
            "units": None,
            "outcome": None,
            "low_limit": None,
            "high_limit": None,
            "nominal": None,
            "comparator": None,
            "spec_id": None,
            "spec_ref": None,
            "meas_dut_pin": None,
            "meas_fixture_point": None,
            "meas_instrument": None,
            "meas_instrument_resource": None,
            "meas_instrument_channel": None,
            "vector_outcome": None,
            "run_outcome": test_run.outcome.value,
        }

    def _build_file_metadata(self, test_run: TestRun) -> dict[bytes, bytes]:
        """Build Parquet file-level metadata with config snapshots."""
        metadata: dict[bytes, bytes] = {}

        if test_run.station_config_yaml:
            metadata[b"station_config_yaml"] = test_run.station_config_yaml.encode("utf-8")
        if test_run.product_spec_yaml:
            metadata[b"product_spec_yaml"] = test_run.product_spec_yaml.encode("utf-8")
        if test_run.fixture_config_yaml:
            metadata[b"fixture_config_yaml"] = test_run.fixture_config_yaml.encode("utf-8")
        if test_run.test_config_yaml:
            metadata[b"test_config_yaml"] = test_run.test_config_yaml.encode("utf-8")

        # Add some convenience metadata
        metadata[b"litmus_version"] = b"1.0.0"
        metadata[b"schema_version"] = b"2.0"  # New analysis-ready schema

        return metadata

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent test runs.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            List of test run summary records, most recent first.
        """
        runs = []
        runs_dir = self.results_dir / "runs"

        if not runs_dir.exists():
            return runs

        # Collect all measurement files across date/run directories
        parquet_files = sorted(runs_dir.rglob("measurements.parquet"), reverse=True)

        for pq_file in parquet_files:
            if len(runs) >= limit:
                break

            try:
                table = pq.read_table(pq_file)
                if table.num_rows == 0:
                    continue

                # Get first row for run-level info (all rows have same run metadata)
                row = table.to_pylist()[0]

                # Build summary record
                summary = {
                    "test_run_id": row.get("run_id"),
                    "started_at": row.get("run_started_at"),
                    "ended_at": row.get("run_ended_at"),
                    "dut_serial": row.get("dut_serial"),
                    "dut_part_number": row.get("dut_part_number"),
                    "product_id": row.get("product_id"),
                    "station_id": row.get("station_id"),
                    "station_type": row.get("station_type"),
                    "test_sequence_id": row.get("sequence_id"),
                    "test_phase": row.get("test_phase"),
                    "operator": row.get("operator_id"),
                    "outcome": row.get("run_outcome"),
                    "total_measurements": table.num_rows,
                    "failed_measurements": sum(
                        1 for r in table.to_pylist() if r.get("outcome") == "fail"
                    ),
                }
                runs.append(summary)
            except Exception:
                continue  # Skip corrupted files

        # Sort by started_at descending
        runs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return runs[:limit]

    def get_run(self, run_id: str) -> dict | None:
        """Get a specific test run by ID.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            Test run summary record or None if not found.
        """
        runs_dir = self.results_dir / "runs"

        if not runs_dir.exists():
            return None

        # Search for matching run directory
        for date_dir in runs_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for run_dir in date_dir.iterdir():
                if run_id in run_dir.name:
                    measurements_file = run_dir / "measurements.parquet"
                    if measurements_file.exists():
                        table = pq.read_table(measurements_file)
                        if table.num_rows > 0:
                            row = table.to_pylist()[0]
                            return {
                                "test_run_id": row.get("run_id"),
                                "started_at": row.get("run_started_at"),
                                "ended_at": row.get("run_ended_at"),
                                "dut_serial": row.get("dut_serial"),
                                "dut_part_number": row.get("dut_part_number"),
                                "product_id": row.get("product_id"),
                                "station_id": row.get("station_id"),
                                "station_type": row.get("station_type"),
                                "test_sequence_id": row.get("sequence_id"),
                                "test_phase": row.get("test_phase"),
                                "operator": row.get("operator_id"),
                                "outcome": row.get("run_outcome"),
                                "total_measurements": table.num_rows,
                            }

        return None

    def get_measurements(self, run_id: str) -> list[dict]:
        """Get all measurements for a specific test run.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            List of measurement records for the run.
        """
        runs_dir = self.results_dir / "runs"

        if not runs_dir.exists():
            return []

        # Search for matching run directory
        for date_dir in runs_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for run_dir in date_dir.iterdir():
                if run_id in run_dir.name:
                    measurements_file = run_dir / "measurements.parquet"
                    if measurements_file.exists():
                        table = pq.read_table(measurements_file)
                        return table.to_pylist()

        return []

    def get_vectors(self, run_id: str) -> list[dict]:
        """Get unique test vectors for a specific test run.

        Extracts unique (step_name, vector_index, attempt) combinations
        from the measurements table.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            List of vector records for the run.
        """
        measurements = self.get_measurements(run_id)
        if not measurements:
            return []

        # Group by (step_name, vector_index, attempt)
        vectors_seen: dict[tuple, dict] = {}
        for m in measurements:
            key = (m.get("step_name"), m.get("vector_index"), m.get("attempt"))
            if key not in vectors_seen:
                # Extract vector-level info
                vector_info = {
                    "test_run_id": m.get("run_id"),
                    "step_name": m.get("step_name"),
                    "index": m.get("vector_index"),
                    "attempt": m.get("attempt"),
                    "outcome": m.get("vector_outcome"),
                    "started_at": m.get("vector_started_at"),
                    "ended_at": m.get("vector_ended_at"),
                    "dut_serial": m.get("dut_serial"),
                    "product_id": m.get("product_id"),
                    "station_id": m.get("station_id"),
                    "sequence_id": m.get("sequence_id"),
                }
                # Extract params (in_* columns without suffix)
                params = {}
                for k, v in m.items():
                    if k.startswith("in_") and "_" not in k[3:]:
                        # e.g., in_vin but not in_vin_instrument
                        param_name = k[3:]  # Remove "in_" prefix
                        params[param_name] = v
                vector_info["params"] = params
                vectors_seen[key] = vector_info

        return list(vectors_seen.values())

    def get_run_metadata(self, run_id: str) -> dict[str, str] | None:
        """Get file-level metadata (config snapshots) for a run.

        Args:
            run_id: The test run ID (can be partial).

        Returns:
            Dict with config YAML strings, or None if not found.
        """
        runs_dir = self.results_dir / "runs"

        if not runs_dir.exists():
            return None

        for date_dir in runs_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for run_dir in date_dir.iterdir():
                if run_id in run_dir.name:
                    measurements_file = run_dir / "measurements.parquet"
                    if measurements_file.exists():
                        pf = pq.ParquetFile(measurements_file)
                        raw_metadata = pf.schema_arrow.metadata or {}
                        # Decode bytes to strings
                        return {
                            k.decode("utf-8"): v.decode("utf-8")
                            for k, v in raw_metadata.items()
                        }

        return None


def load_file(parquet_path: Path, ref: str) -> Any:
    """Load a file from _ref/ directory based on its extension.

    Args:
        parquet_path: Path to the parquet file (used to locate _ref/ dir).
        ref: Reference string like "_ref/abc123_waveform.npz".

    Returns:
        Loaded data in appropriate format:
        - .npz → Waveform model (if has Y, t0, dt) or dict
        - .npy → numpy array
        - .json → dict or Pydantic model
        - .bin → bytes
        - .pkl → pickled object
        - Other → raw file path
    """
    if not ref.startswith(REF_PATH_PREFIX):
        return ref  # Not a reference, return as-is

    # Get path relative to parquet file
    ref_dir = parquet_path.parent / (parquet_path.stem + "_ref")
    filename = ref[len(REF_PATH_PREFIX) :]
    path = ref_dir / filename
    ext = path.suffix.lower()

    if not path.exists():
        return ref  # File not found, return reference

    if ext == ".npz":
        try:
            import numpy as np

            data = dict(np.load(path, allow_pickle=True))
            # Check if this looks like a Waveform
            if "Y" in data and "t0" in data and "dt" in data:
                attrs = {k: v for k, v in data.items() if k not in ("Y", "t0", "dt")}
                return Waveform(
                    Y=data["Y"].tolist(),
                    t0=float(data["t0"]),
                    dt=float(data["dt"]),
                    attrs=attrs,
                )
            return data
        except ImportError:
            return path

    elif ext == ".npy":
        try:
            import numpy as np

            return np.load(path)
        except ImportError:
            return path

    elif ext == ".json":
        return json.loads(path.read_text())

    elif ext == ".bin":
        return path.read_bytes()

    elif ext == ".pkl":
        with open(path, "rb") as f:
            return pickle.load(f)

    else:
        # Return path for other file types
        return path


def is_file_reference(value: Any) -> bool:
    """Check if a value is a _ref/ file reference."""
    return isinstance(value, str) and value.startswith(REF_PATH_PREFIX)
