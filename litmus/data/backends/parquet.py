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
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.backends._row_helpers import build_measurement_fields, build_run_metadata
from litmus.data.models import TestRun, Waveform

# Prefix for path references in columns
REF_PATH_PREFIX = "_ref/"

# Canonical schema for fixed columns. Dynamic columns (in_*, out_*, instr_*, custom)
# are NOT listed here — they pass through with inferred types.
MEASUREMENT_SCHEMA = pa.schema([
    # Identity & timing
    ("run_id", pa.string()),
    ("run_started_at", pa.timestamp("us", tz="UTC")),
    ("run_ended_at", pa.timestamp("us", tz="UTC")),
    ("step_name", pa.string()),
    ("step_index", pa.int64()),
    ("step_started_at", pa.timestamp("us", tz="UTC")),
    ("step_ended_at", pa.timestamp("us", tz="UTC")),
    ("vector_index", pa.int64()),
    ("attempt", pa.int64()),
    ("vector_started_at", pa.timestamp("us", tz="UTC")),
    ("vector_ended_at", pa.timestamp("us", tz="UTC")),
    # Who
    ("operator_id", pa.string()),
    ("operator_name", pa.string()),
    # DUT
    ("dut_serial", pa.string()),
    ("dut_part_number", pa.string()),
    ("dut_revision", pa.string()),
    ("dut_lot_number", pa.string()),
    # Product
    ("product_id", pa.string()),
    ("product_name", pa.string()),
    ("product_revision", pa.string()),
    # Station
    ("station_id", pa.string()),
    ("station_name", pa.string()),
    ("station_type", pa.string()),
    ("station_location", pa.string()),
    # Fixture
    ("fixture_id", pa.string()),
    # Test context
    ("sequence_id", pa.string()),
    ("test_phase", pa.string()),
    ("git_commit", pa.string()),
    # Measurement core
    ("measurement_name", pa.string()),
    ("measurement_timestamp", pa.timestamp("us", tz="UTC")),
    ("value", pa.float64()),
    ("units", pa.string()),
    ("outcome", pa.string()),
    # Limits
    ("low_limit", pa.float64()),
    ("high_limit", pa.float64()),
    ("nominal", pa.float64()),
    ("comparator", pa.string()),
    # Spec traceability
    ("spec_id", pa.string()),
    ("spec_ref", pa.string()),
    # Signal path
    ("meas_dut_pin", pa.string()),
    ("meas_fixture_point", pa.string()),
    ("meas_instrument", pa.string()),
    ("meas_instrument_resource", pa.string()),
    ("meas_instrument_channel", pa.string()),
    # Rollup
    ("vector_outcome", pa.string()),
    ("run_outcome", pa.string()),
    # Environment traceability
    ("python_version", pa.string()),
    ("litmus_version", pa.string()),
    ("env_fingerprint", pa.string()),
])

_SCHEMA_DICT = {f.name: f.type for f in MEASUREMENT_SCHEMA}

_TIMESTAMP_COLS = {
    "run_started_at", "run_ended_at", "vector_started_at", "vector_ended_at",
    "measurement_timestamp", "step_started_at", "step_ended_at",
}


