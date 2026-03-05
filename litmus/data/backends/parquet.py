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

from litmus.data.backends._row_helpers import (
    REF_PATH_PREFIX,
    build_row,
    build_run_metadata,
    save_ref_to_dir,
)
from litmus.data.models import TestRun, Waveform

# Canonical schema for fixed columns. Dynamic columns (in_*, out_*, instr_*, custom_*)
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
            # Build step timing lookup: step_index → (started_at, ended_at)
            # Keyed by index, not name, because multiple steps can share a name.
            step_timing: dict[int, tuple] = {}
            for idx, step in enumerate(test_run.steps):
                step_timing[idx] = (step.started_at, step.ended_at)

            # Vector timing: (step_index, vector_index, attempt) → (started, ended)
            vector_timing: dict[tuple[int, int | None, int | None], tuple] = {}
            for idx, step in enumerate(test_run.steps):
                for vec in step.vectors:
                    vector_timing[(idx, vec.index, vec.attempt)] = (
                        vec.started_at,
                        vec.ended_at,
                    )

            for row in rows:
                if row.get("run_ended_at") is None:
                    row["run_ended_at"] = test_run.ended_at
                step_idx = row.get("step_index")
                if step_idx is not None and step_idx in step_timing:
                    s_start, s_end = step_timing[step_idx]
                    if row.get("step_started_at") is None:
                        row["step_started_at"] = s_start
                    if row.get("step_ended_at") is None:
                        row["step_ended_at"] = s_end
                # Backfill vector timing
                vk = (step_idx, row.get("vector_index"), row.get("attempt"))
                if vk in vector_timing:
                    v_start, v_end = vector_timing[vk]
                    if row.get("vector_started_at") is None:
                        row["vector_started_at"] = v_start
                    if row.get("vector_ended_at") is None:
                        row["vector_ended_at"] = v_end

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
        def ref_saver(vector_id: str, key: str, value: Any) -> str:
            return self._save_file(parquet_path, vector_id, key, value)

        rows: list[dict[str, Any]] = []
        for step_idx, step in enumerate(test_run.steps):
            step_arrays = (
                step.instrument_arrays
                if step.instrument_arrays
                else instrument_arrays or {}
            )
            for vector in step.vectors:
                for measurement in vector.measurements:
                    row_model = build_row(
                        test_run,
                        measurement,
                        step.name,
                        step_idx,
                        vector,
                        step_arrays,
                        ref_saver=ref_saver,
                        step_started_at=step.started_at,
                        step_ended_at=step.ended_at,
                    )
                    rows.append(row_model.to_flat_dict())
        return rows

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
        return save_ref_to_dir(ref_dir, vector_id[:8], key, value)

    def _build_empty_row(
        self,
        test_run: TestRun,
        instrument_arrays: dict[str, list] | None = None,
    ) -> dict[str, Any]:
        """Build a placeholder row when no measurements exist.

        Uses MeasurementRow.model_fields to stay in sync with the schema —
        all fields default to None except run-level metadata and run_outcome.
        """
        from litmus.data.backends._row_helpers import MeasurementRow

        # Start with all MeasurementRow fields set to None
        row: dict[str, Any] = {
            name: None
            for name in MeasurementRow.model_fields
            if name not in ("inputs", "outputs", "instruments", "custom")
        }
        # Overlay run-level metadata (populates run_id, dut_serial, etc.)
        row.update(build_run_metadata(test_run))
        row["run_outcome"] = test_run.outcome.value

        # Add instrument identity arrays (default to empty lists for schema consistency)
        if instrument_arrays:
            row.update(instrument_arrays)
        else:
            from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS

            for key in INSTRUMENT_ARRAY_KEYS:
                row[key] = []

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
    # TestRun Reconstruction (for post-hoc export)
    # =========================================================================

    def reconstruct_test_run(self, run_id: str) -> TestRun:
        """Reconstruct a TestRun model from a stored Parquet file.

        Groups denormalized rows back into the TestRun → TestStep → TestVector
        → Measurement hierarchy. Used by exporters for post-hoc conversion.

        Args:
            run_id: Full or partial run ID.

        Returns:
            Reconstructed TestRun model.

        Raises:
            FileNotFoundError: If no Parquet file found for the run ID.
        """
        pq_file = self.find_run_file(run_id)
        if pq_file is None:
            raise FileNotFoundError(
                f"No Parquet file found for run '{run_id}' in {self.results_dir}/"
            )
        return reconstruct_test_run_from_file(pq_file)

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


# Suffix patterns for stimulus signal-path columns (in_{param}_{suffix}).
# A column like "in_vin_instrument" is metadata, not a param value.
_STIMULUS_SUFFIXES = ("_instrument", "_resource", "_channel", "_dut_pin", "_fixture_point")


