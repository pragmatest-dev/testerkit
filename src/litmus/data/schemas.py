"""Canonical Arrow schema contract shared across all backends.

Defines the measurement schema, type inference for dynamic columns,
and helpers for constructing validated Arrow tables. Any backend
(Parquet, Flight, NATS) imports from here rather than defining its own
schema.
"""

from datetime import datetime
from typing import Any

import pyarrow as pa

from litmus.data.backends._row_helpers import (
    INSTRUMENT_STRUCT_FIELDS,
    LANE_FIELDS,
    MEASUREMENT_STRUCT_FIELDS,
)
from litmus.data.schema_versions import CURRENT_SCHEMA_VERSION, SchemaStore

__all__ = [
    "RUN_ROW_SCHEMA",
    "SCHEMA_VERSION",
    "INSTRUMENT_STRUCT_FIELDS",
    "_INSTRUMENT_LIST",
    "_INSTRUMENT_STRUCT",
    "_LANE_LIST",
    "_MEASUREMENT_LIST",
    "_MEASUREMENT_STRUCT",
    "_SCHEMA_DICT",
    "_build_write_schema",
    "table_from_rows",
]

# Runs parquet footer stamp. Sourced from the central registry (one home);
# see ``litmus.data.schema_versions`` for the SemVer / migration contract.
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]

# EAV lane struct — the nested at-rest representation of one input / output
# entry. ``value_type`` selects which ``value_*`` lane holds the value. Field
# names must match ``_row_helpers.LANE_FIELDS`` / the encoder (guarded below).
_LANE_STRUCT = pa.struct(
    [
        ("name", pa.string()),
        ("value_type", pa.string()),
        ("value_int", pa.int64()),
        ("value_double", pa.float64()),
        ("value_bool", pa.bool_()),
        ("value_text", pa.string()),
        ("value_timestamp", pa.timestamp("us", tz="UTC")),
        ("value_json", pa.string()),
        ("unit", pa.string()),
        ("uut_pin", pa.string()),
    ]
)
assert [f.name for f in _LANE_STRUCT] == list(LANE_FIELDS), (
    "schemas._LANE_STRUCT drifted from _row_helpers.LANE_FIELDS"
)
_LANE_LIST = pa.list_(_LANE_STRUCT)

# Nested measurement struct — the at-rest representation of one measurement,
# carried in the vector row's ``measurements`` LIST. Field names must match
# ``_row_helpers.MEASUREMENT_STRUCT_FIELDS`` (guarded below). The daemon
# UNNESTs these into the flat measurement fact at ingest.
_MEASUREMENT_STRUCT = pa.struct(
    [
        ("name", pa.string()),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("outcome", pa.string()),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("limit_low", pa.float64()),
        ("limit_high", pa.float64()),
        ("limit_nominal", pa.float64()),
        ("limit_comparator", pa.string()),
        ("characteristic_id", pa.string()),
        ("spec_ref", pa.string()),
        ("uut_pin", pa.string()),
        ("fixture_connection", pa.string()),
        ("instrument_name", pa.string()),
        ("instrument_resource", pa.string()),
        ("instrument_channel", pa.string()),
    ]
)
assert [f.name for f in _MEASUREMENT_STRUCT] == list(MEASUREMENT_STRUCT_FIELDS), (
    "schemas._MEASUREMENT_STRUCT drifted from _row_helpers.MEASUREMENT_STRUCT_FIELDS"
)
_MEASUREMENT_LIST = pa.list_(_MEASUREMENT_STRUCT)

# Instrument inventory struct — the at-rest representation of one instrument
# connected during a run. Carried as a ``LIST<STRUCT>`` on every row
# (dense, self-describing). The daemon UNNESTs these into the flat
# ``instruments_materialized`` table for queries. Field names must match
# ``_row_helpers.INSTRUMENT_STRUCT_FIELDS`` (guarded below).
_INSTRUMENT_STRUCT = pa.struct(
    [
        ("name", pa.string()),
        ("id", pa.string()),
        ("driver", pa.string()),
        ("resource", pa.string()),
        ("protocol", pa.string()),
        ("manufacturer", pa.string()),
        ("model", pa.string()),
        ("serial_number", pa.string()),
        ("firmware", pa.string()),
        ("cal_due", pa.string()),
        ("cal_last", pa.string()),
        ("cal_certificate", pa.string()),
        ("cal_lab", pa.string()),
        ("mocked", pa.bool_()),
    ]
)
assert [f.name for f in _INSTRUMENT_STRUCT] == list(INSTRUMENT_STRUCT_FIELDS), (
    "schemas._INSTRUMENT_STRUCT drifted from _row_helpers.INSTRUMENT_STRUCT_FIELDS"
)
_INSTRUMENT_LIST = pa.list_(_INSTRUMENT_STRUCT)

