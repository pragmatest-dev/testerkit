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
    CUSTOM_PREFIX,
    HAS_NUMPY,
    INPUT_PREFIX,
    INSTRUMENT_ARRAY_KEYS,
    OUTPUT_PREFIX,
    REF_PATH_PREFIX,
    build_row,
    build_run_metadata,
    build_run_row,
    build_step_manifest,
    build_step_row,
    extract_prefixed_fields,
    run_context_from_run_started,
    validate_observation_kinds,
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

# Suffix patterns for stimulus signal-path columns (in_{param}_{suffix}).
# A column like "in_vin_instrument" is metadata, not a param value.
_STIMULUS_SUFFIXES = ("_instrument", "_resource", "_channel", "_uut_pin", "_fixture_connection")

# Outcome priority for deterministic worst-case selection from a set.
# Lower rank = worse outcome. Ties (same rank) pick the same "worst" value.
OUTCOME_RANK: dict[str, int] = {"failed": 0, "errored": 1, "skipped": 2, "passed": 3}


def _is_param_column(col: str) -> bool:
    """True if col is an in_* param value, not signal-path metadata."""
    return col.startswith(INPUT_PREFIX) and not any(col.endswith(s) for s in _STIMULUS_SUFFIXES)


def _ensure_instrument_arrays(d: dict[str, Any]) -> dict[str, Any]:
    """Mutate ``d`` so every ``INSTRUMENT_ARRAY_KEYS`` key is present (default ``[]``).

    Single source of truth for the schema-consistency invariant: any
    write-side dict that flows into the parquet writer needs every
    instrument-array column present, even when no instruments were
    connected. Returns ``d`` for chaining.
    """
    for key in INSTRUMENT_ARRAY_KEYS:
        d.setdefault(key, [])
    return d


def _build_parquet_metadata(
    *,
    environment_json: str | None = None,
    step_results: list[dict[str, Any]] | None = None,
    profile_facets: dict[str, str] | None = None,
) -> dict[bytes, bytes]:
    """Build Parquet file-level metadata.

    Shared by ParquetBackend (from TestRun) and
    :func:`materialize_run_to_parquet` (from cached RunStarted event).
    """
    metadata: dict[bytes, bytes] = {}

    if environment_json:
        metadata[b"environment_json"] = environment_json.encode("utf-8")
    if step_results:
        metadata[b"step_results"] = json.dumps(step_results).encode("utf-8")
    if profile_facets:
        metadata[b"profile_facets_json"] = json.dumps(profile_facets).encode("utf-8")

    from litmus import __version__

    metadata[b"litmus_version"] = __version__.encode("utf-8")
    metadata[b"schema_version"] = SCHEMA_VERSION.encode()
    return metadata


