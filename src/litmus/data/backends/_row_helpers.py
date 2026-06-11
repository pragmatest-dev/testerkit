"""Shared row-building helpers for the parquet backend.

Produces denormalized rows with run-level and measurement-level fields.
This module extracts the common logic so new columns only need to be
added in one place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from litmus.data.models import Measurement, TestRun, TestVector
from litmus.data.ref import classify_value, is_ref
from litmus.environment import EnvironmentSnapshot

try:
    import importlib.util as _ilu

    HAS_NUMPY = _ilu.find_spec("numpy") is not None
except Exception:
    HAS_NUMPY = False

# Canonical list of instrument identity array column names.
# Lives here (data layer) so the daemon and parquet backend can import it
# without pulling in the execution framework.
INSTRUMENT_ARRAY_KEYS: tuple[str, ...] = (
    "step_instruments_name",
    "step_instruments_id",
    "step_instruments_driver",
    "step_instruments_resource",
    "step_instruments_protocol",
    "step_instruments_manufacturer",
    "step_instruments_model",
    "step_instruments_serial",
    "step_instruments_firmware",
    "step_instruments_cal_due",
    "step_instruments_cal_last",
    "step_instruments_cal_certificate",
    "step_instruments_cal_lab",
    "step_instruments_mocked",
)

# Prefix for path references in output columns (legacy, use file:// URIs)
REF_PATH_PREFIX = "_ref/"

# Vector ID prefix length for filename namespacing in _ref/ directories.
VECTOR_ID_LENGTH = 8

# Dynamic-column prefixes for denormalized rows.
INPUT_PREFIX = "in_"
OUTPUT_PREFIX = "out_"
CUSTOM_PREFIX = "custom_"


def extract_prefixed_fields(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Extract fields whose key starts with ``prefix`` and strip it.

    Used by the parquet reconstruction path to invert the flattening
    done by :class:`MeasurementRow.to_flat_dict` (``in_*`` / ``out_*`` /
    ``custom_*``).
    """
    plen = len(prefix)
    return {k[plen:]: v for k, v in row.items() if k.startswith(prefix)}


