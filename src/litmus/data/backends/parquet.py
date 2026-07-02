"""Parquet storage backend for test results.

Implements an analysis-ready schema with one row per measurement and all
metadata denormalized for easy querying with DuckDB, Spark, Polars, etc.

Directory structure:
    results/runs/{date}/
    ├── {timestamp}_{run_id8}_{serial}.parquet   # With serial (production)
    ├── {timestamp}_{run_id8}.parquet            # Without serial (dev/debug)
    └── {timestamp}_{run_id8}_{serial}_ref/      # Reference data (waveforms, images)

The run_id (8-char prefix) sits in a fixed position right after the
timestamp so the optional serial can trail without shifting it; it
disambiguates two runs of the same serial that start in the same second
(otherwise the second would silently overwrite the first).

All timestamps are UTC for consistent cross-timezone analysis.

Schema design:
- One row per measurement
- All metadata denormalized onto each row
- Inputs lane: LIST<STRUCT> of stimulus conditions (role='input')
- Outputs lane: LIST<STRUCT> of observations (scalars inline, URIs for large data)
- Config snapshots in Parquet file-level metadata
"""

from __future__ import annotations

import io
import json
import logging
import pickle
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from litmus.data._atomic import atomic_write_table
from litmus.data.backends._event_accumulator import EventAccumulator
from litmus.data.backends._row_helpers import (
    HAS_NUMPY,
    REF_PATH_PREFIX,
    build_measurement_struct,
    build_output_columns,
    build_run_metadata,
    build_run_row,
    build_step_row,
    build_vector_row,
    decode_lane_structs,
    run_context_from_run_started,
    step_entry_dict,
    vector_entry_dict,
)
from litmus.data.data_dir import resolve_data_dir
from litmus.data.models import (
    UUT,
    Measurement,
    Outcome,
    RunSummary,
    TestRun,
    TestStep,
    TestVector,
    Waveform,
    _utcnow,
)
from litmus.data.ref import is_ref, parse_channel_uri, ref_scheme
from litmus.data.run_store import RunStore
from litmus.data.schemas import (
    SCHEMA_VERSION,
    _build_write_schema,
    table_from_rows,
)

logger = logging.getLogger(__name__)

# Suffix patterns that identify signal-path metadata keys in the
# dynamic_attrs MAP. A key ending in one of these suffixes is metadata,
# not a stimulus value.
_STIMULUS_SUFFIXES = ("_instrument", "_resource", "_channel", "_uut_pin", "_fixture_connection")

# Outcome priority for deterministic worst-case selection from a set.
# Lower rank = worse outcome. Ties (same rank) pick the same "worst" value.
OUTCOME_RANK: dict[str, int] = {"failed": 0, "errored": 1, "skipped": 2, "passed": 3}


def _run_parquet_filename(timestamp: str, run_id: str, serial: str) -> str:
    """Per-run parquet filename: ``{timestamp}_{run_id8}[_{serial}]``.

    The 8-char run_id sits in a fixed position right after the timestamp; the
    optional serial trails so its absence never shifts the leading parts. The
    run_id prefix disambiguates two runs of the same serial that start in the
    same second — without it the second would silently overwrite the first.
    """
    parts = [timestamp]
    if run_id:
        parts.append(run_id[:8])
    if serial:
        parts.append(serial)
    return "_".join(parts) + ".parquet"


def _is_stimulus_key(name: str) -> bool:
    """True if ``name`` is stimulus signal-path metadata, not a param value."""
    return any(name.endswith(s) for s in _STIMULUS_SUFFIXES)