# Lazy version lookup: ``litmus/__init__.py`` re-exports ``LitmusClient``
# (which depends on this module via ``ParquetBackend``), so a top-level
# ``from litmus import __version__`` would cycle. Module load-time would
# break; deferring to call time is fine because the function only runs
# at parquet-write time, well after the full package is loaded.


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
    3. Dynamic schema - in_* columns vary per test
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
        uut_serial = test_run.uut.serial.strip() if test_run.uut.serial else ""

        # Create date directory under runs/
        date_dir = self._runs_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # Filename: timestamp first, serial if present
        if uut_serial:
            filename = f"{timestamp}_{uut_serial}.parquet"
        else:
            filename = f"{timestamp}.parquet"

        # Determine parquet path for _ref/ directory creation
        parquet_path = date_dir / filename

        # Run row first — written at the start of the parquet so row-
        # group min/max stats prune ``WHERE record_type = 'run'`` to the
        # first row group, and so ``parquet-tools head`` surfaces run
        # identity immediately. Always present, including for runs with
        # no steps or measurements (in which case the run row alone is
        # the entire parquet — naturally handles the placeholder case).
        rows: list[dict[str, Any]] = [self._build_run_row(test_run, instrument_arrays)]

        # Build measurement rows (may create _ref/ directory for large data)
        rows.extend(self._build_measurement_rows(test_run, instrument_arrays))

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
        """Append a ``record_type='step'`` row for every (step, vector).

        Used by the batch writer (``save_test_run``). Emits a step row
        for each manifest entry unconditionally — measurements get
        their own ``record_type='measurement'`` rows, and queries
        discriminate via the explicit kind column. Delegates to the
        shared ``build_step_row`` helper so streaming and batch paths
        produce identical step rows for the same logical step.
        """
        if not test_run.steps:
            return

        run_context = build_run_metadata(test_run)
        run_outcome = test_run.outcome.value if test_run.outcome else None
        run_ended_at = test_run.ended_at
        instruments = _ensure_instrument_arrays({})

        # ``build_step_manifest`` produces one entry per (step, vector)
        # pair — same shape ``ParquetSubscriber._build_step_row``
        # consumes from ``StepManifest`` events. Routing both writers
        # through the shared ``build_step_row`` helper keeps them in
        # lock-step.
        for entry in build_step_manifest(test_run):
            rows.append(
                build_step_row(
                    run_context=run_context,
                    entry=entry,
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

    def _build_measurement_rows(
        self,
        test_run: TestRun,
        instrument_arrays: dict[str, list] | None = None,
    ) -> list[dict[str, Any]]:
        """Build one row per measurement with all metadata denormalized.

        Item 9 (auto-promotion): a vector with 0 measurements + ≥1
        observation produces one synthesized row per observation,
        ``name=<obs>``, ``value=None``, ``outcome=DONE``. A vector
        with ≥1 measurement produces verify rows only and the
        observations ride along as ``out_*`` columns via
        :func:`build_output_columns`.

        Item 10 (kind-stable ``out_<name>``): the same name across
        vectors must keep the same kind. A per-run registry catches
        mixed-type violations at materialization with a clear
        ``ValueError`` rather than letting parquet coerce / refuse.
        """

        # Item 1d: ref writes route through FileStore (one canonical
        # home for all blobs) instead of the per-parquet sibling
        # ``{stem}_ref/``. The vector_id-shortened prefix on the
        # FileStore filename preserves the audit trail.
        # Lazy import: data.files transitively pulls PIL / serializers
        # that are only needed when this writer runs. Top-level would
        # add load cost to every consumer that imports ParquetBackend.
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

        def _build(
            measurement: Measurement,
            step: TestStep,
            step_idx: int,
            vector: TestVector,
            step_arrays: dict[str, list],
        ) -> dict[str, Any]:
            row_model = build_row(
                test_run,
                measurement,
                step.name,
                step_idx,
                vector,
                step_arrays,
                ref_saver=ref_saver,
                # Fall back step_path → step.name so the daemon's
                # GROUP BY (step_path, vector_index) gives each
                # logical step its own row. Same fallback for
                # step_started_at / step_ended_at — the daemon
                # filters on ``ended_at IS NOT NULL`` by default,
                # and a step row with no timing information is
                # invisible to operator queries.
                step_path=step.step_path or step.name,
                step_started_at=step.started_at or test_run.started_at,
                step_ended_at=step.ended_at or test_run.ended_at or test_run.started_at,
                step_node_id=step.node_id,
                step_module=step.module,
                step_file=step.file,
                step_class=step.class_name,
                step_function=step.function,
                step_markers=step.markers,
                step_outcome=step.outcome.value if step.outcome else None,
                meta=meta,
            )
            return row_model.to_flat_dict()

        meta = build_run_metadata(test_run)
        rows: list[dict[str, Any]] = []
        out_kind_registry: dict[str, str] = {}
        for step_idx, step in enumerate(test_run.steps):
            step_arrays = step.instrument_arrays or _ensure_instrument_arrays(
                dict(instrument_arrays or {})
            )
            for vector in step.vectors:
                # Item 10: validate observation kinds against the per-run
                # registry. Raises ValueError on mismatch; caller surfaces.
                validate_observation_kinds(
                    out_kind_registry,
                    vector.observations,
                    where=f"vector {vector.index} of step {step.name!r}",
                )

                for measurement in vector.measurements:
                    rows.append(_build(measurement, step, step_idx, vector, step_arrays))

                # Item 9: auto-promote a verify-less vector to ONE DONE
                # placeholder row. An observation is not a measurement, so
                # the row carries measurement_name=NULL / value=NULL /
                # outcome=DONE; every observation rides along as out_<name>
                # via the vector's existing out_* column projection.
                non_underscore_obs = any(
                    not obs_name.startswith("_") for obs_name in vector.observations
                )
                if not vector.measurements and non_underscore_obs:
                    placeholder = Measurement(
                        name="",
                        value=None,
                        outcome=Outcome.DONE,
                    )
                    promoted_row = _build(placeholder, step, step_idx, vector, step_arrays)
                    promoted_row["measurement_name"] = None
                    promoted_row["measurement_value"] = None
                    promoted_row["measurement_outcome"] = Outcome.DONE.value
                    rows.append(promoted_row)
        return rows

    def _build_run_row(
        self,
        test_run: TestRun,
        instrument_arrays: dict[str, list] | None = None,
    ) -> dict[str, Any]:
        """Build the single ``record_type='run'`` row for the parquet.

        Carries run-level identity / UUT / station / fixture / environment
        columns plus ``custom_metadata`` (flattened to ``custom_*``).
        Step and measurement columns are NULL. Always present (one per
        parquet); for empty runs it is the entire parquet.
        """
        instruments = _ensure_instrument_arrays(dict(instrument_arrays or {}))
        return build_run_row(
            run_context=build_run_metadata(test_run),
            run_outcome=test_run.outcome.value if test_run.outcome else None,
            run_ended_at=test_run.ended_at,
            instruments=instruments,
            custom=dict(test_run.custom_metadata),
        )

    def _build_file_metadata(self, test_run: TestRun) -> dict[bytes, bytes]:
        """Build Parquet file-level metadata."""
        return _build_parquet_metadata(
            environment_json=test_run.environment_json,
            step_results=build_step_manifest(test_run),
            profile_facets=test_run.profile_facets or None,
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

    def get_measurements(self, run_id: str, *, _file: str | None = None) -> list[dict[str, Any]]:
        """Get all measurements for a specific test run. Delegates to RunStore."""
        with self._run_store_ctx() as store:
            return store.get_measurements(run_id, _file=_file)

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
                    "uut_serial": m.get("uut_serial"),
                    "part_id": m.get("part_id"),
                    "station_id": m.get("station_id"),
                }
                vector_info["params"] = {k[3:]: v for k, v in m.items() if _is_param_column(k)}
                vectors_seen[key] = vector_info

        return list(vectors_seen.values())

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        """Get step results for a run from the unified parquet's file-level metadata."""
        with self._run_store_ctx() as store:
            pq_file = store.find_run_file(run_id)
        if pq_file is None:
            return []
        return read_step_results(Path(pq_file))

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

        if uut_serial:
            filename = f"{timestamp}_{uut_serial}.parquet"
        else:
            filename = f"{timestamp}.parquet"

        parquet_path = date_dir / filename

        # Ensure instrument array columns exist for schema consistency
        for row in rows:
            _ensure_instrument_arrays(row)

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

    Items 9 + 10 (live materialization path):

    - Item 10: validate ``out_<name>`` kind stability across all
      observation events. Mismatches raise ``ValueError`` here rather
      than letting parquet silently coerce / refuse.
    - Item 9: synthesize DONE rows for verify-less vectors. Each
      observation in a vector with 0 measurements promotes to a row
      with ``value=None``, ``outcome="done"``; the observation value
      itself rides on ``out_<name>``.
    """
    rows: list[dict[str, Any]] = []
    run_row = _build_run_row_from_acc(acc, run_ended_at=run_ended_at, run_outcome=run_outcome)
    if run_row is not None:
        rows.append(run_row)
    # Item 10 — fail loudly on a kind mismatch before we synthesize.
    acc._validate_observation_kinds()
    for event in acc._measurement_events:
        row = acc._build_row(event)
        row["run_ended_at"] = run_ended_at
        row["run_outcome"] = run_outcome
        rows.append(row)
    # Item 9 — promote observations in verify-less vectors.
    for promoted_row in acc._build_promoted_rows():
        promoted_row["run_ended_at"] = run_ended_at
        promoted_row["run_outcome"] = run_outcome
        rows.append(promoted_row)
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
        instruments=acc._build_instrument_arrays(),
        custom=dict(getattr(s, "custom_metadata", None) or {}),
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
        instruments=acc._build_instrument_arrays(),
    )


def _build_file_metadata_from_acc(acc: EventAccumulator) -> dict[bytes, bytes]:
    s = acc._run_started
    if not s:
        return _build_parquet_metadata()
    results = acc._build_step_results_from_events() or None
    return _build_parquet_metadata(
        environment_json=s.environment_json,
        step_results=results,
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
        uut_serial=s.uut_serial,
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


def read_step_results(parquet_path: Path) -> list[dict[str, Any]]:
    """Read step results from sibling _steps.parquet or file-level metadata.

    Checks for ``{stem}_steps.parquet`` first (new format). Falls back to
    JSON in file-level metadata (legacy format).

    Returns an empty list if no step results are stored.
    """
    # Try sibling _steps.parquet first
    steps_path = parquet_path.with_name(parquet_path.stem + "_steps.parquet")
    if steps_path.exists():
        try:
            table = pq.read_table(steps_path)
            return table.to_pylist()
        except (OSError, pa.ArrowInvalid):
            pass

    # Fall back to JSON metadata (legacy files)
    try:
        pf = pq.ParquetFile(parquet_path)
        raw_metadata = pf.schema_arrow.metadata or {}
        results_bytes = raw_metadata.get(b"step_results")
        if results_bytes:
            return json.loads(results_bytes)
    except (OSError, pa.ArrowInvalid, json.JSONDecodeError):
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
        # Item 1d: new FileStore-shape URIs resolve without
        # parquet_path (FileStore walks date dirs itself). Legacy
        # ``file://_ref/...`` URIs still need parquet_path for the
        # per-parquet sibling-dir resolution.
        return load_file(parquet_path, value)

    if scheme == "channel":
        if channel_store is None:
            return value
        try:
            channel_id, session_id = parse_channel_uri(value)
            return channel_store.query(channel_id, session_id=session_id or None)
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

    Scans the run's string columns (``out_*`` etc.) for ``channel://`` / ``file://``
    URIs — the run's full reachable set, both schemes. Used by promote (carry a
    run's data) and retention (reference-aware file pruning).
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
                cid, sid = parse_channel_uri(v)
                if cid and sid:
                    channels.add((cid, sid))
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

    # Group rows for reconstruction. Measurement rows group by
    # (step_name, step_index) → (vector_index, vector_retry) — that's the
    # measurement payload grain. Step rows are tracked separately by
    # vector_index alone so they can backfill TestVectors that recorded
    # no measurements (vector_retry is per-measurement; step rows
    # carry None there and would otherwise create phantom vectors).
    step_groups: dict[
        tuple[str | None, int | None],
        dict[tuple[int | None, int | None], list[dict]],
    ] = defaultdict(lambda: defaultdict(list))
    step_timing: dict[tuple[str | None, int | None], dict[str, Any]] = {}
    step_rows_by_vector: dict[tuple[str | None, int | None], dict[int | None, dict]] = defaultdict(
        dict
    )

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
        if rt == "measurement":
            vk = (row.get("vector_index"), row.get("vector_retry"))
            step_groups[sk][vk].append(row)
        else:
            step_rows_by_vector[sk][row.get("vector_index")] = row

    # Ensure measurement-free vectors still surface — for any step row
    # whose (step, vector_index) has no measurement-group entry, seed
    # an empty group keyed by ``(vector_index, None)``.
    for sk, by_vec in step_rows_by_vector.items():
        existing_vec_indices = {vk[0] for vk in step_groups[sk]}
        for vec_idx, step_row in by_vec.items():
            if vec_idx not in existing_vec_indices:
                step_groups[sk][(vec_idx, None)].append(step_row)

    # Build steps
    steps: list[TestStep] = []
    for sk in sorted(step_groups, key=lambda x: (x[1] or 0, x[0] or "")):
        vector_groups = step_groups[sk]
        vectors: list[TestVector] = []

        # One sample row for step-level extraction (step_instruments_* arrays)
        step_sample_row = next(iter(vector_groups.values()))[0]
        step_instr: dict[str, list] = {}
        for col, val in step_sample_row.items():
            if col.startswith("step_instruments_"):
                if val is not None:
                    step_instr[col] = val if isinstance(val, list) else [val]

        for vk in sorted(vector_groups, key=lambda x: (x[0] or 0, x[1] or 0)):
            group_rows = vector_groups[vk]
            # Filter to measurement-kind rows for the payload loop.
            # Step rows establish the (vector) presence even for
            # measurement-free vectors but carry no measurement payload.
            meas_rows = [r for r in group_rows if r.get("record_type") == "measurement"]
            measurements: list[Measurement] = []

            # Extract params from in_* columns (skipping signal-path metadata
            # like in_vin_instrument) and observations from out_*. Use any
            # row in the group — both kinds carry the denormalized in_*/
            # out_* columns identically.
            sample_row = group_rows[0]
            params: dict[str, Any] = {
                k[len(INPUT_PREFIX) :]: v for k, v in sample_row.items() if _is_param_column(k)
            }
            observations = extract_prefixed_fields(sample_row, OUTPUT_PREFIX)

            for mr in meas_rows:
                outcome_str = mr.get("measurement_outcome")
                m = Measurement(
                    name=mr.get("measurement_name") or "",
                    value=mr.get("measurement_value"),
                    units=mr.get("measurement_units"),
                    limit_low=mr.get("limit_low"),
                    limit_high=mr.get("limit_high"),
                    limit_nominal=mr.get("limit_nominal"),
                    limit_comparator=mr.get("limit_comparator"),
                    outcome=Outcome(outcome_str) if outcome_str else None,
                    characteristic_id=mr.get("characteristic_id"),
                    spec_ref=mr.get("spec_ref"),
                    uut_pin=mr.get("uut_pin"),
                    instrument_name=mr.get("instrument_name"),
                    instrument_resource=mr.get("instrument_resource"),
                    instrument_channel=mr.get("instrument_channel"),
                    fixture_connection=mr.get("fixture_connection"),
                )
                ts = mr.get("measurement_timestamp")
                if ts is not None:
                    m.timestamp = ts
                measurements.append(m)

            # Vector outcome should be uniform across the vector's
            # measurement rows (it's denormalized from the vector model).
            # Warn if a row diverges so silent data corruption surfaces.
            vec_outcomes = {
                mr.get("vector_outcome") for mr in meas_rows if mr.get("vector_outcome")
            }
            if len(vec_outcomes) > 1:
                logger.warning(
                    "Vector %s has inconsistent vector_outcome values across rows: %s",
                    vk,
                    sorted(o for o in vec_outcomes if o is not None),
                )
            vec_outcome_str = min(
                vec_outcomes, key=lambda o: OUTCOME_RANK.get(str(o), 99), default=None
            )
            vectors.append(
                TestVector(
                    index=vk[0] or 0,
                    retry=vk[1] if vk[1] is not None else 0,
                    params=params,
                    observations=observations,
                    outcome=Outcome(vec_outcome_str) if vec_outcome_str else Outcome.PASSED,
                    measurements=measurements,
                    started_at=sample_row.get("vector_started_at") or run_started_at,
                    ended_at=sample_row.get("vector_ended_at"),
                )
            )

        timing = step_timing.get(sk, {})
        # Prefer the stored step_outcome column (cascade rollup written
        # at row-build time). Fall back to deriving from vector
        # outcomes for older parquet files written before the column
        # existed.
        step_outcome_str = step_sample_row.get("step_outcome")
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
                instrument_arrays=step_instr if step_instr else None,
            )
        )

    # Extract custom metadata from custom_* columns
    custom_meta = extract_prefixed_fields(first, CUSTOM_PREFIX)

    run_outcome_str = first.get("run_outcome")
    run_outcome = Outcome(run_outcome_str) if run_outcome_str else Outcome.PASSED

    return TestRun(
        id=UUID(run_id_str),
        started_at=run_started_at,
        ended_at=first.get("run_ended_at"),
        uut=UUT(
            serial=first.get("uut_serial") or "",
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
        custom_metadata=custom_meta or {},
    )