def _to_datetime(value: Any) -> datetime | None:
    """Coerce a value to ``datetime`` if possible, else ``None``.

    Accepts a ``datetime`` (returned as-is), an ISO-8601 string (parsed
    via ``datetime.fromisoformat``), or anything else (``None``).
    Malformed strings return ``None`` rather than raising.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class MeasurementRow(BaseModel):
    """A single denormalized row for streaming and storage.

    Three row kinds, distinguished by the explicit ``record_type``
    discriminator:

    * ``record_type = 'run'`` — one row per run; carries run-level
      identity / DUT / station / fixture / environment context. Step
      and measurement columns are NULL. Provides an addressable
      "runs table" within the unified per-run parquet (lakehouse
      adopters can ``WHERE record_type = 'run'`` for clean ingest).
    * ``record_type = 'step'`` — one per ``(step_path, vector_index)``
      execution; ``measurement_*`` columns are NULL. Carries denormalized
      run-level columns alongside step context.
    * ``record_type = 'measurement'`` — one per recorded measurement;
      carries the measurement payload plus the same denormalized run +
      step context.

    Steps and measurements share grain ``(run_id, step_path,
    vector_index)``; measurement rows further key on
    ``measurement_name``. Run rows are keyed by ``run_id`` alone.
    """

    model_config = ConfigDict(extra="forbid")

    # Discriminator
    record_type: Literal["run", "step", "measurement"]

    # Session / run identity
    session_id: str
    run_id: str
    slot_id: str | None = None
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None

    # Operator
    operator_id: str | None = None
    operator_name: str | None = None

    # DUT
    dut_serial: str
    dut_part_number: str | None = None
    dut_revision: str | None = None
    dut_lot_number: str | None = None

    # Part
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None

    # Station — id is None for bringup tier (no station YAML loaded)
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    station_hostname: str | None = None

    # Fixture
    fixture_id: str | None = None

    # Test context
    test_phase: str | None = None
    project_name: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None

    # Environment traceability
    python_version: str | None = None
    litmus_version: str | None = None
    env_fingerprint: str | None = None

    # Step/vector context
    step_name: str
    step_index: int
    step_path: str = ""
    parent_path: str = ""
    step_started_at: datetime | None = None
    step_ended_at: datetime | None = None
    step_node_id: str | None = None
    step_module: str | None = None
    step_file: str | None = None
    step_class: str | None = None
    step_function: str | None = None
    step_markers: str | None = None
    step_vector_count: int | None = None
    vector_index: int = 0
    # 0-based retry counter — 0 for the first execution, N for the Nth retry.
    # Per-measurement (NULL on step / run rows). Companion to ``RetryConfig.max_retries``
    # which bounds the count (max_retries=0 → no retries; max_retries=N → up to N retries).
    vector_retry: int | None = None
    vector_started_at: datetime | None = None
    vector_ended_at: datetime | None = None

    # Measurement payload — populated only when record_type == 'measurement'.
    measurement_name: str | None = None
    measurement_timestamp: datetime | None = None
    measurement_value: float | None = None
    measurement_units: str | None = None
    measurement_outcome: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None
    dut_pin: str | None = None
    fixture_connection: str | None = None
    instrument_name: str | None = None
    instrument_resource: str | None = None
    instrument_channel: str | None = None

    # Outcomes (cascade rollups: measurement → vector → step → run)
    step_outcome: str | None = None
    vector_outcome: str | None = None
    run_outcome: str | None = None

    # Dynamic namespaced columns
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    instruments: dict[str, list[str | bool | None]] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to denormalized dict for Parquet write boundary.

        Merges dynamic columns back into the flat namespace:
        - ``inputs`` keys are prefixed with ``in_`` (provide unprefixed keys)
        - ``outputs`` keys are prefixed with ``out_`` (provide unprefixed keys)
        - ``instruments`` keys pass through (already ``instr_``-prefixed)
        - ``custom`` keys are prefixed with ``custom_`` (provide unprefixed keys)

        Datetime values are left as ``datetime`` objects — callers must
        serialise them at the actual write boundary (e.g. ``.isoformat()``).
        """
        row = self.model_dump(
            exclude={"inputs", "outputs", "instruments", "custom"},
        )
        for k, v in self.inputs.items():
            row[f"in_{k}"] = v
        for k, v in self.outputs.items():
            row[f"out_{k}"] = v
        row.update(self.instruments)
        for k, v in self.custom.items():
            row[f"custom_{k}"] = v
        return row


def build_run_metadata(test_run: TestRun) -> dict[str, Any]:
    """Extract run-level metadata fields from a TestRun.

    These fields are identical on every row in a run.  Returns raw
    Python objects (datetime, str, None) — callers that need JSON
    serialisation should post-process timestamps.
    """
    from litmus.execution._state import get_current_slot_id

    return {
        "session_id": str(test_run.session_id),
        "run_id": str(test_run.id),
        "slot_id": get_current_slot_id(),
        "run_started_at": test_run.started_at,
        "run_ended_at": test_run.ended_at,
        # WHO
        "operator_id": test_run.operator_id,
        "operator_name": test_run.operator_name,
        # DUT
        "dut_serial": test_run.dut.serial,
        "dut_part_number": test_run.dut.part_number,
        "dut_revision": test_run.dut.revision,
        "dut_lot_number": test_run.dut.lot_number,
        # Part
        "part_id": test_run.part_id,
        "part_name": test_run.part_name,
        "part_revision": test_run.part_revision,
        # Station
        "station_id": test_run.station_id,
        "station_name": test_run.station_name,
        "station_type": test_run.station_type,
        "station_location": test_run.station_location,
        "station_hostname": test_run.station_hostname,
        # Fixture
        "fixture_id": test_run.fixture_id,
        # Test context
        "test_phase": test_run.test_phase,
        "project_name": test_run.project_name,
        "git_commit": test_run.git_commit,
        "git_branch": test_run.git_branch,
        "git_remote": test_run.git_remote,
        # Environment traceability (scalars from environment snapshot)
        **_env_columns(test_run.environment_json),
    }


