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
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from litmus.data._atomic import atomic_write_table
from litmus.data.backends._row_helpers import (
    REF_PATH_PREFIX,
    MeasurementRow,
    _env_columns,
    build_row,
    build_run_metadata,
    build_step_manifest,
    save_ref_to_dir,
)
from litmus.data.models import TestRun, Waveform
from litmus.data.ref import is_ref, ref_scheme
from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS

# Canonical schema for fixed columns. Dynamic columns (in_*, out_*, instr_*, custom_*)
# are NOT listed here — they pass through with inferred types.
MEASUREMENT_SCHEMA = pa.schema([
    # Identity & timing
    ("session_id", pa.string()),
    ("run_id", pa.string()),
    ("run_started_at", pa.timestamp("us", tz="UTC")),
    ("run_ended_at", pa.timestamp("us", tz="UTC")),
    ("step_name", pa.string()),
    ("step_index", pa.int64()),
    ("step_path", pa.string()),
    ("step_started_at", pa.timestamp("us", tz="UTC")),
    ("step_ended_at", pa.timestamp("us", tz="UTC")),
    ("step_node_id", pa.string()),
    ("step_module", pa.string()),
    ("step_file", pa.string()),
    ("step_class", pa.string()),
    ("step_function", pa.string()),
    ("step_markers", pa.string()),
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
    ("station_hostname", pa.string()),
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

        # Try Arrow-native cast first (handles most numeric/string conversions)
        try:
            if pa.types.is_timestamp(target_type) and pa.types.is_string(field.type):
                # String → timestamp: use strptime then cast to target tz
                parsed = pc.strptime(col, format="%Y-%m-%dT%H:%M:%S%z", unit="us")  # type: ignore[attr-defined]
                columns.append(parsed.cast(target_type))
            else:
                columns.append(col.cast(target_type, safe=False))
            names.append(field.name)
            continue
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            pass

        # Fallback for extension types: pylist round-trip
        values = col.to_pylist()

        if pa.types.is_timestamp(target_type):
            parsed_ts = []
            for v in values:
                if isinstance(v, str):
                    parsed_ts.append(datetime.fromisoformat(v.replace("Z", "+00:00")))
                else:
                    parsed_ts.append(v)
            columns.append(pa.array(parsed_ts, type=target_type))
        elif target_type == pa.string():
            columns.append(pa.array(
                [str(v) if v is not None else None for v in values],
                type=target_type,
            ))
        else:
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

    def __init__(self, results_dir: Path | str | None = None):
        from litmus.data.results_dir import resolve_results_dir

        self.results_dir = resolve_results_dir(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_test_run(
        self,
        test_run: TestRun,
        instrument_arrays: dict[str, list] | None = None,
    ) -> Path:
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

        # Write to Parquet (atomic: temp file + os.replace)
        atomic_write_table(table, parquet_path)

        # Notify runs daemon so it indexes immediately
        self._notify_daemon(parquet_path)

        return parquet_path

    def _notify_daemon(self, parquet_path: Path) -> None:
        """Best-effort notification to the runs DuckDB daemon."""
        try:
            from litmus.data.run_store import RunStore

            run_store = RunStore(_results_dir=self.results_dir)
            try:
                run_store.notify_new_run(parquet_path)
            finally:
                run_store.close()
        except Exception:
            pass  # Non-fatal: daemon will rebuild from files on next start

    def _build_measurement_rows(
        self,
        test_run: TestRun,
        parquet_path: Path,
        instrument_arrays: dict[str, list] | None = None,
    ) -> list[dict[str, Any]]:
        """Build one row per measurement with all metadata denormalized."""
        def ref_saver(vector_id: str, key: str, value: Any) -> str:
            return self._save_file(parquet_path, vector_id, key, value)

        meta = build_run_metadata(test_run)
        rows: list[dict[str, Any]] = []
        for step_idx, step in enumerate(test_run.steps):
            step_arrays = (
                step.instrument_arrays
                if step.instrument_arrays
                else instrument_arrays or {k: [] for k in INSTRUMENT_ARRAY_KEYS}
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
                        step_path=step.step_path,
                        step_started_at=step.started_at,
                        step_ended_at=step.ended_at,
                        step_node_id=step.node_id,
                        step_module=step.module,
                        step_file=step.file,
                        step_class=step.class_name,
                        step_function=step.function,
                        step_markers=step.markers,
                        meta=meta,
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

        # Step manifest
        manifest = build_step_manifest(test_run)
        metadata[b"litmus_step_manifest"] = json.dumps(manifest).encode("utf-8")

        # Add some convenience metadata
        metadata[b"litmus_version"] = b"1.0.0"
        metadata[b"schema_version"] = b"2.0"  # New analysis-ready schema

        return metadata

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent test runs. Delegates to RunStore."""
        from litmus.data.run_store import RunStore

        run_store = RunStore(_results_dir=self.results_dir)
        try:
            return run_store.list_runs(limit=limit)
        finally:
            run_store.close()

    def find_run_file(self, run_id: str) -> Path | None:
        """Find parquet file for a run_id. Delegates to RunStore."""
        from litmus.data.run_store import RunStore

        run_store = RunStore(_results_dir=self.results_dir)
        try:
            return run_store.find_run_file(run_id)
        finally:
            run_store.close()

    def get_run(self, run_id: str) -> dict | None:
        """Get a specific test run by ID. Delegates to RunStore."""
        from litmus.data.run_store import RunStore

        run_store = RunStore(_results_dir=self.results_dir)
        try:
            return run_store.get_run(run_id)
        finally:
            run_store.close()

    def get_measurements(self, run_id: str, *, _file: str | None = None) -> list[dict]:
        """Get all measurements for a specific test run. Delegates to RunStore."""
        from litmus.data.run_store import RunStore

        run_store = RunStore(_results_dir=self.results_dir)
        try:
            return run_store.get_measurements(run_id, _file=_file)
        finally:
            run_store.close()

    def get_vectors(self, run_id: str) -> list[dict]:
        """Get unique test vectors for a specific test run."""
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
                        param_name = k[3:]
                        params[param_name] = v
                vector_info["params"] = params
                vectors_seen[key] = vector_info

        return list(vectors_seen.values())

    def get_run_metadata(self, run_id: str) -> dict[str, str] | None:
        """Get file-level metadata (config snapshots) for a run."""
        from litmus.data.run_store import RunStore

        run_store = RunStore(_results_dir=self.results_dir)
        try:
            pq_file = run_store.find_run_file(run_id)
        finally:
            run_store.close()

        if pq_file is None:
            return None

        try:
            pf = pq.ParquetFile(pq_file)
            raw_metadata = pf.schema_arrow.metadata or {}
            return {
                k.decode("utf-8"): v.decode("utf-8") for k, v in raw_metadata.items()
            }
        except Exception:
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

    def save_from_rows(
        self,
        rows: list[dict[str, Any]],
        started_at: datetime,
        dut_serial: str,
        file_metadata: dict[bytes, bytes] | None = None,
    ) -> Path:
        """Save pre-built flat row dicts to Parquet.

        Used by ParquetSubscriber to write accumulated rows without
        needing a TestRun object.
        """
        timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        date_str = started_at.strftime("%Y-%m-%d")
        dut_serial = dut_serial.strip() if dut_serial else ""

        date_dir = self.results_dir / "runs" / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        if dut_serial:
            filename = f"{timestamp}_{dut_serial}.parquet"
        else:
            filename = f"{timestamp}.parquet"

        parquet_path = date_dir / filename

        # Ensure instrument array columns exist for schema consistency
        for row in rows:
            for key in INSTRUMENT_ARRAY_KEYS:
                if key not in row:
                    row[key] = []

        table = pa.Table.from_pylist(rows)
        table = _enforce_schema(table)

        if file_metadata:
            table = table.replace_schema_metadata(file_metadata)

        atomic_write_table(table, parquet_path)

        # Notify runs daemon so it indexes immediately
        self._notify_daemon(parquet_path)

        return parquet_path

class ParquetSubscriber:
    """EventSubscriber that accumulates measurements and writes Parquet on close.

    Caches ``SessionStarted`` for run metadata and ``InstrumentConnected``
    for instrument arrays. On ``MeasurementRecorded``, builds a denormalized
    row using cached context. On ``RunEnded`` or ``close()``, writes Parquet.
    """

    format_name = "parquet"

    def __init__(self, backend: ParquetBackend) -> None:
        from litmus.data.events import (
            InstrumentConnected,
            MeasurementRecorded,
            RunEnded,
            SessionStarted,
            StepEnded,
            StepStarted,
        )

        self.event_types: set[type] = {
            SessionStarted, InstrumentConnected,
            StepStarted, MeasurementRecorded, StepEnded, RunEnded,
        }
        self._backend = backend
        self._session: Any = None  # SessionStarted event
        self._instruments: list[Any] = []  # InstrumentConnected events
        self._measurement_events: list[Any] = []  # MeasurementRecorded events
        self._written = False
        self._steps_with_measurements: set[int] = set()
        self._step_starts: dict[int, Any] = {}  # step_index → StepStarted
        self._step_ends: dict[int, Any] = {}  # step_index → StepEnded

    def open(self) -> None:
        pass

    def on_event(self, event: Any) -> None:
        from litmus.data.events import (
            InstrumentConnected,
            MeasurementRecorded,
            RunEnded,
            SessionStarted,
            StepEnded,
            StepStarted,
        )

        if isinstance(event, SessionStarted):
            self._session = event
        elif isinstance(event, InstrumentConnected):
            self._instruments.append(event)
        elif isinstance(event, StepStarted):
            self._step_starts[event.step_index] = event
        elif isinstance(event, MeasurementRecorded):
            self._steps_with_measurements.add(event.step_index)
            self._measurement_events.append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[event.step_index] = event
        elif isinstance(event, RunEnded):
            self._write(outcome=event.outcome)

    def close(self) -> None:
        if not self._written:
            self._write()

    def _build_instrument_arrays(self) -> dict[str, list]:
        """Build instrument arrays from cached InstrumentConnected events."""
        arrays: dict[str, list] = {k: [] for k in INSTRUMENT_ARRAY_KEYS}
        for inst in self._instruments:
            arrays["instr_name"].append(inst.role)
            arrays["instr_id"].append(inst.instrument_id)
            arrays["instr_driver"].append(inst.driver)
            arrays["instr_resource"].append(inst.resource)
            arrays["instr_protocol"].append(inst.protocol)
            arrays["instr_manufacturer"].append(inst.manufacturer)
            arrays["instr_model"].append(inst.model)
            arrays["instr_serial"].append(inst.serial)
            arrays["instr_firmware"].append(inst.firmware)
            arrays["instr_cal_due"].append(inst.cal_due)
            arrays["instr_cal_last"].append(inst.cal_last)
            arrays["instr_cal_certificate"].append(inst.cal_certificate)
            arrays["instr_cal_lab"].append(inst.cal_lab)
            arrays["instr_mocked"].append(inst.mocked)
        return arrays

    def _session_metadata_kwargs(self, event: Any) -> dict[str, Any]:
        """Extract session-level metadata as kwargs for MeasurementRow.

        Shared by ``_build_row()`` and ``_build_step_summary_row()``.
        """
        s = self._session
        env = _env_columns(s.environment_json if s else None)
        return {
            "session_id": str(s.session_id) if s else str(event.session_id),
            "run_id": str(event.run_id) if event.run_id else "",
            "run_started_at": s.occurred_at if s else None,
            "run_ended_at": None,
            "operator_id": s.operator_id if s else None,
            "operator_name": s.operator_name if s else None,
            "dut_serial": s.dut_serial if s else "unknown",
            "dut_part_number": s.dut_part_number if s else None,
            "dut_revision": s.dut_revision if s else None,
            "dut_lot_number": s.dut_lot_number if s else None,
            "product_id": s.product_id if s else None,
            "product_name": s.product_name if s else None,
            "product_revision": s.product_revision if s else None,
            "station_id": s.station_id if s else "unknown",
            "station_name": s.station_name if s else None,
            "station_type": s.station_type if s else None,
            "station_location": s.station_location if s else None,
            "station_hostname": s.station_hostname if s else None,
            "fixture_id": s.fixture_id if s else None,
            "sequence_id": s.sequence_id if s else None,
            "test_phase": s.test_phase if s else None,
            "git_commit": s.git_commit if s else None,
            "python_version": env.get("python_version"),
            "litmus_version": env.get("litmus_version"),
            "env_fingerprint": env.get("env_fingerprint"),
        }

    def _build_row(self, event: Any) -> dict[str, Any]:
        """Denormalize a MeasurementRecorded event into a flat row dict.

        Joins cached SessionStarted metadata + InstrumentConnected arrays
        with the normalized measurement event to produce a full
        ``MeasurementRow``-compatible flat dict.
        """
        row = MeasurementRow(
            **self._session_metadata_kwargs(event),
            # Step/vector (from event + cached StepStarted)
            step_name=event.step_name,
            step_index=event.step_index,
            step_path=event.step_path,
            step_started_at=(
                self._step_starts[event.step_index].occurred_at
                if event.step_index in self._step_starts
                else None
            ),
            step_ended_at=(
                self._step_ends[event.step_index].occurred_at
                if event.step_index in self._step_ends
                else None
            ),
            step_node_id=(
                self._step_starts[event.step_index].node_id
                if event.step_index in self._step_starts
                else None
            ),
            step_module=(
                self._step_starts[event.step_index].module
                if event.step_index in self._step_starts
                else None
            ),
            step_file=(
                self._step_starts[event.step_index].file
                if event.step_index in self._step_starts
                else None
            ),
            step_class=(
                self._step_starts[event.step_index].class_name
                if event.step_index in self._step_starts
                else None
            ),
            step_function=(
                self._step_starts[event.step_index].function
                if event.step_index in self._step_starts
                else None
            ),
            vector_index=event.vector_index,
            attempt=event.attempt,
            # Measurement (from event)
            measurement_name=event.measurement_name,
            measurement_timestamp=event.measurement_timestamp,
            value=event.value,
            units=event.units,
            outcome=event.outcome,
            low_limit=event.low_limit,
            high_limit=event.high_limit,
            nominal=event.nominal,
            comparator=event.comparator,
            spec_id=event.spec_id,
            spec_ref=event.spec_ref,
            meas_dut_pin=event.meas_dut_pin,
            meas_fixture_point=event.meas_fixture_point,
            meas_instrument=event.meas_instrument,
            meas_instrument_resource=event.meas_instrument_resource,
            meas_instrument_channel=event.meas_instrument_channel,
            # Run outcome backfilled in _write()
            run_outcome=None,
            # Dynamic columns
            inputs=dict(event.inputs),
            outputs=dict(event.outputs),
            instruments=self._build_instrument_arrays(),
            custom=dict(event.custom),
        )
        return row.to_flat_dict()

    def _build_step_summary_row(self, step_ended: Any) -> dict[str, Any]:
        """Build a summary row for a step that completed with no measurements."""
        row = MeasurementRow(
            **self._session_metadata_kwargs(step_ended),
            step_name=step_ended.step_name,
            step_index=step_ended.step_index,
            step_path=step_ended.step_path,
            step_started_at=(
                self._step_starts[step_ended.step_index].occurred_at
                if step_ended.step_index in self._step_starts
                else None
            ),
            step_ended_at=step_ended.occurred_at,
            step_node_id=step_ended.node_id,
            step_module=step_ended.module,
            step_file=step_ended.file,
            step_class=step_ended.class_name,
            step_function=step_ended.function,
            measurement_name="_step_summary",
            value=None,
            outcome=step_ended.outcome,
            run_outcome=None,
            instruments=self._build_instrument_arrays(),
        )
        return row.to_flat_dict()

    def _write(self, outcome: str | None = None) -> None:
        """Write accumulated rows to Parquet.

        Args:
            outcome: Run outcome from RunEnded. If None (crash/close without
                RunEnded), defaults to "error" since the run didn't complete.
        """
        if self._written:
            return
        self._written = True

        s = self._session
        if not s:
            return

        # Build all rows now — all events are in, step times are known
        from litmus.data.models import _utcnow
        ended_at = _utcnow()
        final_outcome = outcome if outcome is not None else "error"

        rows: list[dict[str, Any]] = []
        for event in self._measurement_events:
            row = self._build_row(event)
            row["run_ended_at"] = ended_at
            row["run_outcome"] = final_outcome
            rows.append(row)

        # Add summary rows for steps with no measurements
        for step_idx, step_end in self._step_ends.items():
            if step_idx not in self._steps_with_measurements:
                row = self._build_step_summary_row(step_end)
                row["run_ended_at"] = ended_at
                row["run_outcome"] = final_outcome
                rows.append(row)

        if not rows:
            return

        # Write via save_from_rows
        self._backend.save_from_rows(
            rows,
            started_at=s.occurred_at,
            dut_serial=s.dut_serial,
            file_metadata=self._build_file_metadata(),
        )

    def _build_file_metadata(self) -> dict[bytes, bytes]:
        """Build Parquet file-level metadata from cached session."""
        metadata: dict[bytes, bytes] = {}
        s = self._session
        if not s:
            return metadata

        if s.station_config_yaml:
            metadata[b"station_config_yaml"] = s.station_config_yaml.encode("utf-8")
        if s.product_spec_yaml:
            metadata[b"product_spec_yaml"] = s.product_spec_yaml.encode("utf-8")
        if s.fixture_config_yaml:
            metadata[b"fixture_config_yaml"] = s.fixture_config_yaml.encode("utf-8")
        if s.test_config_yaml:
            metadata[b"test_config_yaml"] = s.test_config_yaml.encode("utf-8")
        if s.environment_json:
            metadata[b"environment_json"] = s.environment_json.encode("utf-8")

        # Step manifest from cached events
        manifest = self._build_step_manifest_from_events()
        if manifest:
            metadata[b"litmus_step_manifest"] = json.dumps(manifest).encode("utf-8")

        metadata[b"litmus_version"] = b"1.0.0"
        metadata[b"schema_version"] = b"2.0"
        return metadata

    def _build_step_manifest_from_events(self) -> list[dict[str, Any]]:
        """Build step manifest from cached StepStarted/StepEnded events."""
        manifest: list[dict[str, Any]] = []
        all_indices = sorted(set(self._step_starts) | set(self._step_ends))
        for idx in all_indices:
            start = self._step_starts.get(idx)
            end = self._step_ends.get(idx)
            meas_count = sum(
                1 for e in self._measurement_events if e.step_index == idx
            )
            entry: dict[str, Any] = {
                "index": idx,
                "name": start.step_name if start else (end.step_name if end else ""),
                "node_id": start.node_id if start else None,
                "file": start.file if start else None,
                "function": start.function if start else None,
                "class": start.class_name if start else None,
                "module": start.module if start else None,
                "step_path": start.step_path if start else (end.step_path if end else ""),
                "description": start.description if start else None,
                "outcome": end.outcome if end else None,
                "started_at": start.occurred_at.isoformat() if start else None,
                "ended_at": end.occurred_at.isoformat() if end else None,
                "has_measurements": meas_count > 0,
                "measurement_count": meas_count,
                "vector_count": 0,
            }
            manifest.append(entry)
        return manifest


def load_file(parquet_path: Path, ref: str) -> Any:
    """Load a file reference (``file://`` URI or legacy ``_ref/`` path).

    Args:
        parquet_path: Path to the parquet file (used to locate _ref/ dir).
        ref: Reference string — ``"file://_ref/abc.npz"`` or legacy ``"_ref/abc.npz"``.

    Returns:
        Loaded data in appropriate format:
        - .npz → Waveform model (if has Y, t0, dt) or dict
        - .npy → numpy array
        - .json → dict or Pydantic model
        - .bin → bytes
        - .pkl → pickled object
        - Other → raw file path
    """
    # Normalize: strip file:// prefix if present
    raw = ref
    if raw.startswith("file://"):
        raw = raw[len("file://"):]

    if not raw.startswith(REF_PATH_PREFIX):
        return ref  # Not a file reference, return as-is

    # Get path relative to parquet file
    ref_dir = parquet_path.parent / (parquet_path.stem + "_ref")
    filename = raw[len(REF_PATH_PREFIX):]
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

    elif ext == ".arrow":
        import pyarrow.ipc as ipc_mod

        return ipc_mod.open_file(path).read_all()

    elif ext == ".pkl":
        with open(path, "rb") as f:
            return pickle.load(f)

    else:
        # Return path for other file types
        return path


def read_step_manifest(parquet_path: Path) -> list[dict[str, Any]]:
    """Read the step manifest from Parquet file-level metadata.

    Returns an empty list if no manifest is stored.
    """
    try:
        pf = pq.ParquetFile(parquet_path)
        raw_metadata = pf.schema_arrow.metadata or {}
        manifest_bytes = raw_metadata.get(b"litmus_step_manifest")
        if manifest_bytes:
            return json.loads(manifest_bytes)
    except Exception:
        pass
    return []


def load_ref(
    value: str,
    *,
    parquet_path: Path | None = None,
    channel_store: Any | None = None,
) -> Any:
    """Unified reference loader — dispatches on URI scheme.

    Args:
        value: URI string (``channel://...``, ``file://...``, or legacy ``_ref/...``).
        parquet_path: Path to parquet file (needed for ``file://`` refs).
        channel_store: ChannelStore instance (needed for ``channel://`` refs).

    Returns:
        Loaded data.
    """
    if not is_ref(value):
        # Legacy _ref/ path without file:// prefix
        if isinstance(value, str) and value.startswith(REF_PATH_PREFIX) and parquet_path:
            return load_file(parquet_path, value)
        return value

    scheme = ref_scheme(value)

    if scheme == "file":
        if parquet_path is None:
            return value
        return load_file(parquet_path, value)

    if scheme == "channel":
        if channel_store is None:
            return value
        from litmus.data.ref import parse_channel_uri
        channel_id, session_id = parse_channel_uri(value)
        return channel_store.query(channel_id, session_id=session_id or None)

    # Unknown scheme — return as-is
    return value


def is_file_reference(value: Any) -> bool:
    """Check if a value is a file reference (``file://`` URI or legacy ``_ref/`` path)."""
    if not isinstance(value, str):
        return False
    if value.startswith(REF_PATH_PREFIX):
        return True
    if is_ref(value) and ref_scheme(value) == "file":
        return True
    return False


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
        station_hostname=first.get("station_hostname"),
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