def _params_from_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Split decoded inputs into vector params (drop stimulus signal-path keys)."""
    return {k: v for k, v in inputs.items() if not _is_stimulus_key(k)}


def _build_parquet_metadata(
    *,
    environment_json: str | None = None,
    custom_metadata: dict[str, Any] | None = None,
) -> dict[bytes, bytes]:
    """Build Parquet file-level metadata.

    Shared by ParquetBackend (from TestRun) and
    :func:`materialize_run_to_parquet` (from cached RunStarted event).
    """
    metadata: dict[bytes, bytes] = {}

    if environment_json:
        metadata[b"environment_json"] = environment_json.encode("utf-8")
    if custom_metadata:
        metadata[b"custom_metadata"] = json.dumps(custom_metadata, default=str).encode("utf-8")

    metadata[b"schema_version"] = SCHEMA_VERSION.encode()
    return metadata


class ParquetMeasurementWriter:
    """Writes measurement RecordBatches as Parquet files."""

    def write_batch(
        self,
        batch: pa.RecordBatch,
        path: Path,
        *,
        file_metadata: dict[bytes, bytes] | None = None,
    ) -> Path:
        """Write a measurement batch to a Parquet file.

        Converts the RecordBatch to a Table, attaches file-level metadata,
        and writes atomically (temp file + os.replace).
        """
        table = pa.Table.from_batches([batch])
        if file_metadata:
            table = table.replace_schema_metadata(file_metadata)
        atomic_write_table(table, path)
        return path


class ParquetBackend:
    """Save test results to Parquet files with analysis-ready schema.

    Key design principles:
    1. One row per measurement - enables flexible queries
    2. All metadata denormalized - no joins needed
    3. Dynamic schema - inputs/outputs lanes vary per test
    4. Config snapshots in file metadata - full reconstruction possible
    """

    def __init__(self, data_dir: Path | str | None = None):
        # ``data_dir`` is the parent (the project's results dir
        # containing ``runs/``, ``events/``, ``channels/`` subdirs).
        # Aligns with ``RunStore._data_dir`` and
        # ``ProjectConfig.data_dir``. Parquets are written under
        # ``self._runs_dir = data_dir / "runs"``.
        self.data_dir = resolve_data_dir(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir = self.data_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._writer = ParquetMeasurementWriter()

    def save_test_run(
        self,
        test_run: TestRun,
        instrument_records: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Save test run to Parquet with analysis-ready schema.

        Creates files:
            runs/{date}/{timestamp}_{run_id8}_{serial}.parquet  (with serial)
            runs/{date}/{timestamp}_{run_id8}.parquet           (without serial)

        All timestamps are UTC for consistent cross-timezone analysis.

        Args:
            test_run: Complete TestRun with steps, vectors, and measurements.

        Returns:
            Path to the Parquet file.
        """
        # UTC timestamp for filename (compact ISO 8601 basic format)
        timestamp = test_run.started_at.strftime("%Y%m%dT%H%M%SZ")
        date_str = test_run.started_at.strftime("%Y-%m-%d")
        uut_serial = test_run.uut.serial.strip() if test_run.uut.serial else ""

        # Create date directory under runs/
        date_dir = self._runs_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # timestamp, then run_id (always present, fixed position), then the
        # optional serial last — so serial's absence never shifts the leading
        # parts and the run_id breaks same-second same-serial collisions.
        filename = _run_parquet_filename(timestamp, str(test_run.id), uut_serial)

        # Determine parquet path for _ref/ directory creation
        parquet_path = date_dir / filename

        # Run row first — written at the start of the parquet so row-
        # group min/max stats prune ``WHERE record_type = 'run'`` to the
        # first row group, and so ``parquet-tools head`` surfaces run
        # identity immediately. Always present, including for runs with
        # no steps or measurements (in which case the run row alone is
        # the entire parquet — naturally handles the placeholder case).
        rows: list[dict[str, Any]] = [self._build_run_row(test_run, instrument_records)]

        # Append a ``record_type='step'`` row for every (step, vector)
        # — containers, action steps, swept variants, and
        # planned-but-unrun vectors all live in the same parquet under
        # the unified RUN_ROW_SCHEMA. Step rows are emitted regardless
        # of whether measurements exist for the same pair; queries
        # discriminate via ``record_type`` rather than via row absence.
        self._append_step_rows(test_run, rows)

        # Convert to RecordBatch with explicit schema
        schema = _build_write_schema(rows)
        table = table_from_rows(rows, schema)
        batch = table.combine_chunks().to_batches()[0]

        # Single unified parquet — write triggers daemon notify.
        metadata = self._build_file_metadata(test_run)
        self._writer.write_batch(batch, parquet_path, file_metadata=metadata)

        return parquet_path

    def _append_step_rows(self, test_run: TestRun, rows: list[dict[str, Any]]) -> None:
        """Append step and vector rows for the offline batch path.

        Used by the batch writer (``save_test_run``). For each step in the
        TestRun:
        - ONE step row (vector_index always NULL — a step row never carries
          its own sweep index, top-level or nested)
        - ONE vector row per vector in ``step.vectors`` (from own VectorStarted
          events — Mode-1 outer, class-outer, or Mode-2 in-body)

        Step-scope measurements (in ``step.measurements``) ride the step row;
        vector measurements ride their vector row.
        """
        if not test_run.steps:
            return

        run_context = build_run_metadata(test_run)
        run_outcome = test_run.outcome.value if test_run.outcome else None
        run_ended_at = test_run.ended_at
        ref_saver = self._filestore_ref_saver(test_run)

        for index, step in enumerate(test_run.steps):
            step_path = step.step_path or step.name
            step_started = step.started_at or test_run.started_at
            step_ended = step.ended_at or test_run.ended_at or test_run.started_at
            instruments = list(step.instrument_records or [])

            # step.vector_index is always NULL at rest — the invariant the
            # event-driven accumulator honors (a step row never carries its
            # own sweep index, swept or not; vectors are separate rows).
            at_rest_vi: int | None = None

            step_meas = [
                {
                    "name": m.name,
                    "value": m.value,
                    "unit": m.unit,
                    "outcome": m.outcome.value if m.outcome else None,
                    "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                    "limit_low": m.limit_low,
                    "limit_high": m.limit_high,
                    "limit_nominal": m.limit_nominal,
                    "limit_comparator": m.limit_comparator,
                    "characteristic_id": m.characteristic_id,
                    "spec_ref": m.spec_ref,
                    "uut_pin": m.uut_pin,
                    "fixture_connection": m.fixture_connection,
                    "instrument_name": m.instrument_name,
                    "instrument_resource": m.instrument_resource,
                    "instrument_channel": m.instrument_channel,
                }
                for m in (step.measurements or [])
            ]

            step_entry = step_entry_dict(
                index=index,
                name=step.name,
                node_id=step.node_id,
                file=step.file,
                function=step.function,
                class_name=step.class_name,
                module=step.module,
                step_path=step_path,
                description=step.description,
                markers=step.markers,
                outcome=step.outcome.value if step.outcome else None,
                started_at=step_started,
                ended_at=step_ended,
                vector_index=at_rest_vi,
                inputs=dict(step.inputs),
                outputs=dict(step.outputs),
                measurements=step_meas,
                measurement_count=len(step_meas),
                step_retry=step.retry,
                instrument_records=instruments,
            )
            rows.append(
                build_step_row(
                    run_context=run_context,
                    entry=step_entry,
                    run_outcome=run_outcome,
                    run_ended_at=run_ended_at,
                    instruments=instruments,
                )
            )

            for vec_offset, vector in enumerate(step.vectors):
                vec_idx = vector.index if vector.index is not None else vec_offset
                vec_entry = vector_entry_dict(
                    index=index,
                    name=step.name,
                    node_id=step.node_id,
                    file=step.file,
                    function=step.function,
                    class_name=step.class_name,
                    module=step.module,
                    step_path=step_path,
                    markers=step.markers,
                    step_started_at=step_started,
                    step_ended_at=step_ended,
                    vector_index=vec_idx,
                    retry=vector.retry,
                    step_retry=step.retry,
                    outcome=vector.outcome.value if vector.outcome else None,
                    started_at=vector.started_at or step_started,
                    ended_at=vector.ended_at or step_ended,
                    inputs=dict(vector.params),
                    outputs=build_output_columns(vector, ref_saver=ref_saver),
                    input_units=dict(vector.param_units),
                    output_units=dict(vector.observation_units),
                    output_pins=dict(vector.observation_pins),
                    measurements=[build_measurement_struct(m) for m in vector.measurements],
                    instrument_records=instruments,
                )
                rows.append(
                    build_vector_row(
                        run_context=run_context,
                        entry=vec_entry,
                        run_outcome=run_outcome,
                        run_ended_at=run_ended_at,
                        instruments=instruments,
                    )
                )

    @contextmanager
    def _run_store_ctx(self) -> Generator[RunStore, None, None]:
        """Yield a configured RunStore, closing it on exit.

        ParquetBackend's read methods (``list_runs``, ``find_run_file``,
        ``get_run``, etc.) delegate to RunStore — this helper centralises
        the lifecycle.

        Both classes use ``data_dir`` to mean *the parent* (the
        results dir containing ``runs/``, ``events/``, ``channels/``).
        Each appends its own subdir internally.
        """
        store = RunStore(_data_dir=self.data_dir)
        try:
            yield store
        finally:
            store.close()

    def _filestore_ref_saver(self, test_run: TestRun) -> Callable[[str, str, Any], str]:
        """Build the FileStore-backed ref_saver for this run's blobs.

        Item 1d: ref writes route through FileStore (one canonical home
        for all blobs) instead of the per-parquet sibling ``{stem}_ref/``.
        The vector_id-shortened prefix on the FileStore filename preserves
        the audit trail. Shared by the measurement and step-row writers so
        a blob is claim-checked the same way regardless of which lane carries it.

        Lazy import: ``data.files`` transitively pulls PIL / serializers
        that are only needed when this writer runs. Top-level would add
        load cost to every consumer that imports ParquetBackend.
        """
        from litmus.data.files import get_filestore  # noqa: PLC0415

        filestore = get_filestore()
        session_id_str = str(test_run.session_id)

        def ref_saver(vector_id: str, key: str, value: Any) -> str:
            return filestore.write(
                key,
                value,
                session_id=session_id_str,
                vector_id=vector_id,
            )

        return ref_saver

    def _build_run_row(
        self,
        test_run: TestRun,
        instrument_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build the single ``record_type='run'`` row for the parquet.

        Carries run-level identity / UUT / station / fixture / environment
        columns. Step and measurement columns are NULL. Always present (one per
        parquet); for empty runs it is the entire parquet.
        """
        return build_run_row(
            run_context=build_run_metadata(test_run),
            run_outcome=test_run.outcome.value if test_run.outcome else None,
            run_ended_at=test_run.ended_at,
            instruments=instrument_records or [],
        )

    def _build_file_metadata(self, test_run: TestRun) -> dict[bytes, bytes]:
        """Build Parquet file-level metadata."""
        return _build_parquet_metadata(
            environment_json=test_run.environment_json,
            custom_metadata=dict(test_run.custom_metadata) or None,
        )

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        """List recent test runs. Delegates to RunStore."""
        with self._run_store_ctx() as store:
            return store.list_runs(limit=limit)

    def find_run_file(self, run_id: str) -> Path | None:
        """Find parquet file for a run_id. Delegates to RunStore."""
        with self._run_store_ctx() as store:
            return store.find_run_file(run_id)

    def get_run(self, run_id: str) -> RunSummary | None:
        """Get a specific test run by ID. Delegates to RunStore."""
        with self._run_store_ctx() as store:
            return store.get_run(run_id)

    def get_measurements(self, run_id: str) -> list[dict[str, Any]]:
        """Get all measurements for a specific test run. Delegates to RunStore."""
        with self._run_store_ctx() as store:
            return store.get_measurements(run_id)

    def get_measurement(
        self,
        file_path: str,
        measurement_name: str,
        *,
        step_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get rows for a specific measurement name with row-group pushdown."""
        with self._run_store_ctx() as store:
            return store.get_measurement(file_path, measurement_name, step_index=step_index)

    def get_vectors(self, run_id: str) -> list[dict]:
        """Get unique test vectors for a specific test run."""
        measurements = self.get_measurements(run_id)
        if not measurements:
            return []

        # Group by (step_name, vector_index, retry)
        vectors_seen: dict[tuple, dict] = {}
        for m in measurements:
            key = (m.get("step_name"), m.get("vector_index"), m.get("vector_retry"))
            if key not in vectors_seen:
                # Extract vector-level info
                vector_info = {
                    "test_run_id": m.get("run_id"),
                    "step_name": m.get("step_name"),
                    "index": m.get("vector_index"),
                    "retry": m.get("vector_retry"),
                    "outcome": m.get("vector_outcome"),
                    "started_at": m.get("vector_started_at"),
                    "ended_at": m.get("vector_ended_at"),
                    "uut_serial_number": m.get("uut_serial_number"),
                    "part_id": m.get("part_id"),
                    "station_id": m.get("station_id"),
                }
                vector_info["params"] = _params_from_inputs(m.get("inputs") or {})
                vectors_seen[key] = vector_info

        return list(vectors_seen.values())

    def get_run_metadata(self, run_id: str) -> dict[str, str] | None:
        """Get file-level metadata (config snapshots) for a run."""
        with self._run_store_ctx() as store:
            pq_file = store.find_run_file(run_id)
        if pq_file is None:
            return None

        try:
            pf = pq.ParquetFile(pq_file)
            raw_metadata = pf.schema_arrow.metadata or {}
            return {k.decode("utf-8"): v.decode("utf-8") for k, v in raw_metadata.items()}
        except (OSError, pa.ArrowInvalid):
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
            raise FileNotFoundError(f"No Parquet file found for run '{run_id}' in {self.data_dir}/")
        return reconstruct_test_run_from_file(pq_file)

    def save_from_rows(
        self,
        rows: list[dict[str, Any]],
        started_at: datetime,
        uut_serial: str,
        run_id: str,
        file_metadata: dict[bytes, bytes] | None = None,
    ) -> Path:
        """Save pre-built flat row dicts to Parquet.

        Used by :func:`materialize_run_to_parquet` to write
        accumulated rows without needing a TestRun object.
        """
        if not rows:
            raise ValueError("save_from_rows() requires at least one row")

        timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        date_str = started_at.strftime("%Y-%m-%d")
        uut_serial = uut_serial.strip() if uut_serial else ""

        date_dir = self._runs_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        filename = _run_parquet_filename(timestamp, run_id, uut_serial)

        parquet_path = date_dir / filename

        schema = _build_write_schema(rows)
        table = table_from_rows(rows, schema)
        batch = table.combine_chunks().to_batches()[0]

        self._writer.write_batch(batch, parquet_path, file_metadata=file_metadata)

        return parquet_path


# EventAccumulator is defined in _event_accumulator.py (lightweight,
# no parquet/subscriber deps) and imported above. The runs daemon
# holds one EventAccumulator per in-flight run in its AccumulatorPool;
# materialize_run_to_parquet (below) takes one of those and writes the
# canonical parquet.


# ---------------------------------------------------------------------------
# Free-standing materializer — accumulator state → parquet file
# ---------------------------------------------------------------------------
#
# Called by the runs daemon's event-dispatch loop when ``RunEnded`` lands
# (real or synthetic-from-orphan-sweep). The daemon's
# :class:`~litmus.data.accumulator_pool.AccumulatorPool` already holds
# the run's :class:`EventAccumulator`; the materializer writes its
# state to disk via :class:`ParquetBackend`. No subscriber class
# needed — projection lives on the accumulator, writing lives here.


def _build_unified_rows_from_acc(
    acc: EventAccumulator, run_ended_at: datetime, run_outcome: str
) -> list[dict[str, Any]]:
    """Build the per-run unified-rows list from an accumulator's state.

    Free-standing because the daemon calls it with accumulator instances
    drawn from its pool; not method-on-class because EventAccumulator is
    the pure projection and shouldn't know about output formats.

    Record order: one ``run`` row, then ``vector`` rows (each carrying its
    nested ``measurements``), then ``step`` rows. No fabricated rows: a
    verify-less vector / assert fail / observation-only execution is
    represented by its vector or step record, never a synthesized
    measurement.
    """
    rows: list[dict[str, Any]] = []
    run_row = _build_run_row_from_acc(acc, run_ended_at=run_ended_at, run_outcome=run_outcome)
    if run_row is not None:
        rows.append(run_row)
    for entry in acc._build_vector_results_from_events():
        vector_row = _build_vector_row_from_acc(
            acc, entry, run_ended_at=run_ended_at, run_outcome=run_outcome
        )
        if vector_row is not None:
            rows.append(vector_row)
    for entry in acc._build_step_results_from_events():
        step_row = _build_step_row_from_acc(
            acc, entry, run_ended_at=run_ended_at, run_outcome=run_outcome
        )
        if step_row is not None:
            rows.append(step_row)
    return rows


def _build_run_row_from_acc(
    acc: EventAccumulator, *, run_ended_at: datetime, run_outcome: str
) -> dict[str, Any] | None:
    s = acc._run_started
    if not s:
        return None
    return build_run_row(
        run_context=run_context_from_run_started(s, s, include_env=True),
        run_outcome=run_outcome,
        run_ended_at=run_ended_at,
        instruments=acc._build_instrument_records(),
    )


def _build_step_row_from_acc(
    acc: EventAccumulator,
    entry: dict[str, Any],
    *,
    run_ended_at: datetime,
    run_outcome: str,
) -> dict[str, Any] | None:
    s = acc._run_started
    if not s:
        return None
    return build_step_row(
        run_context=run_context_from_run_started(s, s, include_env=True),
        entry=entry,
        run_outcome=run_outcome,
        run_ended_at=run_ended_at,
        instruments=entry.get("instrument_records") or [],
    )


def _build_vector_row_from_acc(
    acc: EventAccumulator,
    entry: dict[str, Any],
    *,
    run_ended_at: datetime,
    run_outcome: str,
) -> dict[str, Any] | None:
    s = acc._run_started
    if not s:
        return None
    return build_vector_row(
        run_context=run_context_from_run_started(s, s, include_env=True),
        entry=entry,
        run_outcome=run_outcome,
        run_ended_at=run_ended_at,
        instruments=entry.get("instrument_records") or [],
    )


def _build_file_metadata_from_acc(acc: EventAccumulator) -> dict[bytes, bytes]:
    s = acc._run_started
    if not s:
        return _build_parquet_metadata()
    return _build_parquet_metadata(
        environment_json=s.environment_json,
        custom_metadata=dict(s.custom_metadata) or None,
    )


def materialize_run_to_parquet(
    acc: EventAccumulator,
    output_dir: Path,
    *,
    outcome: str | None = None,
    run_ended_at: datetime | None = None,
) -> Path | None:
    """Materialize an accumulator's state to a per-run parquet file.

    Returns the parquet path on success, ``None`` when nothing was
    written (no ``RunStarted`` seen, or empty run with no
    measurements / executed steps).

    Args:
        acc: The :class:`EventAccumulator` holding the run's events.
        output_dir: Where to write — the runs daemon's data dir.
        outcome: Final run outcome. ``None`` falls back to ``"aborted"``
            (matches the orphan-sweep semantic).
        run_ended_at: Wall-clock time the run ended. Defaults to ``now()``.
    """
    s = acc._run_started
    if not s:
        return None

    ended_at = run_ended_at if run_ended_at is not None else _utcnow()
    final_outcome = outcome if outcome is not None else "aborted"

    rows = _build_unified_rows_from_acc(acc, ended_at, final_outcome)
    if not rows:
        return None

    backend = ParquetBackend(data_dir=output_dir)
    return backend.save_from_rows(
        rows,
        started_at=s.occurred_at,
        uut_serial=s.uut_serial_number,
        run_id=str(s.run_id) if s.run_id else "",
        file_metadata=_build_file_metadata_from_acc(acc),
    )


def _resolve_ref_to_path(parquet_path: Path | None, ref: str) -> Path | None:
    """Resolve a file ref to an on-disk path. Item 1d dual-path.

    Returns ``None`` for non-file references (channel://, plain
    strings) or unresolvable URIs. Callers decide what to do with
    the un-resolution (typically return the ref as-is).

    ``parquet_path`` is only consulted for legacy
    ``file://_ref/{filename}`` URIs (per-parquet sidecar layout).
    New FileStore-shape URIs (``file://{date}/{session_id}/{filename}``)
    resolve without it.
    """
    raw = ref
    if raw.startswith("file://"):
        raw = raw[len("file://") :]

    # Legacy: starts with the per-parquet ``_ref/`` prefix.
    if raw.startswith(REF_PATH_PREFIX):
        if parquet_path is None:
            return None
        filename = raw[len(REF_PATH_PREFIX) :]
        return parquet_path.parent / (parquet_path.stem + "_ref") / filename

    # New (item 1d) FileStore refs (``file://{date}/{session_id}/{filename}``) are
    # NOT path-resolved here — ``load_file`` reads them as bytes through the
    # blob backend (the store owns where they live; no path crosses out).
    return None


def load_file(parquet_path: Path | None, ref: str) -> Any:
    """Load a file reference (``file://`` URI or legacy ``_ref/`` path).

    Dual-path post-item-1d:

    - New: ``file://{date}/{session_id}/{filename}`` — resolves through
      FileStore (canonical home for all artifacts).
    - Legacy: ``file://_ref/{filename}`` or bare ``_ref/{filename}`` —
      resolves to the parquet's sibling ``{stem}_ref/`` directory.
      Stays for the lifetime of pre-1d parquets on disk.

    Args:
        parquet_path: Path to the parquet file (used to locate the
            legacy ``_ref/`` sibling dir).
        ref: Reference string — any of the three shapes above.

    Returns:
        Loaded data in appropriate format:
        - .npz → Waveform model (if has Y, t0, dt) or dict
        - .npy → numpy array
        - .json → dict or Pydantic model
        - .bin → bytes
        - .arrow → Arrow Table
        - .pkl → pickled object
        - Other → raw file path
    """
    # FileStore artifact ref → read the bytes through the backend (the store
    # owns where they live: local disk or a remote object store). Legacy
    # ``file://_ref/`` / bare ``_ref/`` refs predate FileStore and stay
    # path-based (a per-parquet sibling dir).
    raw = ref[len("file://") :] if ref.startswith("file://") else ref
    if ref.startswith("file://") and not raw.startswith(REF_PATH_PREFIX):
        from litmus.data.files import get_filestore  # noqa: PLC0415

        payload = get_filestore().read(ref)
        if payload is None:
            return ref  # Unresolved / missing artifact
        return _deserialize_ref(
            PurePosixPath(raw).suffix.lower(),
            lambda: io.BytesIO(payload),
            ref,
            fallback=payload,
        )

    path = _resolve_ref_to_path(parquet_path, ref)
    if path is None or not path.exists():
        return ref  # Not a file reference, unresolved, or missing
    return _deserialize_ref(path.suffix.lower(), lambda: path.open("rb"), ref, fallback=path)


def _deserialize_ref(
    ext: str,
    open_stream: Callable[[], BinaryIO],
    ref: str,
    *,
    fallback: Any,
) -> Any:
    """Deserialize a file ref by extension from a binary stream.

    Source-agnostic: ``open_stream`` yields a fresh readable for either a
    FileStore artifact (``io.BytesIO`` over backend bytes) or a legacy
    on-disk ref (the file handle). ``fallback`` is returned for unknown
    extensions or when numpy is absent (the raw bytes for a FileStore ref,
    the on-disk path for a legacy ref); a decode error returns ``ref``.
    """
    try:
        if ext == ".npz":
            if not HAS_NUMPY:
                return fallback
            # numpy import is deliberately deferred — top-level numpy imports
            # add ~150ms to every consumer of this module (RunsQuery,
            # MeasurementsQuery, the runs daemon). Readers that never touch
            # .npz blobs should not pay it.
            import numpy as np  # noqa: PLC0415

            with open_stream() as f:
                data = dict(np.load(f, allow_pickle=True))
            if "Y" in data and "t0" in data and "dt" in data:
                attributes = {k: v for k, v in data.items() if k not in ("Y", "t0", "dt")}
                # t0 is stored as ISO-8601 string by the serializer (datetime
                # can't go into np.savez directly). Empty string = "unknown".
                t0_str = str(data["t0"])
                t0_val = datetime.fromisoformat(t0_str) if t0_str else None
                return Waveform(
                    Y=data["Y"].tolist(),
                    t0=t0_val,
                    dt=float(data["dt"]),
                    attributes=attributes,
                )
            return data
        elif ext == ".npy":
            if not HAS_NUMPY:
                return fallback
            import numpy as np  # noqa: PLC0415

            with open_stream() as f:
                return np.load(f)
        elif ext == ".json":
            with open_stream() as f:
                return json.loads(f.read())
        elif ext == ".bin":
            with open_stream() as f:
                return f.read()
        elif ext == ".arrow":
            with open_stream() as f:
                return ipc.open_file(f).read_all()
        elif ext == ".pkl":
            with open_stream() as f:
                return pickle.load(f)
        else:
            return fallback
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        pickle.UnpicklingError,
        EOFError,
        pa.ArrowInvalid,
    ) as exc:
        logger.warning("Failed to load reference %s: %s", ref, exc)
        return ref


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
        # Item 1d: new FileStore-shape URIs resolve without
        # parquet_path (FileStore walks date dirs itself). Legacy
        # ``file://_ref/...`` URIs still need parquet_path for the
        # per-parquet sibling-dir resolution.
        return load_file(parquet_path, value)

    if scheme == "channel":
        if channel_store is None:
            return value
        try:
            ticket = parse_channel_uri(value)
            return channel_store.query(
                ticket.channel_id,
                session_id=ticket.session_id or None,
                sample_offset=ticket.sample_offset,
            )
        except Exception:  # noqa: BLE001
            # A dangling or unreachable channel degrades to "unavailable" (return
            # the URI) — never crash the caller/UI. Mirrors load_file's missing-
            # artifact behaviour: a clean failure surfaced, not silent corruption.
            logger.debug("Channel ref %r could not be resolved (unavailable)", value)
            return value

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


