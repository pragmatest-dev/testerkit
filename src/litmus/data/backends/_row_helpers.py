"""Shared row-building helpers for the parquet backend.

Produces denormalized rows with run-level and measurement-level fields.
This module extracts the common logic so new columns only need to be
added in one place.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from litmus.data.models import Measurement, TestRun, TestVector
from litmus.data.ref import classify_value, is_ref
from litmus.environment import EnvironmentSnapshot

try:
    import importlib.util as _ilu

    HAS_NUMPY = _ilu.find_spec("numpy") is not None
except (ImportError, ValueError):
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

# EAV lane struct — the at-rest nested representation of one input / output
# entry. ``value_type`` is the value-type discriminator that selects which
# ``value_*`` lane holds the value. The Arrow struct type in
# ``schemas._LANE_STRUCT`` must match these names (guarded there).
LANE_FIELDS: tuple[str, ...] = (
    "name",
    "value_type",
    "value_int",
    "value_double",
    "value_bool",
    "value_text",
    "value_timestamp",
    "value_json",
    "unit",
    "uut_pin",
)

# Fields of the at-rest nested measurement struct carried on the vector row.
MEASUREMENT_STRUCT_FIELDS: tuple[str, ...] = (
    "name",
    "value",
    "unit",
    "outcome",
    "timestamp",
    "limit_low",
    "limit_high",
    "limit_nominal",
    "limit_comparator",
    "characteristic_id",
    "spec_ref",
    "uut_pin",
    "fixture_connection",
    "instrument_name",
    "instrument_resource",
    "instrument_channel",
)

# Flat measurement scalar columns — present on the flat-fact / overlay / export
# row, but dropped from the at-rest parquet (at rest a measurement lives in the
# vector row's nested ``measurements`` list, not as flat columns).
_MEASUREMENT_SCALAR_FIELDS: frozenset[str] = frozenset(
    {
        "measurement_name",
        "measurement_timestamp",
        "measurement_value",
        "measurement_unit",
        "measurement_outcome",
        "limit_low",
        "limit_high",
        "limit_nominal",
        "limit_comparator",
        "characteristic_id",
        "spec_ref",
        "uut_pin",
        "fixture_connection",
        "instrument_name",
        "instrument_resource",
        "instrument_channel",
    }
)


def _decode_dynamic_attrs_map(
    dynamic_attrs: dict | list | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a ``dynamic_attrs`` MAP into ``(inputs, outputs)`` dicts.

    Shared by :meth:`RunStore.get_measurements` and
    :meth:`StepsQuery.list_for_run`. Both call sites receive the MAP
    as either a plain ``dict`` (DuckDB Arrow conversion) or a list of
    ``(key, value)`` tuples. Keys prefixed ``in_<name>`` go to
    ``inputs`` (prefix stripped); ``out_<name>`` go to ``outputs``.
    ``None`` keys or values are skipped. VARCHAR values are coerced:
    ``"true"``/``"false"`` → ``bool``; numeric strings → ``float``.
    """
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    if not dynamic_attrs:
        return inputs, outputs
    pairs = dynamic_attrs.items() if isinstance(dynamic_attrs, dict) else dynamic_attrs
    for k, v in pairs:
        if k is None or v is None:
            continue
        v = _coerce_dynamic_attr(v)
        if k.startswith("in_"):
            inputs[k[3:]] = v
        elif k.startswith("out_"):
            outputs[k[4:]] = v
    return inputs, outputs


def _coerce_dynamic_attr(v: Any) -> Any:
    """Coerce a dynamic_attrs VARCHAR value to its native Python type."""
    if not isinstance(v, str):
        return v
    if v == "true":
        return True
    if v == "false":
        return False
    try:
        return float(v)
    except ValueError:
        return v