def _env_columns(environment_json: str | None) -> dict[str, str | None]:
    """Extract queryable environment columns from the JSON snapshot."""
    if not environment_json:
        return {"python_version": None, "litmus_version": None, "env_fingerprint": None}

    snapshot = EnvironmentSnapshot.model_validate_json(environment_json)
    return {
        "python_version": snapshot.python_version,
        "litmus_version": snapshot.litmus_version,
        "env_fingerprint": snapshot.lockfile_hash,
    }


def run_context_from_run_started(
    run_started: Any | None,
    event: Any,
    *,
    include_env: bool = False,
) -> dict[str, Any]:
    """Run-level context kwargs derived from a cached ``RunStarted`` event.

    Streaming-path counterpart to :func:`build_run_metadata` (which
    operates on a ``TestRun`` model). Both ``the accumulator-to-parquet path``
    and ``_write_steps_parquet`` use this — they previously had drifting
    copies of the same dict, missing different fields.

    ``event`` supplies the row's ``run_id`` (a measurement event may carry
    it before ``RunStarted`` arrives, and the steps writer passes
    ``run_started`` itself). When ``run_started`` is ``None`` (events
    arrived before RunStarted), falls back to a sparse dict with
    placeholder defaults.

    Set ``include_env=True`` to include environment columns
    (``python_version``, ``litmus_version``, ``env_fingerprint``). The
    measurement schema exposes them; the steps schema does not.
    """
    if run_started is None:
        kwargs: dict[str, Any] = {
            "session_id": str(event.session_id),
            "run_id": str(event.run_id) if event.run_id else "",
            "slot_id": None,
            "run_started_at": None,
            "run_ended_at": None,
            "operator_id": None,
            "operator_name": None,
            "dut_serial": "unknown",
            "dut_part_number": None,
            "dut_revision": None,
            "dut_lot_number": None,
            "part_id": None,
            "part_name": None,
            "part_revision": None,
            "station_id": "unknown",
            "station_name": None,
            "station_type": None,
            "station_location": None,
            "station_hostname": None,
            "fixture_id": None,
            "test_phase": None,
            "project_name": None,
            "git_commit": None,
            "git_branch": None,
            "git_remote": None,
        }
    else:
        kwargs = {
            "session_id": str(run_started.session_id),
            "run_id": str(event.run_id) if event.run_id else "",
            "slot_id": run_started.slot_id,
            "run_started_at": run_started.occurred_at,
            "run_ended_at": None,
            "operator_id": run_started.operator_id,
            "operator_name": run_started.operator_name,
            "dut_serial": run_started.dut_serial,
            "dut_part_number": run_started.dut_part_number,
            "dut_revision": run_started.dut_revision,
            "dut_lot_number": run_started.dut_lot_number,
            "part_id": run_started.part_id,
            "part_name": run_started.part_name,
            "part_revision": run_started.part_revision,
            "station_id": run_started.station_id,
            "station_name": run_started.station_name,
            "station_type": run_started.station_type,
            "station_location": run_started.station_location,
            "station_hostname": run_started.station_hostname,
            "fixture_id": run_started.fixture_id,
            "test_phase": run_started.test_phase,
            "project_name": run_started.project_name,
            "git_commit": run_started.git_commit,
            "git_branch": run_started.git_branch,
            "git_remote": run_started.git_remote,
        }
    if include_env:
        env_json = run_started.environment_json if run_started else None
        kwargs.update(_env_columns(env_json))
    return kwargs


def build_measurement_fields(measurement: Measurement) -> dict[str, Any]:
    """Extract measurement-level fields from a Measurement."""
    return {
        "measurement_name": measurement.name,
        "measurement_timestamp": measurement.timestamp,
        "measurement_value": measurement.value,
        "measurement_units": measurement.units,
        # measurement.outcome is contractually set by log_measurement
        # (RuntimeError raised in execution/logger.py if None reaches here).
        "measurement_outcome": measurement.outcome.value if measurement.outcome else None,
        # Limits
        "limit_low": measurement.limit_low,
        "limit_high": measurement.limit_high,
        "limit_nominal": measurement.limit_nominal,
        "limit_comparator": measurement.limit_comparator,
        # Spec traceability
        "characteristic_id": measurement.characteristic_id,
        "spec_ref": measurement.spec_ref,
        # Signal path
        "dut_pin": measurement.dut_pin,
        "fixture_connection": measurement.fixture_connection,
        "instrument_name": measurement.instrument_name,
        "instrument_resource": measurement.instrument_resource,
        "instrument_channel": measurement.instrument_channel,
    }