def _enforce_schema(table: pa.Table) -> pa.Table:
    """Normalize column types to match MEASUREMENT_SCHEMA.

    For each column in the table that appears in the canonical schema:
    - If the type already matches, no-op.
    - If the column is null-typed, cast to the target type.
    - If the column is an extension type (uuid, json) or string where timestamp
      expected, rebuild via to_pylist() round-trip.

    Dynamic columns not in the schema pass through unchanged.
    """
    columns = []
    names = []

    for i, field in enumerate(table.schema):
        col = table.column(i)
        target_type = _SCHEMA_DICT.get(field.name)

        if target_type is None or field.type == target_type:
            # Dynamic column or already correct
            columns.append(col)
            names.append(field.name)
            continue

        if pa.types.is_null(field.type):
            # All nulls — cast to target
            columns.append(col.cast(target_type))
            names.append(field.name)
            continue

        # Extension types or type mismatches — rebuild via pylist
        values = col.to_pylist()

        if pa.types.is_timestamp(target_type):
            # Parse string timestamps
            parsed = []
            for v in values:
                if isinstance(v, str):
                    parsed.append(datetime.fromisoformat(v.replace("Z", "+00:00")))
                else:
                    parsed.append(v)
            columns.append(pa.array(parsed, type=target_type))
        elif target_type == pa.float64():
            # Coerce to float (handles json extension → float)
            parsed = []
            for v in values:
                if v is None:
                    parsed.append(None)
                else:
                    try:
                        parsed.append(float(v))
                    except (TypeError, ValueError):
                        parsed.append(None)
            columns.append(pa.array(parsed, type=target_type))
        elif target_type == pa.string():
            # Coerce to string (handles uuid extension, json extension)
            parsed = [str(v) if v is not None else None for v in values]
            columns.append(pa.array(parsed, type=target_type))
        elif target_type == pa.int64():
            parsed = []
            for v in values:
                if v is None:
                    parsed.append(None)
                else:
                    try:
                        parsed.append(int(v))
                    except (TypeError, ValueError):
                        parsed.append(None)
            columns.append(pa.array(parsed, type=target_type))
        else:
            # Fallback: try direct cast
            try:
                columns.append(col.cast(target_type))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                columns.append(col)  # keep as-is

        names.append(field.name)

    return pa.table(columns, names=names)


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

    def save_test_run(
        self,
        test_run: TestRun,
        journal_dir: Path | None = None,
        instrument_arrays: dict[str, list] | None = None,
    ) -> Path:
        """Save test run to Parquet with analysis-ready schema.

        If journal_dir is provided, converts the journal to parquet and moves
        ref files instead of building rows from TestRun. This is the preferred
        path when journaling is enabled, as it:
        1. Uses the already-captured measurements
        2. Handles crash recovery scenarios
        3. Moves ref files atomically

        Creates files:
            results/runs/{date}/{timestamp}_{serial}.parquet  (with serial)
            results/runs/{date}/{timestamp}.parquet           (without serial)

        All timestamps are UTC for consistent cross-timezone analysis.

        Args:
            test_run: Complete TestRun with steps, vectors, and measurements.
            journal_dir: Optional path to journal directory to convert.

        Returns:
            Path to the Parquet file.
        """
        # If journal exists, convert it instead of using in-memory data
        if journal_dir is not None and journal_dir.exists():
            return self.convert_journal(journal_dir, test_run)

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
        rows = self._build_measurement_rows(test_run, parquet_path, instrument_arrays)

        if not rows:
            # No measurements - create empty file with minimal schema
            rows = [self._build_empty_row(test_run, instrument_arrays)]

        # Convert to PyArrow table with canonical types
        table = pa.Table.from_pylist(rows)
        table = _enforce_schema(table)

        # Add file-level metadata (config snapshots)
        metadata = self._build_file_metadata(test_run)
        table = table.replace_schema_metadata(metadata)

        # Write to Parquet
        pq.write_table(table, parquet_path)

        return parquet_path

    def convert_journal(self, journal_dir: Path, test_run: TestRun | None = None) -> Path:
        """Convert a JSONL journal to Parquet format.

        Reads the journal file, converts to parquet, moves ref files,
        and deletes the journal directory on success.

        If the journal is empty but test_run is provided, falls back to
        building parquet from the TestRun (standard behavior without journaling).

        Args:
            journal_dir: Path to journal directory containing measurements.jsonl
            test_run: Optional TestRun for metadata (used for filename if provided)

        Returns:
            Path to the created Parquet file.

        Raises:
            FileNotFoundError: If journal file doesn't exist
            ValueError: If journal is empty and no test_run provided
        """
        journal_path = journal_dir / "measurements.jsonl"
        journal_ref_dir = journal_dir / "_ref"

        if not journal_path.exists():
            raise FileNotFoundError(f"Journal not found: {journal_path}")

        # Check if journal has content
        journal_content = journal_path.read_text().strip()
        if not journal_content:
            # Journal is empty - fall back to TestRun if available
            if test_run is not None:
                # Clean up empty journal directory
                shutil.rmtree(journal_dir)
                # Use standard save path (without journal)
                return self.save_test_run(test_run, journal_dir=None)
            raise ValueError(f"Journal is empty: {journal_path}")

        # Parse JSONL lines
        rows = []
        for line in journal_content.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))

        if not rows:
            if test_run is not None:
                shutil.rmtree(journal_dir)
                return self.save_test_run(test_run, journal_dir=None)
            raise ValueError(f"Journal is empty or invalid: {journal_path}")

        first_row = rows[0]

        # Determine output path from metadata
        run_started_at = first_row.get("run_started_at", "")
        dut_serial = first_row.get("dut_serial", "")

        # Parse timestamp - handle ISO format
        if isinstance(run_started_at, str):
            try:
                started_dt = datetime.fromisoformat(run_started_at.replace("Z", "+00:00"))
            except ValueError:
                started_dt = datetime.now()
        else:
            started_dt = run_started_at

        timestamp = started_dt.strftime("%Y%m%dT%H%M%SZ")
        date_str = started_dt.strftime("%Y-%m-%d")

        # Create output directory
        date_dir = self.results_dir / "runs" / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # Filename: timestamp first, serial if present
        if dut_serial:
            filename = f"{timestamp}_{dut_serial}.parquet"
        else:
            filename = f"{timestamp}.parquet"

        parquet_path = date_dir / filename

        # Backfill run_ended_at and step timestamps from TestRun
        # (journal rows are written mid-run, so these are null at write time)
        if test_run is not None:
            # Build step timing lookup: step_name → (started_at, ended_at)
            step_timing: dict[str, tuple] = {}
            for step in test_run.steps:
                step_timing[step.name] = (step.started_at, step.ended_at)

            for row in rows:
                if row.get("run_ended_at") is None:
                    row["run_ended_at"] = test_run.ended_at
                step_name = row.get("step_name")
                if step_name and step_name in step_timing:
                    s_start, s_end = step_timing[step_name]
                    if row.get("step_started_at") is None:
                        row["step_started_at"] = s_start
                    if row.get("step_ended_at") is None:
                        row["step_ended_at"] = s_end

        # Parse timestamp strings back to datetime objects before table construction
        for row in rows:
            for col in _TIMESTAMP_COLS:
                val = row.get(col)
                if isinstance(val, str):
                    row[col] = datetime.fromisoformat(val.replace("Z", "+00:00"))

        # Convert JSONL rows to Parquet with canonical types
        table = pa.Table.from_pylist(rows)
        table = _enforce_schema(table)

        # Add file-level metadata now if test_run provided (avoid re-read later)
        if test_run is not None:
            metadata = self._build_file_metadata(test_run)
            table = table.replace_schema_metadata(metadata)

        pq.write_table(table, parquet_path)

        # Move ref files if they exist
        if journal_ref_dir.exists() and any(journal_ref_dir.iterdir()):
            parquet_ref_dir = date_dir / (parquet_path.stem + "_ref")
            if parquet_ref_dir.exists():
                shutil.rmtree(parquet_ref_dir)
            shutil.move(str(journal_ref_dir), str(parquet_ref_dir))

        # Delete journal directory on successful conversion
        shutil.rmtree(journal_dir)

        return parquet_path

    def _build_measurement_rows(
        self,
        test_run: TestRun,
        parquet_path: Path,
        instrument_arrays: dict[str, list] | None = None,
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
                    row = build_run_metadata(test_run)
                    row.update({
                        "step_name": step.name,
                        "step_index": step_idx,
                        "step_started_at": step.started_at,
                        "step_ended_at": step.ended_at,
                        "vector_index": vector.index,
                        "attempt": vector.attempt,
                        "vector_started_at": vector.started_at,
                        "vector_ended_at": vector.ended_at,
                    })
                    row.update(build_measurement_fields(measurement))
                    row.update({
                        "vector_outcome": vector_outcome,
                        "run_outcome": run_outcome,
                    })

                    # Add stimulus columns (dynamic in_* columns)
                    row.update(stimulus_cols)

                    # Add observation columns (dynamic out_* columns)
                    row.update(observation_cols)

                    # Add custom metadata columns
                    for key, value in test_run.custom_metadata.items():
                        row[key] = value

                    # Add instrument identity arrays (per-step tracking)
                    step_arrays = (
                        step.instrument_arrays
                        if step.instrument_arrays
                        else instrument_arrays
                    )
                    if step_arrays:
                        row.update(step_arrays)

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

    def _save_file(self, parquet_path: Path, vector_id: str, key: str, value: Any) -> str:
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

    def _build_empty_row(
        self,
        test_run: TestRun,
        instrument_arrays: dict[str, list] | None = None,
    ) -> dict[str, Any]:
        """Build a placeholder row when no measurements exist."""
        row = build_run_metadata(test_run)
        row.update({
            "step_name": None,
            "step_index": None,
            "step_started_at": None,
            "step_ended_at": None,
            "vector_index": None,
            "attempt": None,
            "vector_started_at": None,
            "vector_ended_at": None,
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
        })

        # Add instrument identity arrays (parallel arrays)
        if instrument_arrays:
            row.update(instrument_arrays)
        else:
            # Add empty arrays if no instruments
            row.update({
                "instr_name": [],
                "instr_id": [],
                "instr_driver": [],
                "instr_resource": [],
                "instr_protocol": [],
                "instr_manufacturer": [],
                "instr_model": [],
                "instr_serial": [],
                "instr_firmware": [],
                "instr_cal_due": [],
                "instr_cal_last": [],
                "instr_cal_certificate": [],
                "instr_cal_lab": [],
                "instr_mocked": [],
            })

        return row

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

        if test_run.environment_json:
            metadata[b"environment_json"] = test_run.environment_json.encode("utf-8")

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

        # Collect all parquet files across date directories
        # Files are named: {timestamp}_{serial}.parquet or {timestamp}.parquet
        # Skip _ref directories
        parquet_files = sorted(
            (p for p in runs_dir.rglob("*.parquet") if "_ref" not in p.parent.name),
            reverse=True,
        )

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

    def find_run_file(self, run_id: str) -> Path | None:
        """Find parquet file for a run_id (cached)."""
        runs_dir = self.results_dir / "runs"
        if not runs_dir.exists():
            return None

        run_prefix = run_id[:8] if len(run_id) >= 8 else run_id

        # Sort by date folder descending, then by filename descending (most recent first)
        date_dirs = sorted(runs_dir.iterdir(), reverse=True) if runs_dir.exists() else []
        for date_dir in date_dirs:
            if not date_dir.is_dir():
                continue
            for pq_file in sorted(date_dir.glob("*.parquet"), reverse=True):
                if "_ref" in pq_file.parent.name:
                    continue
                try:
                    # Read just first row to check run_id
                    pf = pq.ParquetFile(pq_file)
                    table = pf.read_row_group(0, columns=["run_id"])
                    if table.num_rows == 0:
                        continue
                    file_run_id = table.to_pylist()[0].get("run_id", "")
                    if file_run_id.startswith(run_prefix) or run_prefix in file_run_id:
                        return pq_file
                except Exception:
                    continue
        return None

    def get_run(self, run_id: str) -> dict | None:
        """Get a specific test run by ID.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).

        Returns:
            Test run summary record or None if not found.
        """
        pq_file = self.find_run_file(run_id)
        if pq_file is None:
            return None

        try:
            table = pq.read_table(pq_file)
            if table.num_rows == 0:
                return None
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
                "_file": str(pq_file),  # Cache file path for get_measurements
            }
        except Exception:
            return None

    def get_measurements(self, run_id: str, *, _file: str | None = None) -> list[dict]:
        """Get all measurements for a specific test run.

        Args:
            run_id: The test run ID (can be partial, at least 8 chars).
            _file: Cached file path from get_run (optimization).

        Returns:
            List of measurement records for the run.
        """
        if _file:
            pq_file = Path(_file)
        else:
            pq_file = self.find_run_file(run_id)

        if pq_file is None or not pq_file.exists():
            return []

        try:
            table = pq.read_table(pq_file)
            return table.to_pylist()
        except Exception:
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

        run_prefix = run_id[:8] if len(run_id) >= 8 else run_id
        for pq_file in runs_dir.rglob("*.parquet"):
            if "_ref" in pq_file.parent.name:
                continue
            try:
                pf = pq.ParquetFile(pq_file)
                # Quick check: read first row to get run_id
                table = pf.read_row_group(0)
                if table.num_rows == 0:
                    continue
                row = table.to_pylist()[0]
                file_run_id = row.get("run_id", "")
                if file_run_id.startswith(run_prefix) or run_prefix in file_run_id:
                    raw_metadata = pf.schema_arrow.metadata or {}
                    return {
                        k.decode("utf-8"): v.decode("utf-8") for k, v in raw_metadata.items()
                    }
            except Exception:
                continue

        return None

    # =========================================================================
    # Journal Management
    # =========================================================================

    def list_journals(self) -> list[dict]:
        """List all journal directories with metadata.

        Returns:
            List of journal info dicts with run_id, dut_serial, measurement_count, etc.
        """
        from litmus.data.backends.journal import get_journal_info

        journals = []
        journals_dir = self.results_dir / ".journals"

        if not journals_dir.exists():
            return journals

        for date_dir in journals_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for journal_dir in date_dir.iterdir():
                if not journal_dir.is_dir():
                    continue
                info = get_journal_info(journal_dir)
                if info:
                    journals.append(info)

        # Sort by started_at descending
        journals.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return journals

    def get_journal_measurements(self, journal_dir: Path | str) -> list[dict]:
        """Read measurements from a journal file.

        Args:
            journal_dir: Path to journal directory or its path as string

        Returns:
            List of measurement row dicts
        """
        from litmus.data.backends.journal import read_journal

        journal_dir = Path(journal_dir)
        journal_path = journal_dir / "measurements.jsonl"
        return read_journal(journal_path)

    def recover_journal(self, journal_dir: Path | str) -> Path:
        """Convert an orphaned journal to parquet.

        Use this to recover data from crashed or interrupted test runs.

        Args:
            journal_dir: Path to journal directory

        Returns:
            Path to the created parquet file
        """
        journal_dir = Path(journal_dir)
        return self.convert_journal(journal_dir)

    def cleanup_journals(self) -> int:
        """Delete journals that have corresponding parquet files.

        Returns:
            Number of journals deleted
        """
        deleted = 0
        journals_dir = self.results_dir / ".journals"

        if not journals_dir.exists():
            return deleted

        for date_dir in journals_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for journal_dir in date_dir.iterdir():
                if not journal_dir.is_dir():
                    continue

                # Check if corresponding parquet exists
                parquet_path = (
                    self.results_dir / "runs" / date_dir.name / f"{journal_dir.name}.parquet"
                )
                if parquet_path.exists():
                    shutil.rmtree(journal_dir)
                    deleted += 1

            # Clean up empty date directories
            if date_dir.exists() and not any(date_dir.iterdir()):
                date_dir.rmdir()

        # Clean up empty .journals directory
        if journals_dir.exists() and not any(journals_dir.iterdir()):
            journals_dir.rmdir()

        return deleted

    def get_orphaned_journals(self) -> list[dict]:
        """List journals that don't have corresponding parquet files.

        These are likely from crashed or interrupted test runs.

        Returns:
            List of journal info dicts
        """
        from litmus.data.backends.journal import get_journal_info

        orphaned = []
        journals_dir = self.results_dir / ".journals"

        if not journals_dir.exists():
            return orphaned

        for date_dir in journals_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for journal_dir in date_dir.iterdir():
                if not journal_dir.is_dir():
                    continue

                # Check if corresponding parquet exists
                parquet_path = (
                    self.results_dir / "runs" / date_dir.name / f"{journal_dir.name}.parquet"
                )
                if not parquet_path.exists():
                    info = get_journal_info(journal_dir)
                    if info:
                        orphaned.append(info)

        return orphaned


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