def extract_refs(parquet_path: Path) -> tuple[set[tuple[str, str]], set[str]]:
    """Channel ``(channel_id, session_id)`` pairs + ``file://`` keys a run references.

    Scans the run's string columns (outputs lane structs and others) for
    ``channel://`` / ``file://`` URIs — the run's full reachable set, both
    schemes. Used by promote (carry a run's data) and retention (reference-aware
    file pruning).
    """
    channels: set[tuple[str, str]] = set()
    files: set[str] = set()
    try:
        table = pq.read_table(parquet_path)
    except (OSError, pa.ArrowException):
        return channels, files
    for name in table.column_names:
        col = table.column(name)
        if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)):
            continue
        for v in col.to_pylist():
            if not is_ref(v):
                continue
            if v.startswith("channel://"):
                # Retention is per-(channel, session): a channel is reachable if
                # any ticket references it, regardless of offset. Drop the offset.
                ticket = parse_channel_uri(v)
                if ticket.channel_id and ticket.session_id:
                    channels.add((ticket.channel_id, ticket.session_id))
            else:  # file://
                files.add(v[len("file://") :])
    return channels, files


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
    if not pq_file.exists():
        raise FileNotFoundError(f"Parquet file not found: {pq_file}")

    pf = pq.ParquetFile(pq_file)
    table = pf.read()
    rows = table.to_pylist()
    if not rows:
        raise FileNotFoundError(f"Parquet file is empty: {pq_file}")

    first = rows[0]

    # Required identity columns — fail fast with a clear error rather
    # than letting bracket-access raise KeyError mid-reconstruction.
    run_id_str = first.get("run_id")
    if not run_id_str:
        raise ValueError(f"Parquet file missing required 'run_id' column: {pq_file}")
    run_started_at = first.get("run_started_at")
    if run_started_at is None:
        raise ValueError(f"Parquet file missing required 'run_started_at' column: {pq_file}")

    # Read file-level metadata for config snapshots
    raw_meta = pf.schema_arrow.metadata or {}
    file_meta = {k.decode(): v.decode() for k, v in raw_meta.items()}

    # Group rows for reconstruction. Vector rows are the carriers: each
    # holds its conditions (inputs), context (outputs), and nested
    # ``measurements``. Step rows supply step-level timing, outcome, and the
    # instrument arrays. Keyed by ``(step_name, step_index)``.
    step_vector_rows: dict[tuple[str | None, int | None], list[dict]] = defaultdict(list)
    step_meta_rows: dict[tuple[str | None, int | None], dict] = {}
    step_timing: dict[tuple[str | None, int | None], dict[str, Any]] = {}

    for row in rows:
        rt = row.get("record_type")
        if rt == "run":
            # Run row carries run-level identity only; doesn't participate
            # in step/vector grouping. Run-level fields are denormalized
            # onto every other row, so reconstruction picks them up there.
            continue
        sk = (row.get("step_name"), row.get("step_index"))
        if sk not in step_timing:
            step_timing[sk] = {
                "started_at": row.get("step_started_at"),
                "ended_at": row.get("step_ended_at"),
            }
        if rt == "vector":
            step_vector_rows[sk].append(row)
        elif rt == "step":
            step_meta_rows[sk] = row

    # Build steps
    steps: list[TestStep] = []
    all_sks = sorted(
        set(step_vector_rows) | set(step_meta_rows),
        key=lambda x: (x[1] or 0, x[0] or ""),
    )
    for sk in all_sks:
        vector_rows = step_vector_rows.get(sk, [])
        step_row = step_meta_rows.get(sk)

        # Instrument records from the step row (fall back to a vector row).
        instr_source = step_row or (vector_rows[0] if vector_rows else {})
        step_instr: list[dict[str, Any]] = instr_source.get("instruments") or []

        vectors: list[TestVector] = []
        for vr in sorted(
            vector_rows,
            key=lambda r: (r.get("vector_index") or 0, r.get("vector_retry") or 0),
        ):
            params: dict[str, Any] = _params_from_inputs(decode_lane_structs(vr.get("inputs")))
            observations = decode_lane_structs(vr.get("outputs"))
            measurements: list[Measurement] = []
            for ms in vr.get("measurements") or []:
                outcome_str = ms.get("outcome")
                m = Measurement(
                    name=ms.get("name") or "",
                    value=ms.get("value"),
                    unit=ms.get("unit"),
                    limit_low=ms.get("limit_low"),
                    limit_high=ms.get("limit_high"),
                    limit_nominal=ms.get("limit_nominal"),
                    limit_comparator=ms.get("limit_comparator"),
                    outcome=Outcome(outcome_str) if outcome_str else None,
                    characteristic_id=ms.get("characteristic_id"),
                    spec_ref=ms.get("spec_ref"),
                    uut_pin=ms.get("uut_pin"),
                    instrument_name=ms.get("instrument_name"),
                    instrument_resource=ms.get("instrument_resource"),
                    instrument_channel=ms.get("instrument_channel"),
                    fixture_connection=ms.get("fixture_connection"),
                )
                ts = ms.get("timestamp")
                if ts is not None:
                    m.timestamp = ts
                measurements.append(m)

            vec_outcome_str = vr.get("vector_outcome")
            vectors.append(
                TestVector(
                    index=vr.get("vector_index") or 0,
                    retry=vr.get("vector_retry") or 0,
                    params=params,
                    observations=observations,
                    outcome=Outcome(vec_outcome_str) if vec_outcome_str else Outcome.PASSED,
                    measurements=measurements,
                    started_at=vr.get("vector_started_at") or run_started_at,
                    ended_at=vr.get("vector_ended_at"),
                )
            )

        timing = step_timing.get(sk, {})
        # Prefer the stored step_outcome column (cascade rollup written at
        # row-build time); fall back to deriving from vector outcomes.
        step_outcome_str = step_row.get("step_outcome") if step_row else None
        if step_outcome_str:
            step_outcome = Outcome(step_outcome_str)
        elif any(v.outcome == Outcome.FAILED for v in vectors):
            step_outcome = Outcome.FAILED
        else:
            step_outcome = Outcome.PASSED

        steps.append(
            TestStep(
                name=sk[0] or "",
                started_at=timing.get("started_at") or run_started_at,
                ended_at=timing.get("ended_at"),
                outcome=step_outcome,
                vectors=vectors,
                instrument_records=step_instr if step_instr else None,
            )
        )

    run_outcome_str = first.get("run_outcome")
    run_outcome = Outcome(run_outcome_str) if run_outcome_str else Outcome.PASSED

    return TestRun(
        id=UUID(run_id_str),
        started_at=run_started_at,
        ended_at=first.get("run_ended_at"),
        uut=UUT(
            serial=first.get("uut_serial_number") or "",
            part_number=first.get("uut_part_number"),
            revision=first.get("uut_revision"),
            lot_number=first.get("uut_lot_number"),
        ),
        part_id=first.get("part_id"),
        part_name=first.get("part_name"),
        part_revision=first.get("part_revision"),
        station_id=first.get("station_id"),
        station_name=first.get("station_name"),
        station_type=first.get("station_type"),
        station_location=first.get("station_location"),
        station_hostname=first.get("station_hostname"),
        fixture_id=first.get("fixture_id"),
        test_phase=first.get("test_phase"),
        operator_id=first.get("operator_id"),
        operator_name=first.get("operator_name"),
        git_commit=first.get("git_commit"),
        outcome=run_outcome,
        steps=steps,
        environment_json=file_meta.get("environment_json"),
        custom_metadata=(
            json.loads(file_meta["custom_metadata"]) if file_meta.get("custom_metadata") else {}
        ),
    )