def _as_utc(value: datetime) -> datetime:
    """Normalise a datetime to tz-aware UTC (assume UTC if naive)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lane_entry(
    name: str, value: Any, unit: str | None = None, uut_pin: str | None = None
) -> dict[str, Any]:
    """Encode one ``(name, value)`` into an EAV lane struct dict.

    ``observation_kind`` routes the value to exactly one ``value_*`` lane;
    the others stay ``None``. ``unit`` carries the optional engineering unit;
    ``uut_pin`` carries the pin this observation belongs to (or None = all pins).
    """
    value_type = observation_kind(value)
    entry: dict[str, Any] = dict.fromkeys(LANE_FIELDS)
    entry["name"] = name
    entry["value_type"] = value_type
    entry["unit"] = unit
    entry["uut_pin"] = uut_pin
    if value_type == "scalar:bool":
        entry["value_bool"] = bool(value)
    elif value_type == "scalar:int":
        entry["value_int"] = int(value)
    elif value_type == "scalar:float":
        entry["value_double"] = float(value)
    elif value_type == "scalar:datetime":
        entry["value_timestamp"] = _as_utc(value)
    elif value_type in ("scalar:str", "uri"):
        entry["value_text"] = str(value)
    elif value_type in ("list", "dict"):
        entry["value_json"] = json.dumps(value, default=str)
    else:  # other:*
        entry["value_text"] = repr(value)
    return entry


def encode_lane_structs(
    values: dict[str, Any],
    units: dict[str, str] | None = None,
    pins: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Encode an inputs or outputs dict into a list of lane structs.

    ``units`` maps a slot name to its engineering unit; ``pins`` maps a slot
    name to its ``uut_pin``. Both ride into the lane's named fields.
    """
    units = units or {}
    pins = pins or {}
    return [
        _lane_entry(name, value, units.get(name), pins.get(name)) for name, value in values.items()
    ]


def _lane_value(entry: dict[str, Any]) -> Any:
    """Inverse of :func:`_lane_entry` — read the value from its lane by ``value_type``."""
    value_type = entry.get("value_type")
    if value_type == "scalar:bool":
        return entry.get("value_bool")
    if value_type == "scalar:int":
        return entry.get("value_int")
    if value_type == "scalar:float":
        return entry.get("value_double")
    if value_type == "scalar:datetime":
        return entry.get("value_timestamp")
    if value_type in ("list", "dict"):
        raw = entry.get("value_json")
        return json.loads(raw) if raw is not None else None
    return entry.get("value_text")  # scalar:str, uri, other:*