def build_input_columns(vector: TestVector) -> dict[str, Any]:
    """Build inputs dict from vector params and stimulus records.

    Keys are unprefixed (e.g. ``"vin"``); ``to_flat_dict()`` adds the ``in_`` prefix.
    """
    cols: dict[str, Any] = {}

    for param, value in vector.params.items():
        if param.startswith("_"):
            continue
        cols[param] = value

    for stim in vector.stimulus:
        param = stim.param
        if stim.value is not None:
            cols[param] = stim.value
        if stim.instrument:
            cols[f"{param}_instrument"] = stim.instrument
        if stim.resource:
            cols[f"{param}_resource"] = stim.resource
        if stim.channel:
            cols[f"{param}_channel"] = stim.channel
        if stim.dut_pin:
            cols[f"{param}_dut_pin"] = stim.dut_pin
        if stim.fixture_connection:
            cols[f"{param}_fixture_connection"] = stim.fixture_connection

    return cols


def observation_kind(value: Any) -> str:
    """Item 10: classify an observation value for parquet column kind-stability.

    Returns a short tag describing the shape that ends up in the
    parquet ``out_<name>`` column. Used by
    :func:`validate_observation_kinds` to enforce that the first
    observation of a name pins the kind and subsequent observations
    must match — otherwise the column would carry mixed types.

    URIs (``channel://`` and ``file://``) are tagged ``"uri"`` even
    though they're str at the storage layer — keeps "this column is
    a claim-check ref" distinct from "this column is a free string".
    """
    if is_ref(value):
        return "uri"
    if isinstance(value, bool):
        return "scalar:bool"
    if isinstance(value, int):
        return "scalar:int"
    if isinstance(value, float):
        return "scalar:float"
    if isinstance(value, str):
        return "scalar:str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return f"other:{type(value).__name__}"


def validate_observation_kinds(
    registry: dict[str, str],
    observations: dict[str, Any],
    *,
    where: str,
) -> None:
    """Item 10: register first-observation kinds; raise on mismatch.

    Mutates ``registry`` in place — the materializer threads a
    single registry through every vector in a run. ``where`` is
    included in the error message (e.g.
    ``"vector 3 of test_x"``) for diagnostic clarity.

    Args:
        registry: ``name -> kind`` map. First observation of each
            name registers its kind; subsequent observations must
            match.
        observations: The vector's ``observations`` dict.
        where: Diagnostic prefix used in the error message.

    Raises:
        ValueError: When an observation's kind disagrees with the
            registered kind for its name.
    """
    for name, value in observations.items():
        if name.startswith("_"):
            continue
        kind = observation_kind(value)
        existing = registry.get(name)
        if existing is None:
            registry[name] = kind
        elif existing != kind:
            raise ValueError(
                f"out_{name} kind mismatch at {where}: first observation "
                f"registered '{existing}'; now seeing '{kind}'. "
                "Item 10: out_<name> must be type-stable across vectors."
            )


def build_output_columns(
    vector: TestVector,
    ref_saver: Callable[[str, str, Any], str] | None = None,
) -> dict[str, Any]:
    """Build outputs dict from vector observations.

    Keys are unprefixed (e.g. ``"temperature"``); ``to_flat_dict()`` adds
    the ``out_`` prefix.

    By the time this runs, observations already contain URIs (from
    Context.observe() writing to ChannelStore) or inline scalars.

    Routing:
    - **ref URI** (``channel://``, ``file://``) → pass through as-is
    - **scalar** → inline value
    - **blob** → ``ref_saver()`` → ``file://`` URI, or ``repr()``
    """
    cols: dict[str, Any] = {}

    for key, value in vector.observations.items():
        if key.startswith("_"):
            continue

        # Already a URI (from proxy or context.observe writing to stores)
        if is_ref(value):
            cols[key] = value
            continue

        vtype = classify_value(value)

        if vtype == "scalar":
            cols[key] = value
        elif vtype == "blob" and ref_saver is not None:
            cols[key] = ref_saver(str(vector.id)[:VECTOR_ID_LENGTH], key, value)
        elif isinstance(value, (list, dict)):
            cols[key] = value
        elif ref_saver is not None:
            cols[key] = ref_saver(str(vector.id)[:VECTOR_ID_LENGTH], key, value)
        else:
            cols[key] = repr(value)

    return cols


