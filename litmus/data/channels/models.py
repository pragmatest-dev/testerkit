"""Data models for the channel store.

Channel metadata (ChannelDescriptor) is written once per channel.
Raw data uses typed Arrow schemas based on data_type.
"""

from __future__ import annotations

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


def _infer_schema(value: object, source_method: str = "") -> pa.Schema:
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
    elif isinstance(value, bool):
        fields.append(pa.field("value", pa.bool_()))
    elif isinstance(value, (int, float)):
        fields.append(pa.field("value", pa.float64()))
    elif isinstance(value, str):
        fields.append(pa.field("value", pa.utf8()))
    elif isinstance(value, (list, tuple)):
        fields.append(pa.field("samples", pa.list_(pa.float64())))
        fields.append(pa.field("sample_interval", pa.float64()))
    elif hasattr(value, "tolist"):
        fields.append(pa.field("samples", pa.list_(pa.float64())))
        fields.append(pa.field("sample_interval", pa.float64()))
    else:
        fields.append(pa.field("value", pa.utf8()))

    fields.append(pa.field("source_method", pa.utf8()))
    return pa.schema(fields)


# Legacy schemas — kept for reading old files.

SCALAR_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("value", pa.float64()),
    ("source_method", pa.utf8()),
])

ARRAY_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("samples", pa.list_(pa.float64())),
    ("sample_interval", pa.float64()),
    ("source_method", pa.utf8()),
])