def decode_lane_structs(entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Decode a list of lane structs back into a ``{name: value}`` dict."""
    return {entry["name"]: _lane_value(entry) for entry in (entries or [])}


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
      identity / UUT / station / fixture / environment context. Step
      and vector columns are NULL. Provides an addressable
      "runs table" within the unified per-run parquet (lakehouse
      adopters can ``WHERE record_type = 'run'`` for clean ingest).
    * ``record_type = 'step'`` — one per ``(step_path, vector_index)``
      execution; carries code identity + timing + rolled-up outcome.
    * ``record_type = 'vector'`` — one execution carrier; holds the
      ``inputs``/``outputs`` lanes and the nested ``measurements``
      list for that execution.

    Run rows are keyed by ``run_id``; steps and vectors share grain
    ``(run_id, step_path, vector_index)``.
    """

    model_config = ConfigDict(extra="forbid")

    # Discriminator
    record_type: Literal["run", "step", "vector"]

    # Session / run identity
    session_id: str
    run_id: str
    slot_id: str | None = None
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None

    # Operator
    operator_id: str | None = None
    operator_name: str | None = None

    # UUT
    uut_serial_number: str
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None

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
    # 0-based outer (item) retry — pytest-rerunfailures rerun count of this
    # step. On step + scope-vector rows; NULL on run/measurement rows. The
    # inner per-vector retry is ``vector_retry``.
    step_retry: int | None = None
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
    measurement_unit: str | None = None
    measurement_outcome: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None
    uut_pin: str | None = None
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
    # Optional per-slot engineering unit for inputs / outputs (name → unit),
    # flowed into the lane's ``unit`` field at encode time.
    input_units: dict[str, str] = Field(default_factory=dict)
    output_units: dict[str, str] = Field(default_factory=dict)
    output_pins: dict[str, str] = Field(default_factory=dict)
    # Nested measurements carried on the vector row (LIST<STRUCT>).
    measurements: list[dict[str, Any]] = Field(default_factory=list)

    def to_flat_dict(self, *, at_rest: bool = False) -> dict[str, Any]:
        """Flatten to denormalized dict for the Parquet write boundary.

        ``inputs`` / ``outputs`` are encoded as nested EAV lane structs
        (``LIST<STRUCT>``; see :func:`encode_lane_structs`) under the
        ``inputs`` / ``outputs`` keys. ``input_units`` / ``output_units``
        ride into each lane's ``unit`` field. ``instruments`` keys pass
        through (already ``step_instruments_``-prefixed).

        ``at_rest=True`` drops the flat measurement scalar columns: at rest a
        measurement lives in the vector row's nested ``measurements`` list, not
        as flat columns. The flat-fact path (overlay / export) keeps them.

        Datetime values are left as ``datetime`` objects — callers must
        serialise them at the actual write boundary (e.g. ``.isoformat()``).
        """
        exclude = {
            "inputs",
            "outputs",
            "instruments",
            "input_units",
            "output_units",
            "output_pins",
        }
        if at_rest:
            exclude |= _MEASUREMENT_SCALAR_FIELDS
        row = self.model_dump(exclude=exclude)
        row["inputs"] = encode_lane_structs(self.inputs, self.input_units)
        row["outputs"] = encode_lane_structs(self.outputs, self.output_units, self.output_pins)
        row.update(self.instruments)
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
        # UUT
        "uut_serial_number": test_run.uut.serial,
        "uut_part_number": test_run.uut.part_number,
        "uut_revision": test_run.uut.revision,
        "uut_lot_number": test_run.uut.lot_number,
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
    operates on a ``TestRun`` model).

    ``event`` supplies the row's ``run_id`` (a measurement event may carry
    it before ``RunStarted`` arrives). When ``run_started`` is ``None``
    (events arrived before RunStarted), falls back to a sparse dict with
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
            "uut_serial_number": "unknown",
            "uut_part_number": None,
            "uut_revision": None,
            "uut_lot_number": None,
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
            "uut_serial_number": run_started.uut_serial_number,
            "uut_part_number": run_started.uut_part_number,
            "uut_revision": run_started.uut_revision,
            "uut_lot_number": run_started.uut_lot_number,
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
        "measurement_unit": measurement.unit,
        # measurement.outcome is contractually set by log_measurement
        # (RuntimeError raised in execution/run_scope.py if None reaches here).
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
        "uut_pin": measurement.uut_pin,
        "fixture_connection": measurement.fixture_connection,
        "instrument_name": measurement.instrument_name,
        "instrument_resource": measurement.instrument_resource,
        "instrument_channel": measurement.instrument_channel,
    }


def build_measurement_struct(measurement: Measurement) -> dict[str, Any]:
    """Encode a Measurement into the at-rest nested struct on the vector row.

    Field order/names must match ``MEASUREMENT_STRUCT_FIELDS`` (and
    ``schemas._MEASUREMENT_STRUCT``, guarded there).
    """
    return {
        "name": measurement.name,
        "value": measurement.value,
        "unit": measurement.unit,
        "outcome": measurement.outcome.value if measurement.outcome else None,
        "timestamp": measurement.timestamp,
        "limit_low": measurement.limit_low,
        "limit_high": measurement.limit_high,
        "limit_nominal": measurement.limit_nominal,
        "limit_comparator": measurement.limit_comparator,
        "characteristic_id": measurement.characteristic_id,
        "spec_ref": measurement.spec_ref,
        "uut_pin": measurement.uut_pin,
        "fixture_connection": measurement.fixture_connection,
        "instrument_name": measurement.instrument_name,
        "instrument_resource": measurement.instrument_resource,
        "instrument_channel": measurement.instrument_channel,
    }