def save_ref_to_dir(ref_dir: Path, vector_id: str, key: str, value: Any) -> str:
    """Save observation data to a _ref/ directory and return the reference path.

    Both materialization paths use this helper to save out-of-row
    artifacts alongside the parquet (the ``_ref/`` sibling directory
    convention). The dispatch table itself lives in
    :mod:`litmus.data.files.serializers` (build item 12); this
    helper just owns the ``_ref/`` filename / URI shape.

    Args:
        ref_dir: Target directory for reference files.
        vector_id: Vector ID prefix.
        key: Key name for the data.
        value: Data to save. Routed through
            :func:`~litmus.data.files.serializers.find_serializer`
            — see that module for the convention table and the
            ``litmus_serialize`` / :func:`register_serializer`
            extension points.

    Returns:
        Reference string like ``"file://_ref/abc123_waveform.npz"``.
    """
    from litmus.data.files.serializers import find_serializer

    serializer = find_serializer(value)
    # Path values: source suffix wins over the serializer's default
    # ``.bin`` so e.g. ``capture.tdms`` stays ``.tdms`` on disk.
    if isinstance(value, Path):
        ext = value.suffix or serializer.extension
    else:
        ext = serializer.extension
    filename = f"{vector_id}_{key}{ext}"
    serializer.write(value, ref_dir / filename)
    return f"file://{REF_PATH_PREFIX}{filename}"


def build_row(
    test_run: TestRun,
    measurement: Measurement,
    step_name: str,
    step_index: int,
    vector: TestVector,
    instrument_arrays: dict[str, list[str | bool | None]],
    ref_saver: Callable[[str, str, Any], str] | None = None,
    *,
    step_path: str = "",
    step_started_at: datetime | None = None,
    step_ended_at: datetime | None = None,
    step_node_id: str | None = None,
    step_module: str | None = None,
    step_file: str | None = None,
    step_class: str | None = None,
    step_function: str | None = None,
    step_markers: str | None = None,
    step_outcome: str | None = None,
    meta: dict[str, Any] | None = None,
) -> MeasurementRow:
    """Build a complete MeasurementRow from test execution context.

    Args:
        meta: Pre-computed run metadata from ``build_run_metadata()``.
            If ``None``, computed from ``test_run`` (backwards-compatible).
    """
    if meta is None:
        meta = build_run_metadata(test_run)
    meas = build_measurement_fields(measurement)

    return MeasurementRow(
        record_type="measurement",
        **meta,
        **meas,
        # Step/vector context
        step_name=step_name,
        step_index=step_index,
        step_path=step_path,
        step_started_at=step_started_at,
        step_ended_at=step_ended_at,
        step_node_id=step_node_id,
        step_module=step_module,
        step_file=step_file,
        step_class=step_class,
        step_function=step_function,
        step_markers=step_markers,
        vector_index=vector.index,
        vector_retry=vector.retry,
        vector_started_at=vector.started_at,
        vector_ended_at=vector.ended_at,
        # Outcomes (cascade: vector → step → run; all non-Optional with default PASSED)
        step_outcome=step_outcome,
        vector_outcome=vector.outcome.value if vector.outcome else None,
        run_outcome=test_run.outcome.value if test_run.outcome else None,
        # Dynamic columns
        inputs=build_input_columns(vector),
        outputs=build_output_columns(vector, ref_saver=ref_saver),
        instruments=instrument_arrays,
        custom=dict(test_run.custom_metadata),
    )


