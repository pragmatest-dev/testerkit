"""Canonical Arrow schema contract shared across all backends.

Defines the measurement schema, type inference for dynamic columns,
and helpers for constructing validated Arrow tables. Any backend
(Parquet, Flight, NATS) imports from here rather than defining its own
schema.
"""

import logging
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS

logger = logging.getLogger(__name__)

__all__ = [
    "MEASUREMENT_SCHEMA",
    "SCHEMA_VERSION",
    "STEP_SCHEMA",
    "_INSTR_ARRAY_TYPES",
    "_SCHEMA_DICT",
    "_build_write_schema",
    "_enforce_schema",
    "table_from_rows",
]

SCHEMA_VERSION = "2.0"

# Canonical schema for fixed columns. Dynamic columns (in_*, out_*, step_instruments_*, custom_*)
# are NOT listed here — they pass through with inferred types.
MEASUREMENT_SCHEMA = pa.schema(
    [
        # Identity & timing
        ("session_id", pa.string()),
        ("run_id", pa.string()),
        ("slot_id", pa.string()),
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
        ("vector_attempt", pa.int64()),
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
        ("dut_pin", pa.string()),
        ("fixture_connection", pa.string()),
        ("instrument_name", pa.string()),
        ("instrument_resource", pa.string()),
        ("instrument_channel", pa.string()),
        # Rollup
        ("vector_outcome", pa.string()),
        ("run_outcome", pa.string()),
        # Environment traceability
        ("python_version", pa.string()),
        ("litmus_version", pa.string()),
        ("env_fingerprint", pa.string()),
    ]
)

STEP_SCHEMA = pa.schema(
    [
        # Step identity
        ("index", pa.int32()),
        ("name", pa.string()),
        ("node_id", pa.string()),
        ("file", pa.string()),
        ("function", pa.string()),
        ("class_name", pa.string()),
        ("module", pa.string()),
        ("step_path", pa.string()),
        ("description", pa.string()),
        ("markers", pa.string()),
        # Execution
        ("outcome", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("duration_s", pa.float64()),
        # Counts
        ("has_measurements", pa.bool_()),
        ("measurement_count", pa.int32()),
        ("vector_count", pa.int32()),
        # Run context (denormalized — matches measurement schema)
        ("run_id", pa.string()),
        ("session_id", pa.string()),
        ("slot_id", pa.string()),
        ("run_started_at", pa.timestamp("us", tz="UTC")),
        ("run_ended_at", pa.timestamp("us", tz="UTC")),
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
        ("test_phase", pa.string()),
        ("project_name", pa.string()),
        ("git_commit", pa.string()),
        ("git_branch", pa.string()),
        ("git_remote", pa.string()),
    ]
)

_SCHEMA_DICT = {f.name: f.type for f in MEASUREMENT_SCHEMA}

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
    """Build complete Arrow schema: fixed canonical + dynamic columns.

    Fixed columns use MEASUREMENT_SCHEMA types. Instrument arrays use
    known list types. Dynamic columns (in_*, out_*, custom_*) are inferred
    from the first non-None value. Passed to ``pa.Table.from_pylist()``
    so Arrow validates at construction time.

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
    for field in MEASUREMENT_SCHEMA:
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
    """Build a PyArrow Table from row dicts with schema validation.

    Wraps ``pa.Table.from_pylist`` with a descriptive error when dynamic
    columns contain mixed types that Arrow cannot reconcile.
    """
    try:
        return pa.Table.from_pylist(rows, schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        mixed: list[str] = []
        for field in schema:
            if field.name in _SCHEMA_DICT:
                continue
            types_seen = {
                type(row.get(field.name)) for row in rows if row.get(field.name) is not None
            }
            if len(types_seen) > 1:
                type_names = ", ".join(
                    t.__name__ for t in sorted(types_seen, key=lambda t: t.__name__)
                )
                mixed.append(f"  {field.name}: {type_names}")
        detail = "\n".join(mixed) if mixed else "  (see original error)"
        raise pa.ArrowInvalid(
            f"Cannot build table — dynamic columns have mixed types:\n"
            f"{detail}\n"
            f"Ensure each in_*/out_*/custom_* column uses a consistent type "
            f"across all measurements.\n"
            f"Original error: {exc}"
        ) from exc


def _enforce_schema(table: pa.Table) -> pa.Table:
    """Normalize column types to match MEASUREMENT_SCHEMA.

    Read-path shim for files written before explicit schemas were enforced
    on write. New writes use ``_build_write_schema()`` to set types at
    construction time.

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
            columns.append(
                pa.array(
                    [str(v) if v is not None else None for v in values],
                    type=target_type,
                )
            )
        else:
            logger.warning(
                "Cannot enforce schema for %s: %s → %s, keeping original",
                field.name,
                field.type,
                target_type,
            )
            columns.append(col)

        names.append(field.name)

    return pa.table(columns, names=names)