def build_input_columns(vector: TestVector) -> dict[str, Any]:
    """Build inputs dict from vector params and stimulus records.

    Keys are unprefixed (e.g. ``"vin"``); the result is encoded as the
    ``inputs`` lane struct list by ``to_flat_dict()``.
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
        if stim.uut_pin:
            cols[f"{param}_uut_pin"] = stim.uut_pin
        if stim.fixture_connection:
            cols[f"{param}_fixture_connection"] = stim.fixture_connection

    return cols


def observation_kind(value: Any) -> str:
    """Classify a value into its EAV value-type tag.

    Returns a short tag (``scalar:int`` / ``scalar:float`` / ``scalar:bool`` /
    ``scalar:str`` / ``scalar:datetime`` / ``uri`` / ``list`` / ``dict`` /
    ``other:*``) that :func:`_lane_entry` uses to route the value to its
    ``value_*`` lane and that is stored as the ``value_type`` field.

    URIs (``channel://`` and ``file://``) are tagged ``"uri"`` even
    though they're ``str`` — keeps a claim-check ref distinct from a free
    string (both share the ``value_text`` lane, disambiguated by ``value_type``).
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
    if isinstance(value, datetime):
        return "scalar:datetime"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return f"other:{type(value).__name__}"


def build_output_columns(
    vector: TestVector,
    ref_saver: Callable[[str, str, Any], str] | None = None,
) -> dict[str, Any]:
    """Build outputs dict from vector observations.

    Keys are unprefixed (e.g. ``"temperature"``); the result is encoded as
    the ``outputs`` lane struct list by ``to_flat_dict()``.

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
    denormalize_conditions: bool = True,
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
        record_type="vector",
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
        # v2: at rest a measurement references (does not copy) its vector —
        # the in/out conditions live on the vector record and the EAV join
        # resolves them by the shared vector key (the at-rest path passes
        # denormalize_conditions=False). Standalone/export callers
        # (iter_rows → CSV/DataFrame) denormalize the vector's conditions
        # back onto the flat row. custom_metadata is a run-level fact.
        inputs=build_input_columns(vector) if denormalize_conditions else {},
        outputs=(
            build_output_columns(vector, ref_saver=ref_saver) if denormalize_conditions else {}
        ),
        instruments=instrument_arrays,
    )


def iter_rows(test_run: TestRun) -> Iterator[dict[str, Any]]:
    """Yield one denormalized flat row dict per measurement in ``test_run``.

    Joins run-level context (UUT, station, operator, etc.) onto each
    measurement and denormalizes the enclosing vector's conditions back
    onto the flat row — the analysis/export view (CSV, DataFrames).
    No ``ref_saver`` is used, so non-serializable observation values
    (Waveform, ndarray, bytes, etc.) fall back to ``repr()`` strings in
    the output columns. The flat row is stamped ``record_type='measurement'``.
    """
    for step_index, step in enumerate(test_run.steps):
        for vector in step.vectors:
            for measurement in vector.measurements:
                row = build_row(
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
                ).to_flat_dict()
                row["record_type"] = "measurement"
                row.pop("measurements", None)
                yield row


def build_run_row(
    *,
    run_context: dict[str, Any],
    run_outcome: str | None,
    run_ended_at: datetime | None,
    instruments: dict[str, list],
) -> dict[str, Any]:
    """Build the single ``record_type = 'run'`` row for a parquet.

    Carries run-level identity / UUT / station / fixture / environment
    columns. Step and measurement columns stay NULL. Provides an
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
    )
    return row.to_flat_dict(at_rest=True)


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

    The step record carries ONLY code identity + timing + rolled-up
    outcome (v2). Conditions/observations live on the synthesized scope
    ``vector`` row (:func:`build_scope_vector_row`), so the step row's
    ``inputs`` / ``outputs`` lanes are empty.

    ``run_context`` is the dict returned by ``build_run_metadata`` or
    ``run_context_from_run_started`` (with ``run_ended_at`` overridden
    by the caller for the streaming case). ``entry`` is one step
    manifest entry as produced by ``step_entry_dict`` /
    ``_append_not_started``.
    """
    ctx = dict(run_context)
    ctx["run_ended_at"] = run_ended_at
    raw_vi = entry.get("vector_index")
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
        step_vector_count=None,
        step_retry=entry.get("step_retry") or 0,
        vector_index=raw_vi if raw_vi is not None else 0,
        vector_retry=None,
        measurement_name=None,
        run_outcome=run_outcome,
        # v2: the step sheds inputs/outputs onto its scope vector.
        inputs={},
        outputs={},
        instruments=instruments,
    )
    return row.to_flat_dict(at_rest=True)


def build_scope_vector_row(
    *,
    run_context: dict[str, Any],
    entry: dict[str, Any],
    run_outcome: str | None,
    run_ended_at: datetime | None,
    instruments: dict[str, list],
) -> dict[str, Any]:
    """Synthesize one scope ``vector`` row from a step manifest entry (v2).

    Every step execution that has NO in-body iteration vectors (Mode 1,
    parametrize item, class container, single, measurement-less) gets one
    uniform scope vector — derived here by the materializer — so the
    at-rest parquet is uniform: a ``vector`` row per execution. It carries
    the step's conditions (``inputs``) and observations (``outputs``) that
    the step record sheds, keyed ``(step_path, vector_index, retry=0)`` to
    match the step and its measurements, so the EAV vector-key join is
    unchanged.

    Mode-2 in-body loops emit their own ``vector`` rows
    (:func:`build_vector_row`) at the iteration grain; for those steps no
    scope vector is synthesized (the iteration vectors are the carriers).
    """
    ctx = dict(run_context)
    ctx["run_ended_at"] = run_ended_at
    raw_vi = entry.get("vector_index")
    raw_idx = entry.get("index")
    row = MeasurementRow(
        record_type="vector",
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
        step_outcome=None,
        step_vector_count=None,
        step_retry=entry.get("step_retry") or 0,
        vector_index=raw_vi if raw_vi is not None else 0,
        # Scope vector runs once per step execution → its (step_path,
        # vector_index) occurrence ordinal equals the step's attempt count.
        vector_retry=entry.get("step_retry") or 0,
        vector_started_at=_to_datetime(entry.get("started_at")),
        vector_ended_at=_to_datetime(entry.get("ended_at")),
        vector_outcome=entry.get("outcome"),
        measurement_name=None,
        run_outcome=run_outcome,
        inputs=dict(entry.get("inputs") or {}),
        outputs=dict(entry.get("outputs") or {}),
        input_units=dict(entry.get("input_units") or {}),
        output_units=dict(entry.get("output_units") or {}),
        output_pins=dict(entry.get("output_pins") or {}),
        measurements=entry.get("measurements") or [],
        instruments=instruments,
    )
    return row.to_flat_dict(at_rest=True)


def build_vector_row(
    *,
    run_context: dict[str, Any],
    entry: dict[str, Any],
    run_outcome: str | None,
    run_ended_at: datetime | None,
    instruments: dict[str, list],
) -> dict[str, Any]:
    """Build one ``record_type = 'vector'`` row from a vector manifest entry.

    A vector row appears ONLY for Mode-2 in-body iterations (the ``vectors``
    fixture / ``run_vector`` loop) — Mode 1 and class containers fuse into
    the ``step`` record. It carries the in-body iteration's own
    ``(step_path, parent_path, vector_index, retry)`` identity, its
    ``inputs`` (this iteration's conditions), ``outputs``, and
    ``vector_outcome``. ``measurement_*`` columns are NULL.

    ``entry`` is one vector manifest entry as produced by
    ``vector_entry_dict``. Mirrors :func:`build_step_row` so both record
    kinds share the same write boundary.
    """
    ctx = dict(run_context)
    ctx["run_ended_at"] = run_ended_at
    raw_vi = entry.get("vector_index")
    raw_retry = entry.get("retry")
    raw_idx = entry.get("index")
    row = MeasurementRow(
        record_type="vector",
        **ctx,
        step_name=entry.get("name") or "",
        step_index=int(raw_idx) if raw_idx is not None else 0,
        step_path=entry.get("step_path") or "",
        parent_path=entry.get("parent_path") or "",
        step_started_at=_to_datetime(entry.get("step_started_at")),
        step_ended_at=_to_datetime(entry.get("step_ended_at")),
        step_node_id=entry.get("node_id"),
        step_module=entry.get("module"),
        step_file=entry.get("file"),
        step_class=entry.get("class_name"),
        step_function=entry.get("function"),
        step_markers=entry.get("markers"),
        step_outcome=None,
        step_vector_count=None,
        step_retry=entry.get("step_retry") or 0,
        vector_index=raw_vi if raw_vi is not None else 0,
        vector_retry=raw_retry if raw_retry is not None else 0,
        vector_started_at=_to_datetime(entry.get("started_at")),
        vector_ended_at=_to_datetime(entry.get("ended_at")),
        vector_outcome=entry.get("outcome"),
        measurement_name=None,
        run_outcome=run_outcome,
        inputs=dict(entry.get("inputs") or {}),
        outputs=dict(entry.get("outputs") or {}),
        input_units=dict(entry.get("input_units") or {}),
        output_units=dict(entry.get("output_units") or {}),
        output_pins=dict(entry.get("output_pins") or {}),
        measurements=entry.get("measurements") or [],
        instruments=instruments,
    )
    return row.to_flat_dict(at_rest=True)


def vector_entry_dict(
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
    markers: str | None,
    step_started_at: datetime | None,
    step_ended_at: datetime | None,
    vector_index: int,
    retry: int,
    step_retry: int = 0,
    outcome: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    input_units: dict[str, str] | None = None,
    output_units: dict[str, str] | None = None,
    output_pins: dict[str, str] | None = None,
    measurements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Single source of truth for one in-body vector manifest entry's shape.

    Distinct from :func:`step_entry_dict` — a vector entry keys on
    ``(step_path, vector_index, retry)`` and carries vector-grain timing
    and outcome. ``step_retry`` is the enclosing step's outer (item) attempt.
    Timestamps are serialised here.
    """
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
        "markers": markers,
        "step_started_at": step_started_at.isoformat() if step_started_at else None,
        "step_ended_at": step_ended_at.isoformat() if step_ended_at else None,
        "vector_index": vector_index,
        "retry": retry,
        "step_retry": step_retry,
        "outcome": outcome,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "input_units": input_units or {},
        "output_units": output_units or {},
        "output_pins": output_pins or {},
        "measurements": measurements or [],
    }