def iter_rows(test_run: TestRun) -> Iterator[MeasurementRow]:
    """Yield denormalized :class:`MeasurementRow` for each measurement
    in ``test_run``.

    Joins run-level context (DUT, station, operator, etc.) onto each
    measurement, producing the same flat view used by streaming and
    Parquet. Intended for analysis and ad-hoc denormalization (CSV
    export, DataFrames). No ``ref_saver`` is used, so non-serializable
    observation values (Waveform, ndarray, bytes, etc.) fall back to
    ``repr()`` strings in the output columns. For full fidelity, call
    :func:`build_row` directly with a ``ref_saver``.
    """
    for step_index, step in enumerate(test_run.steps):
        for vector in step.vectors:
            for measurement in vector.measurements:
                yield build_row(
                    test_run,
                    measurement,
                    step.name,
                    step_index,
                    vector,
                    step.instrument_arrays or {},
                    # step_path falls back to step.name so the daemon's
                    # GROUP BY (step_path, vector_index) gives each
                    # logical step its own row even when the producer
                    # didn't set an explicit path.
                    step_path=step.step_path or step.name,
                    step_started_at=step.started_at,
                    step_ended_at=step.ended_at,
                    step_node_id=step.node_id,
                    step_module=step.module,
                    step_file=step.file,
                    step_class=step.class_name,
                    step_function=step.function,
                    step_markers=step.markers,
                    step_outcome=step.outcome.value if step.outcome else None,
                )


