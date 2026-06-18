"""Canonical Arrow schema contract shared across all backends.

Defines the measurement schema, type inference for dynamic columns,
and helpers for constructing validated Arrow tables. Any backend
(Parquet, Flight, NATS) imports from here rather than defining its own
schema.
"""

from datetime import datetime
from typing import Any

import pyarrow as pa

from litmus.data.backends._row_helpers import INSTRUMENT_ARRAY_KEYS, LANE_FIELDS

__all__ = [
    "RUN_ROW_SCHEMA",
    "SCHEMA_VERSION",
    "_INSTR_ARRAY_TYPES",
    "_LANE_LIST",
    "_SCHEMA_DICT",
    "_build_write_schema",
    "table_from_rows",
]

SCHEMA_VERSION = "1.0"

# EAV lane struct — the nested at-rest representation of one input / output /
# custom entry. ``kind`` selects which ``value_*`` lane holds the value. Field
# names must match ``_row_helpers.LANE_FIELDS`` / the encoder (guarded below).
_LANE_STRUCT = pa.struct(
    [
        ("name", pa.string()),
        ("kind", pa.string()),
        ("value_int", pa.int64()),
        ("value_double", pa.float64()),
        ("value_bool", pa.bool_()),
        ("value_text", pa.string()),
        ("value_timestamp", pa.timestamp("us", tz="UTC")),
        ("value_json", pa.string()),
        ("unit", pa.string()),
    ]
)
assert [f.name for f in _LANE_STRUCT] == list(LANE_FIELDS), (
    "schemas._LANE_STRUCT drifted from _row_helpers.LANE_FIELDS"
)
_LANE_LIST = pa.list_(_LANE_STRUCT)

# Canonical row schema for the unified per-run parquet. Every row carries
# an explicit ``record_type`` discriminator with one of two values:
#   * ``record_type = 'step'`` — one row per ``(step_path, vector_index)``
#     execution (or planned-but-unrun vector). Carries step identity,
#     timing, outcome, and dynamic ``in_*``/``out_*`` columns.
#     ``measurement_*`` columns are NULL.
#   * ``record_type = 'measurement'`` — one row per recorded measurement.
#     Carries the measurement payload plus the same denormalized step +
#     run + UUT + station + fixture context as the corresponding step
#     row, so cross-run measurement queries don't need self-joins.
#
# Both kinds share grain ``(run_id, step_path, vector_index)``; measurement
# rows are further keyed by ``measurement_name``. A step that records N
# measurements emits 1 step row + N measurement rows.
#
# Dynamic columns (in_*, out_*, step_instruments_*, custom_*) are NOT
# listed here — they pass through with inferred types via
# ``_build_write_schema``.
RUN_ROW_SCHEMA = pa.schema(
    [
        # Discriminator — 'step' or 'measurement'
        ("record_type", pa.string()),
        # Identity & timing
        ("session_id", pa.string()),
        ("run_id", pa.string()),
        ("slot_id", pa.string()),
        ("run_started_at", pa.timestamp("us", tz="UTC")),
        ("run_ended_at", pa.timestamp("us", tz="UTC")),
        ("step_name", pa.string()),
        ("step_index", pa.int64()),
        ("step_path", pa.string()),
        # parent_path: container's path (empty string for root steps).
        # Enables hierarchical reconstruction (e.g. TestPowerSequence →
        # test_efficiency) without joining other event types.
        ("parent_path", pa.string()),
        ("step_started_at", pa.timestamp("us", tz="UTC")),
        ("step_ended_at", pa.timestamp("us", tz="UTC")),
        ("step_node_id", pa.string()),
        ("step_module", pa.string()),
        ("step_file", pa.string()),
        ("step_class", pa.string()),
        ("step_function", pa.string()),
        ("step_markers", pa.string()),
        # step_vector_count: total planned vectors for this step (1 for
        # non-swept; N for sweep with N variants). Stored as a hint so
        # consumers can detect partial-runs without joining the manifest.
        ("step_vector_count", pa.int32()),
        ("vector_index", pa.int64()),
        ("vector_retry", pa.int64()),
        ("vector_started_at", pa.timestamp("us", tz="UTC")),
        ("vector_ended_at", pa.timestamp("us", tz="UTC")),
        # Who
        ("operator_id", pa.string()),
        ("operator_name", pa.string()),
        # UUT
        ("uut_serial", pa.string()),
        ("uut_part_number", pa.string()),
        ("uut_revision", pa.string()),
        ("uut_lot_number", pa.string()),
        # Part
        ("part_id", pa.string()),
        ("part_name", pa.string()),
        ("part_revision", pa.string()),
        # Station
        ("station_id", pa.string()),
        ("station_name", pa.string()),
        ("station_type", pa.string()),
        ("station_location", pa.string()),
        ("station_hostname", pa.string()),
        # Fixture
        ("fixture_id", pa.string()),
        # Test context
        ("test_phase", pa.string()),
        ("project_name", pa.string()),
        ("git_commit", pa.string()),
        ("git_branch", pa.string()),
        ("git_remote", pa.string()),
        # Measurement core
        ("measurement_name", pa.string()),
        ("measurement_timestamp", pa.timestamp("us", tz="UTC")),
        ("measurement_value", pa.float64()),
        ("measurement_units", pa.string()),
        ("measurement_outcome", pa.string()),
        # Limits
        ("limit_low", pa.float64()),
        ("limit_high", pa.float64()),
        ("limit_nominal", pa.float64()),
        ("limit_comparator", pa.string()),
        # Spec traceability
        ("characteristic_id", pa.string()),
        ("spec_ref", pa.string()),
        # Signal path
        ("uut_pin", pa.string()),
        ("fixture_connection", pa.string()),
        ("instrument_name", pa.string()),
        ("instrument_resource", pa.string()),
        ("instrument_channel", pa.string()),
        # Rollup
        ("step_outcome", pa.string()),
        ("vector_outcome", pa.string()),
        ("run_outcome", pa.string()),
        # Environment traceability
        ("python_version", pa.string()),
        ("litmus_version", pa.string()),
        ("env_fingerprint", pa.string()),
        # Dynamic measurement attributes — nested EAV lanes (see _row_helpers).
        # Names are values inside the structs, so there is no column explosion.
        ("inputs", _LANE_LIST),
        ("outputs", _LANE_LIST),
        ("custom", _LANE_LIST),
    ]
)