def build_step_manifest(
    test_run: TestRun,
    ref_saver: Callable[[str, str, Any], str] | None = None,
) -> list[dict[str, Any]]:
    """Build step manifest entries from all (step, vector) pairs in a TestRun.

    Returns one entry per ``(step_index, vector_index)`` execution so
    each sweep variant gets its own entry — matches the streaming path
    (``EventAccumulator._build_step_results_from_events``). Executed
    entries come first; ``_append_not_started`` follows with entries
    for collected items / planned vectors that never ran.

    ``ref_saver`` claim-checks blob observations through the same saver
    the measurement path uses (``build_output_columns``) so a raw blob
    never reaches the step-record lane structs or the ``step_results``
    JSON metadata. Without it, blobs fall back to ``repr()``.
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
            # Vector observations ride on the step record's outputs lanes
            # (Mode-1 fused) — same as the streaming path merges
            # StepEnded.outputs / observations into its step entry. Routed
            # through ``build_output_columns`` (same as the measurement
            # path) so blobs are claim-checked via ``ref_saver`` rather
            # than reaching the lane structs / step_results JSON raw.
            outputs = (
                build_output_columns(vector, ref_saver=ref_saver) if vector is not None else {}
            )
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
                    outputs=outputs,
                    input_units=dict(vector.param_units) if vector is not None else {},
                    output_units=dict(vector.observation_units) if vector is not None else {},
                    output_pins=dict(vector.observation_pins) if vector is not None else {},
                    measurements=[
                        build_measurement_struct(m)
                        for m in (vector.measurements if vector is not None else [])
                    ],
                    measurement_count=measurement_count,
                    step_retry=step.retry,
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
    input_units: dict[str, str] | None = None,
    output_units: dict[str, str] | None = None,
    output_pins: dict[str, str] | None = None,
    measurements: list[dict[str, Any]] | None = None,
    measurement_count: int,
    step_retry: int = 0,
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
        "input_units": input_units or {},
        "output_units": output_units or {},
        "output_pins": output_pins or {},
        "measurements": measurements or [],
        "measurement_count": measurement_count,
        "step_retry": step_retry,
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
                "measurement_count": 0,
                "step_retry": 0,
            }
        )
        next_index += 1