def build_run_row(
    *,
    run_context: dict[str, Any],
    run_outcome: str | None,
    run_ended_at: datetime | None,
    instruments: dict[str, list],
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the single ``record_type = 'run'`` row for a parquet.

    Carries run-level identity / DUT / station / fixture / environment
    columns plus run-level ``custom_metadata`` (flattened to
    ``custom_*``). Step and measurement columns stay NULL. Provides an
    addressable run-row inside the unified per-run parquet so lakehouse
    adopters can ``WHERE record_type = 'run'`` for clean ingest into a
    ``runs`` table without ``SELECT DISTINCT`` over the denormalized
    step + measurement rows.

    Conventionally written first in the parquet so readers / row-group
    pruners reach the run identity at the start of the file.
    """
    ctx = dict(run_context)
    ctx["run_ended_at"] = run_ended_at
    row = MeasurementRow(
        record_type="run",
        **ctx,
        # Step / vector context: NULL on run rows. ``step_name`` and
        # ``step_index`` are required-non-None on the model so they
        # carry sentinel "" / 0 values.
        step_name="",
        step_index=0,
        step_path="",
        parent_path="",
        step_started_at=None,
        step_ended_at=None,
        step_node_id=None,
        step_module=None,
        step_file=None,
        step_class=None,
        step_function=None,
        step_markers=None,
        step_outcome=None,
        step_vector_count=None,
        vector_index=0,
        vector_retry=None,
        # Measurement payload: NULL on run rows.
        measurement_name=None,
        run_outcome=run_outcome,
        inputs={},
        outputs={},
        instruments=instruments,
        custom=dict(custom or {}),
    )
    return row.to_flat_dict()


def build_step_row(
    *,
    run_context: dict[str, Any],
    entry: dict[str, Any],
    run_outcome: str | None,
    run_ended_at: datetime | None,
    instruments: dict[str, list],
) -> dict[str, Any]:
    """Build one ``record_type = 'step'`` row from a step manifest entry.

    Single source of truth for step-row construction. Used by BOTH the
    streaming subscriber path
    (``materialize_run_to_parquet``) and the batch path
    (``ParquetBackend._append_step_rows``) so the on-disk shape is
    identical regardless of which writer produced it.

    Every ``(step_path, vector_index)`` pair gets a step row — including
    pairs that also have measurement rows. Step rows are independent of
    measurements; queries count steps via
    ``COUNT(*) FILTER (WHERE record_type = 'step')`` instead of
    deduping over measurement rows.

    ``run_context`` is the dict returned by ``build_run_metadata`` or
    ``run_context_from_run_started`` (with ``run_ended_at`` overridden
    by the caller for the streaming case). ``entry`` is one step
    manifest entry as produced by ``step_entry_dict`` /
    ``_append_not_started``.
    """
    ctx = dict(run_context)
    ctx["run_ended_at"] = run_ended_at
    raw_vi = entry.get("vector_index")
    raw_vc = entry.get("vector_count")
    raw_idx = entry.get("index")
    row = MeasurementRow(
        record_type="step",
        **ctx,
        step_name=entry.get("name") or "",
        step_index=int(raw_idx) if raw_idx is not None else 0,
        step_path=entry.get("step_path") or "",
        parent_path=entry.get("parent_path") or "",
        step_started_at=_to_datetime(entry.get("started_at")),
        step_ended_at=_to_datetime(entry.get("ended_at")),
        step_node_id=entry.get("node_id"),
        step_module=entry.get("module"),
        step_file=entry.get("file"),
        step_class=entry.get("class_name"),
        step_function=entry.get("function"),
        step_markers=entry.get("markers"),
        step_outcome=entry.get("outcome"),
        step_vector_count=raw_vc if raw_vc is not None else 1,
        vector_index=raw_vi if raw_vi is not None else 0,
        vector_retry=None,
        measurement_name=None,
        run_outcome=run_outcome,
        inputs=dict(entry.get("inputs") or {}),
        outputs=dict(entry.get("outputs") or {}),
        instruments=instruments,
        custom={},
    )
    return row.to_flat_dict()


def build_step_manifest(test_run: TestRun) -> list[dict[str, Any]]:
    """Build step manifest entries from all (step, vector) pairs in a TestRun.

    Returns one entry per ``(step_index, vector_index)`` execution so
    each sweep variant gets its own entry — matches the streaming path
    (``EventAccumulator._build_step_results_from_events``). Executed
    entries come first; ``_append_not_started`` follows with entries
    for collected items / planned vectors that never ran.
    """
    manifest: list[dict[str, Any]] = []
    executed_node_ids: set[str] = set()
    executed_vectors: set[tuple[str, int]] = set()

    for index, step in enumerate(test_run.steps):
        if step.node_id:
            executed_node_ids.add(step.node_id)
        # Iterate vectors so each (step, vector) pair becomes its own
        # manifest entry — non-swept steps still produce one entry
        # because ``step.vectors`` always contains at least one
        # ``TestVector`` once the step ran.
        vectors = step.vectors or [None]
        # Fall back to the run's timing so step-summary rows always
        # carry ``step_ended_at IS NOT NULL`` — the daemon's runs view
        # filters on that to drop in-flight rows, and in-process batch
        # callers that build a TestRun without populating per-step
        # timing would otherwise be invisible to queries.
        step_started = step.started_at or test_run.started_at
        step_ended = step.ended_at or test_run.ended_at or test_run.started_at
        for vec_offset, vector in enumerate(vectors):
            measurement_count = len(vector.measurements) if vector is not None else 0
            vec_idx = (
                vector.index if vector is not None and vector.index is not None else vec_offset
            )
            inputs = dict(vector.params) if vector is not None else {}
            if step.node_id:
                executed_node_ids.add(step.node_id)
            # ``executed_vectors`` is keyed by (step_path, vector_index) so
            # _append_not_started can match across parametrize variants that
            # share one logical step_path but have distinct node_ids.
            executed_vectors.add((step.step_path or step.name, vec_idx))
            manifest.append(
                step_entry_dict(
                    index=index,
                    name=step.name,
                    node_id=step.node_id,
                    file=step.file,
                    function=step.function,
                    class_name=step.class_name,
                    module=step.module,
                    step_path=step.step_path or step.name,
                    parent_path=step.parent_path or "",
                    description=step.description,
                    markers=step.markers,
                    outcome=step.outcome.value if step.outcome else None,
                    started_at=step_started,
                    ended_at=step_ended,
                    vector_index=vec_idx,
                    inputs=inputs,
                    outputs={},
                    has_measurements=measurement_count > 0,
                    measurement_count=measurement_count,
                    vector_count=len(step.vectors) if step.vectors else 1,
                )
            )

    # Append not-started entries for collected items that never executed,
    # plus unrun-vector entries for partially-run sweeps.
    _append_not_started(
        manifest,
        [ci.model_dump() for ci in test_run.collected_items],
        executed_node_ids,
        executed_vectors=executed_vectors,
    )

    return manifest


def step_entry_dict(
    *,
    index: int,
    name: str,
    node_id: str | None,
    file: str | None,
    function: str | None,
    class_name: str | None,
    module: str | None,
    step_path: str,
    parent_path: str = "",
    description: str | None,
    markers: str | None,
    outcome: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    vector_index: int = 0,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    has_measurements: bool,
    measurement_count: int,
    vector_count: int,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Single source of truth for one step manifest entry's shape.

    Shared by the batch path (:func:`build_step_manifest`) and the
    streaming path (``the accumulator-to-parquet path``); both
    pre-compute their values and pass them as kwargs. Timestamps are
    serialised here, ``duration_s`` derived from start/end.

    ``parent_path`` mirrors the same field on ``StepStarted`` /
    ``StepEnded`` so step-summary rows in the unified parquet preserve
    the hierarchy. ``vector_index`` / ``inputs`` / ``outputs`` carry the
    per-vector identity so each (step_path, vector_index) is its own
    entry — a sweep of N variants produces N entries with the same
    step_path and vector_index 0..N-1.
    """
    duration_s: float | None = None
    if started_at and ended_at:
        duration_s = (ended_at - started_at).total_seconds()
    return {
        "index": index,
        "name": name,
        "node_id": node_id,
        "file": file,
        "function": function,
        "class_name": class_name,
        "module": module,
        "step_path": step_path,
        "parent_path": parent_path,
        "description": description,
        "markers": markers,
        "outcome": outcome,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_s": duration_s,
        "vector_index": vector_index,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "has_measurements": has_measurements,
        "measurement_count": measurement_count,
        "vector_count": vector_count,
        "retry_count": retry_count,
    }


def _append_not_started(
    manifest: list[dict[str, Any]],
    collected_items: list[dict[str, str | int | None]],
    executed_node_ids: set[str],
    *,
    executed_vectors: set[tuple[str, int]] | None = None,
) -> None:
    """Append ``planned`` entries for collected items that never executed.

    Shared by both the batch path (``build_step_manifest``) and the
    streaming path (``the accumulator-to-parquet path``).

    Each collected item maps to ONE execution at its own
    ``(step_path, vector_index)``.  We add a "not-started" entry iff
    that specific pair did not appear in the executed events.

    ``executed_vectors`` is keyed by ``(step_path, vector_index)`` —
    matching the accumulator's keying — so the check is unambiguous
    even when multiple pytest items (parametrize variants) share one
    logical step.
    """
    next_index = len(manifest)
    for ci in collected_items:
        node_id = ci.get("node_id") or ""
        step_path = ci.get("step_path") or ""
        raw_vi = ci.get("vector_index") or 0
        vi = raw_vi if isinstance(raw_vi, int) else 0
        if executed_vectors is not None and (step_path, vi) in executed_vectors:
            # This exact (step_path, vector_index) ran; nothing to fill in.
            continue
        if node_id in executed_node_ids and executed_vectors is None:
            # Legacy path (no per-vector info): node_id ran, so nothing to do.
            continue
        manifest.append(
            {
                "index": next_index,
                "name": ci.get("function") or node_id,
                "node_id": node_id,
                "file": ci.get("file"),
                "function": ci.get("function"),
                "class_name": ci.get("class_name"),
                "module": ci.get("module"),
                "step_path": step_path,
                "parent_path": ci.get("parent_path") or "",
                "description": None,
                # No outcome stamped — the absence IS the receipt
                # that this step never ran (the row was collected
                # but its turn never came). Display layer renders
                # "Never Ran" for outcome=None at finalize time.
                "outcome": None,
                "started_at": None,
                "ended_at": None,
                "vector_index": vi,
                "inputs": {},
                "outputs": {},
                "has_measurements": False,
                "measurement_count": 0,
                "vector_count": 0,
            }
        )
        next_index += 1
