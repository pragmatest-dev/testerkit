"""Data models for the channel store.

Channel metadata (ChannelDescriptor) is written once per channel.
Raw data uses typed Arrow schemas based on data_type.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field

from litmus.data.models import _utcnow


class ChannelDescriptor(BaseModel):
    """Metadata for a single channel, written once when first seen."""

    channel_id: str
    data_type: str = "scalar"  # "scalar", "array"
    instrument_role: str = ""
    resource: str = ""
    units: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    first_seen: datetime = Field(default_factory=_utcnow)


class ChannelSample(BaseModel):
    """A single channel data point delivered to subscribers."""

    channel_id: str
    timestamp: datetime
    value: Any
    units: str | None = None
    sample_interval: float | None = None
    source_method: str = ""


# Arrow schemas — minimal columns, no per-row metadata duplication.


def _infer_field_type(value: object) -> pa.DataType:
    """Infer an Arrow data type from a Python value."""
    if isinstance(value, bool):
        return pa.bool_()
    if isinstance(value, int):
        return pa.float64()
    if isinstance(value, float):
        return pa.float64()
    if isinstance(value, str):
        return pa.utf8()
    if isinstance(value, (list, tuple)):
        if not value:
            return pa.list_(pa.float64())
        first = value[0]
        if isinstance(first, (list, tuple)):
            return pa.list_(pa.list_(pa.float64()))
        if isinstance(first, (int, float)):
            return pa.list_(pa.float64())
        return pa.list_(pa.utf8())
    if hasattr(value, "tolist"):  # numpy array
        return pa.list_(pa.float64())
    return pa.utf8()  # fallback: store repr


def _infer_schema(value: object) -> pa.Schema:
    """Build an Arrow schema from the first value written to a channel.

    - scalar (int/float) → timestamp + value columns
    - str/bool → timestamp + value columns with appropriate type
    - list/tuple of numbers → timestamp + samples + sample_interval
    - dict → timestamp + one column per key
    - numpy array → timestamp + samples
    - tuple ``([samples], dt)`` is a legacy waveform, converted before calling this
    """
    fields: list[pa.Field] = [pa.field("timestamp", pa.timestamp("us", tz="UTC"))]

    if isinstance(value, dict):
        for k, v in value.items():
            fields.append(pa.field(k, _infer_field_type(v)))
    elif isinstance(value, (list, tuple)) or hasattr(value, "tolist"):
        # numpy array or list/tuple of numbers → array channel
        fields.append(pa.field("samples", pa.list_(pa.float64())))
        fields.append(pa.field("sample_interval", pa.float64()))
    else:
        # scalar (int/float/bool/str) — delegate to the single-value
        # inference so leaf-type rules live in exactly one place
        fields.append(pa.field("value", _infer_field_type(value)))

    fields.append(pa.field("source_method", pa.utf8()))
    fields.append(pa.field("session_id", pa.utf8()))
    return pa.schema(fields)


# Legacy schemas — kept for reading old files and as empty-result
# fallbacks. ``SCALAR_SCHEMA`` is used by ``ChannelStore.query`` when
# no writer schema is available; ``ARRAY_SCHEMA`` is the parallel
# fallback intended for array-type channels — see ROADMAP "Array
# channel empty-result schema" for the wiring plan.

SCALAR_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("value", pa.float64()),
        ("source_method", pa.utf8()),
        ("session_id", pa.utf8()),
    ]
)

ARRAY_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("samples", pa.list_(pa.float64())),
        ("sample_interval", pa.float64()),
        ("source_method", pa.utf8()),
        ("session_id", pa.utf8()),
    ]
)


def encode_value(value: object) -> str:
    """Encode a Python value to the wire string used in the ``value`` column.

    Strings pass through verbatim (avoid double-encoding); everything
    else goes through ``json.dumps``. The Flight transport stores all
    values as ``pa.utf8`` for flexibility; readers JSON-decode and
    fall back to the raw string when decode fails.
    """
    return value if isinstance(value, str) else json.dumps(value)


def sample_schema() -> pa.Schema:
    """Default schema for ``ChannelSample`` batches over Arrow Flight.

    Used by both the server (which sends batches to subscribers) and
    the client (which writes batches via ``do_put``). Lives here rather
    than in ``server.py`` because both store.py and client.py need it,
    and importing through server creates a circular dependency.
    """
    return pa.schema(
        [
            ("channel_id", pa.utf8()),
            ("timestamp", pa.timestamp("us", tz="UTC")),
            ("value", pa.utf8()),  # JSON-encoded for flexibility
            ("source_method", pa.utf8()),
            ("units", pa.utf8()),
            ("sample_interval", pa.float64()),
        ]
    )


def sample_to_batch(sample: ChannelSample) -> pa.RecordBatch:
    """Convert a :class:`ChannelSample` to a single-row RecordBatch."""
    value_str = encode_value(sample.value)
    return pa.record_batch(
        {
            "channel_id": [sample.channel_id],
            "timestamp": [sample.timestamp],
            "value": [value_str],
            "source_method": [sample.source_method],
            "units": [sample.units or ""],
            "sample_interval": [sample.sample_interval],
        },
        schema=sample_schema(),
    )


def batch_row_to_sample(batch: pa.RecordBatch, i: int) -> ChannelSample:
    """Reconstruct a :class:`ChannelSample` from row ``i`` of a Flight batch.

    Inverse of :func:`sample_to_batch`. Used by both the in-process
    server (writing remote do_put batches into the local store) and
    the client (delivering subscription updates back to user
    callbacks). The ``value`` column is JSON-decoded; non-JSON
    strings pass through. Optional columns (``units``,
    ``sample_interval``, ``source_method``) read defensively for
    older or trimmed schemas.
    """
    columns = set(batch.schema.names)
    value_raw = batch.column("value")[i].as_py()
    try:
        value: Any = json.loads(value_raw)
    except (json.JSONDecodeError, TypeError):
        value = value_raw

    units: str | None = None
    if "units" in columns:
        units = batch.column("units")[i].as_py() or None

    sample_interval: float | None = None
    if "sample_interval" in columns:
        sample_interval = batch.column("sample_interval")[i].as_py()

    source_method = ""
    if "source_method" in columns:
        source_method = batch.column("source_method")[i].as_py() or ""

    return ChannelSample(
        channel_id=batch.column("channel_id")[i].as_py(),
        timestamp=batch.column("timestamp")[i].as_py(),
        value=value,
        units=units,
        sample_interval=sample_interval,
        source_method=source_method,
    )