def reconstruct_test_run_from_file(pq_file: Path) -> TestRun:
    """Reconstruct a TestRun model from a Parquet file.

    Groups denormalized rows back into the TestRun → TestStep → TestVector
    → Measurement hierarchy. Used by exporters for post-hoc conversion
    and by the ``litmus convert`` CLI.

    Args:
        pq_file: Path to the Parquet file.

    Returns:
        Reconstructed TestRun model.

    Raises:
        FileNotFoundError: If the file doesn't exist or is empty.
    """
    from collections import defaultdict
    from uuid import UUID

    from litmus.data.models import DUT, Measurement, Outcome, TestStep, TestVector

    if not pq_file.exists():
        raise FileNotFoundError(f"Parquet file not found: {pq_file}")

    pf = pq.ParquetFile(pq_file)
    table = pf.read()
    rows = table.to_pylist()
    if not rows:
        raise FileNotFoundError(f"Parquet file is empty: {pq_file}")

    first = rows[0]

    # Read file-level metadata for config snapshots
    raw_meta = pf.schema_arrow.metadata or {}
    file_meta = {k.decode(): v.decode() for k, v in raw_meta.items()}

    # Group rows by (step_name, step_index) → (vector_index, attempt) → measurements
    step_groups: dict[
        tuple[str | None, int | None],
        dict[tuple[int | None, int | None], list[dict]],
    ] = defaultdict(lambda: defaultdict(list))
    step_timing: dict[tuple[str | None, int | None], dict[str, Any]] = {}

    for row in rows:
        sk = (row.get("step_name"), row.get("step_index"))
        vk = (row.get("vector_index"), row.get("attempt"))
        step_groups[sk][vk].append(row)

        if sk not in step_timing:
            step_timing[sk] = {
                "started_at": row.get("step_started_at"),
                "ended_at": row.get("step_ended_at"),
            }

    # Build steps
    steps: list[TestStep] = []
    for sk in sorted(step_groups, key=lambda x: (x[1] or 0, x[0] or "")):
        vector_groups = step_groups[sk]
        vectors: list[TestVector] = []

        # One sample row for step-level extraction (instr_* arrays)
        step_sample_row = next(iter(vector_groups.values()))[0]
        step_instr: dict[str, list] = {}
        for col, val in step_sample_row.items():
            if col.startswith("instr_"):
                if val is not None:
                    step_instr[col] = val if isinstance(val, list) else [val]

        for vk in sorted(vector_groups, key=lambda x: (x[0] or 0, x[1] or 0)):
            meas_rows = vector_groups[vk]
            measurements: list[Measurement] = []

            # Extract params from in_* columns and observations from out_*
            params: dict[str, Any] = {}
            observations: dict[str, Any] = {}
            sample_row = meas_rows[0]
            for col, val in sample_row.items():
                if col.startswith("in_") and not any(
                    col.endswith(s) for s in _STIMULUS_SUFFIXES
                ):
                    params[col[3:]] = val
                elif col.startswith("out_"):
                    observations[col[4:]] = val

            for mr in meas_rows:
                outcome_str = mr.get("outcome")
                m = Measurement(
                    name=mr.get("measurement_name") or "",
                    value=mr.get("value"),
                    units=mr.get("units"),
                    low_limit=mr.get("low_limit"),
                    high_limit=mr.get("high_limit"),
                    nominal=mr.get("nominal"),
                    comparator=mr.get("comparator"),
                    outcome=Outcome(outcome_str) if outcome_str else None,
                    spec_id=mr.get("spec_id"),
                    spec_ref=mr.get("spec_ref"),
                    dut_pin=mr.get("meas_dut_pin"),
                    instrument_name=mr.get("meas_instrument"),
                    instrument_resource=mr.get("meas_instrument_resource"),
                    instrument_channel=mr.get("meas_instrument_channel"),
                    fixture_point=mr.get("meas_fixture_point"),
                )
                ts = mr.get("measurement_timestamp")
                if ts is not None:
                    m.timestamp = ts
                measurements.append(m)

            vec_outcome_str = sample_row.get("vector_outcome")
            vectors.append(
                TestVector(
                    index=vk[0] or 0,
                    attempt=vk[1] or 1,
                    params=params,
                    observations=observations,
                    outcome=Outcome(vec_outcome_str) if vec_outcome_str else Outcome.PASS,
                    measurements=measurements,
                    started_at=sample_row.get("vector_started_at") or first["run_started_at"],
                    ended_at=sample_row.get("vector_ended_at"),
                )
            )

        timing = step_timing.get(sk, {})
        step_outcome = Outcome.PASS
        if any(v.outcome == Outcome.FAIL for v in vectors):
            step_outcome = Outcome.FAIL

        steps.append(
            TestStep(
                name=sk[0] or "",
                started_at=timing.get("started_at") or first["run_started_at"],
                ended_at=timing.get("ended_at"),
                outcome=step_outcome,
                vectors=vectors,
                instrument_arrays=step_instr if step_instr else None,
            )
        )

    # Extract custom metadata from custom_* columns
    custom_meta: dict[str, Any] = {}
    for col in first:
        if col.startswith("custom_"):
            custom_meta[col.removeprefix("custom_")] = first[col]

    run_outcome_str = first.get("run_outcome")
    run_outcome = Outcome(run_outcome_str) if run_outcome_str else Outcome.PASS

    return TestRun(
        id=UUID(first["run_id"]),
        started_at=first["run_started_at"],
        ended_at=first.get("run_ended_at"),
        dut=DUT(
            serial=first.get("dut_serial") or "",
            part_number=first.get("dut_part_number"),
            revision=first.get("dut_revision"),
            lot_number=first.get("dut_lot_number"),
        ),
        product_id=first.get("product_id"),
        product_name=first.get("product_name"),
        product_revision=first.get("product_revision"),
        station_id=first.get("station_id") or "",
        station_name=first.get("station_name"),
        station_type=first.get("station_type"),
        station_location=first.get("station_location"),
        fixture_id=first.get("fixture_id"),
        test_sequence_id=first.get("sequence_id") or "",
        test_phase=first.get("test_phase") or "development",
        operator_id=first.get("operator_id"),
        operator_name=first.get("operator_name"),
        git_commit=first.get("git_commit"),
        outcome=run_outcome,
        steps=steps,
        environment_json=file_meta.get("environment_json"),
        station_config_yaml=file_meta.get("station_config_yaml"),
        product_spec_yaml=file_meta.get("product_spec_yaml"),
        fixture_config_yaml=file_meta.get("fixture_config_yaml"),
        test_config_yaml=file_meta.get("test_config_yaml"),
        custom_metadata=custom_meta or {},
    )