_SCHEMA_DICT = {f.name: f.type for f in RUN_ROW_SCHEMA}

# Instrument array columns have known list types
_INSTR_ARRAY_TYPES: dict[str, pa.DataType] = {
    k: pa.list_(pa.bool_()) if k == "step_instruments_mocked" else pa.list_(pa.string())
    for k in INSTRUMENT_ARRAY_KEYS
}


def _infer_type_from_value(value: Any) -> pa.DataType:
    """Infer Arrow type from a Python value. None → string (safe default)."""
    if value is None:
        return pa.string()
    if isinstance(value, bool):
        return pa.bool_()
    if isinstance(value, (int, float)):
        return pa.float64()
    if isinstance(value, datetime):
        return pa.timestamp("us", tz="UTC")
    if isinstance(value, list):
        return pa.list_(pa.string())
    return pa.string()


def _build_write_schema(rows: list[dict[str, Any]]) -> pa.Schema:
    """Build complete Arrow schema: fixed canonical + instrument arrays.

    Fixed columns (including the nested ``inputs``/``outputs``/``custom``
    lanes) use RUN_ROW_SCHEMA types. Instrument arrays use known list types.
    Any other stray column is inferred from its first non-None value. Passed
    to ``pa.Table.from_pylist()`` so Arrow validates at construction time.

    Single pass over rows to collect keys and first non-None values.
    """
    first_values: dict[str, Any] = {}
    all_keys: set[str] = set()
    for row in rows:
        for k, v in row.items():
            all_keys.add(k)
            if k not in first_values and v is not None:
                first_values[k] = v

    fields: list[pa.Field] = []
    used: set[str] = set()

    # Fixed columns first, in canonical order
    for field in RUN_ROW_SCHEMA:
        if field.name in all_keys:
            fields.append(field)
            used.add(field.name)

    # Remaining columns sorted: instrument arrays, then dynamic
    for key in sorted(all_keys - used):
        if key in _INSTR_ARRAY_TYPES:
            fields.append(pa.field(key, _INSTR_ARRAY_TYPES[key]))
        else:
            fields.append(pa.field(key, _infer_type_from_value(first_values.get(key))))

    return pa.schema(fields)


def table_from_rows(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Build a PyArrow Table from row dicts against ``schema``.

    Wraps ``pa.Table.from_pylist`` so a build failure carries row context.
    """
    try:
        return pa.Table.from_pylist(rows, schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise pa.ArrowInvalid(f"Cannot build measurement table from rows: {exc}") from exc