# Canonical row schema for the unified per-run parquet — a chronological
# telling of the run. Every row carries an explicit ``record_type``
# discriminator with one of three values:
#   * ``record_type = 'run'`` — one row per run; run / UUT / station /
#     environment context. Step / vector columns are NULL.
#   * ``record_type = 'step'`` — one step-execution, keyed ``(step_path,
#     step_retry, vector_outer_index)``; carries code identity + timing +
#     rolled-up outcome, and its OWN ``inputs``/``outputs``/``measurements``
#     (a non-looping step can have zero vector rows — the step row carries
#     its own data, no synthesized scope vector). ``vector_index`` is always
#     NULL on this row kind.
#   * ``record_type = 'vector'`` — one condition-point execution carrier: a
#     swept step's own sweep variant, or an in-body ``vectors`` loop
#     iteration. Holds the ``inputs``/``outputs`` lanes and the nested
#     ``measurements`` list for that execution; ``vector_index`` is its own
#     0..N position.
#
# ``inputs`` / ``outputs`` are nested ``LIST<STRUCT<lanes>>`` columns (see
# ``_LANE_STRUCT``), not wide ``in_*``/``out_*`` columns; the DuckDB daemon
# UNNESTs them into the honestly-named ``inputs``/``outputs`` EAV tables (one
# per role, no ``role`` column) for queries. ``measurements`` is a nested
# ``LIST<STRUCT>`` on the vector row; the daemon UNNESTs it into the flat
# measurement fact for queries.
RUN_ROW_SCHEMA = pa.schema(
    [
        # Discriminator — 'run', 'step', or 'vector'
        ("record_type", pa.string()),
        # Identity & timing
        ("session_id", pa.string()),
        ("run_id", pa.string()),
        ("site_index", pa.int64()),
        ("site_name", pa.string()),
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
        # step_retry: 0-based outer (item) retry — pytest-rerunfailures rerun
        # count of this step. Distinct from vector_retry (the inner per-vector
        # retry). Both stamp every execution so a rerun is a distinct row.
        ("step_retry", pa.int64()),
        ("vector_index", pa.int64()),
        ("vector_outer_index", pa.int64()),
        ("vector_retry", pa.int64()),
        ("vector_started_at", pa.timestamp("us", tz="UTC")),
        ("vector_ended_at", pa.timestamp("us", tz="UTC")),
        # Who
        ("operator_id", pa.string()),
        ("operator_name", pa.string()),
        # UUT
        ("uut_serial_number", pa.string()),
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
        # Rollup
        ("step_outcome", pa.string()),
        ("vector_outcome", pa.string()),
        ("run_outcome", pa.string()),
        # Environment traceability
        ("python_version", pa.string()),
        ("litmus_version", pa.string()),
        ("env_fingerprint", pa.string()),
        # Dynamic attributes — nested EAV lanes (see _row_helpers). Names are
        # values inside the structs, so there is no column explosion.
        ("inputs", _LANE_LIST),
        ("outputs", _LANE_LIST),
        # Nested measurements on the vector row; the daemon UNNESTs these into
        # the flat measurement fact at ingest.
        ("measurements", _MEASUREMENT_LIST),
        # Instrument inventory on every row (dense, self-describing); the daemon
        # UNNESTs these into the flat ``instruments_materialized`` table.
        ("instruments", _INSTRUMENT_LIST),
    ],
    # Stamp the schema itself with its version (like events' ``_IPC_SCHEMA``), so
    # every table built with ``schema=RUN_ROW_SCHEMA`` carries the stamp by
    # construction — the real write path overrides this metadata with the fuller
    # ``_build_parquet_metadata`` dict (still schema_version), and fixtures that
    # write directly are stamped automatically instead of silently unstamped.
    metadata={b"schema_version": SCHEMA_VERSION.encode()},
)

_SCHEMA_DICT = {f.name: f.type for f in RUN_ROW_SCHEMA}


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
    """Build complete Arrow schema: fixed canonical + dynamic columns.

    Fixed columns (including the nested ``inputs``/``outputs`` lanes and
    the ``instruments`` struct list) use ``RUN_ROW_SCHEMA`` types. Any other
    stray column is inferred from its first non-None value. Passed to
    ``pa.Table.from_pylist()`` so Arrow validates at construction time.

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

    # Remaining dynamic columns inferred from values
    for key in sorted(all_keys - used):
        fields.append(pa.field(key, _infer_type_from_value(first_values.get(key))))

    # Carry the version stamp (as RUN_ROW_SCHEMA does), so tables built from this
    # dynamic schema are stamped by construction. The real write path overrides
    # with the fuller _build_parquet_metadata dict; direct writers are covered.
    return pa.schema(fields, metadata={b"schema_version": SCHEMA_VERSION.encode()})


def table_from_rows(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Build a PyArrow Table from row dicts against ``schema``.

    Wraps ``pa.Table.from_pylist`` so a build failure carries row context.
    """
    try:
        return pa.Table.from_pylist(rows, schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise pa.ArrowInvalid(f"Cannot build measurement table from rows: {exc}") from exc
