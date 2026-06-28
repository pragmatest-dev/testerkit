"""Data models for the channel store.

Channel metadata (ChannelDescriptor) is written once per channel.
Raw data uses typed Arrow schemas based on value_type.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field

from litmus.data.models import _utcnow


class SubscribePolicy(StrEnum):
    """How a live subscriber's ring handles samples it hasn't drained yet.

    - ``ALL``: keep every batch (lossless while the consumer keeps up); on
      overflow drop oldest and count a gap.
    - ``LATEST``: conflate to the newest batch only (a gauge that always shows
      the current value, never a backlog).
    """

    ALL = "all"
    LATEST = "latest"


# C3 schema-version stamp — first stamp of the channel Arrow IPC format.
# Channel ``.arrow`` files are a published, directly-readable consumer
# surface (DuckDB / pandas / Polars can open them without Litmus), so
# this is a real consumer-facing version contract. Bump when the durable
# at-rest column shape changes in a breaking way; add a migration note.
CHANNEL_SCHEMA_VERSION = "1.0"

# Channels rides the shared DuckDBFlightServer under this db name. The do_put
# descriptor is ``CHANNELS_FLIGHT_DB\0<table>`` — the put-hook reads the
# ``channel_id`` column off the wire batch, so the table slot is a constant.
CHANNELS_FLIGHT_DB = "channels"
CHANNELS_PUT_COMMAND = b"channels\0live"


class ChannelDescriptor(BaseModel):
    """Metadata for a single channel, written once when first seen.

    ``value_type`` carries the channel's shape AND leaf type after
    build item 14 — examples: ``"scalar:float"``, ``"scalar:int"``,
    ``"scalar:bool"``, ``"scalar:str"``, ``"array:float"``,
    ``"array:int"``, ``"array:bool"``, ``"array:str"``. Legacy bare
    ``"scalar"`` / ``"array"`` values from pre-0.2 data dirs aren't
    supported (no-backcompat; ``rm -rf data/`` is the migration).
    """

    channel_id: str
    value_type: str = "scalar:float"  # "{shape}:{leaf}" — see class docstring
    instrument_role: str = ""
    resource: str = ""
    unit: str | None = None
    # Producing session's host + id, stamped at registration. The registry keys
    # identity on (hostname, channel) so it survives a producer restart.
    hostname: str = ""
    session_id: str = ""
    # Channel-level metadata bag. Renamed from ``properties`` to
    # ``attributes`` in build item 17 for cross-schema vocabulary
    # consistency (matches FileArtifactMetadata.attributes and
    # Waveform.attributes).
    attributes: dict[str, Any] = Field(default_factory=dict)
    first_seen: datetime = Field(default_factory=_utcnow)


class ChannelSample(BaseModel):
    """A single channel data point delivered to subscribers.

    Two timestamps (build item 11):

    - ``received_at`` (was ``timestamp``): when the system received
      the sample (today's ``datetime.now(UTC)`` at write time).
    - ``sampled_at``: when the instrument sampled the value at the source.
      Hardware-timestamped values (scope acquisitions, DAQ blocks)
      should set this. Nullable — drivers that don't know leave it
      ``None`` and analytics falls back to ``received_at``.
    """

    channel_id: str
    received_at: datetime
    sampled_at: datetime | None = None
    value: Any
    unit: str | None = None
    sample_interval: float | None = None
    source_method: str = ""
    session_id: str | None = None
    sample_offset: int = -1
    """Monotonic per-(channel, session) write position, stamped by the
    producer. Carried identically into the live batch and the durable
    segment so a history-to-live stitch can dedup on (session_id, sample_offset)
    without timestamp ties. ``-1`` means unstamped. Internal ordering
    cursor — never a verb parameter."""


# Arrow schemas — minimal columns, no per-row metadata duplication.


def _infer_field_type(value: object) -> pa.DataType:
    """Infer an Arrow data type from a Python value.

    Build item 14: typed leaf-type support. Pre-0.2 ``int`` was cast
    to ``float64`` (truncation hazard for large ints); arrays always
    became ``list<float64>``. Now leaf types preserve:

    - scalar ``bool`` → ``pa.bool_()``
    - scalar ``int`` → ``pa.int64()`` (was ``float64``)
    - scalar ``float`` → ``pa.float64()``
    - scalar ``str`` → ``pa.utf8()``
    - list/tuple → ``pa.list_(<inferred leaf>)`` from first element
      (or ``float64`` for empty)
    - numpy array → ``pa.list_(<dtype from numpy>)`` (was hardcoded
      float64)
    - else → ``pa.utf8()`` (repr fallback)
    """
    # bool must come BEFORE int since `True` is also an int in Python.
    if isinstance(value, bool):
        return pa.bool_()
    if isinstance(value, int):
        return pa.int64()
    if isinstance(value, float):
        return pa.float64()
    if isinstance(value, str):
        return pa.utf8()
    if isinstance(value, (list, tuple)):
        if not value:
            return pa.list_(pa.float64())
        first = value[0]
        if isinstance(first, (list, tuple)):
            # Nested lists — fall back to the leaf of the inner list.
            return pa.list_(_infer_field_type(first))
        # Element type follows leaf-inference rules; bool before int.
        return pa.list_(_infer_field_type(first))
    if hasattr(value, "tolist") and hasattr(value, "dtype"):
        # numpy array — preserve dtype (item 14: no more float64 erasure).
        try:
            leaf = pa.from_numpy_dtype(value.dtype)  # type: ignore[attr-defined]
        except (pa.ArrowNotImplementedError, TypeError):
            leaf = pa.float64()
        return pa.list_(leaf)
    if hasattr(value, "tolist"):
        # Generic array-like without dtype — fall back to float64.
        return pa.list_(pa.float64())
    return pa.utf8()  # fallback: store repr


def _value_type_for(value: object) -> str:
    """Return the ``ChannelDescriptor.value_type`` string for a value.

    Format: ``"{shape}:{leaf}"`` — e.g., ``"scalar:int"``, ``"array:bool"``.
    Used by the registry to record the channel's value type at first write.
    """
    if isinstance(value, dict):
        return "struct"
    if isinstance(value, (list, tuple)) or (
        hasattr(value, "tolist") and not isinstance(value, str)
    ):
        # array shape — leaf type from first element / dtype
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            return f"array:{_leaf_name_from_pytype(first)}"
        if hasattr(value, "tolist") and hasattr(value, "dtype"):
            return f"array:{_leaf_name(value.dtype)}"  # type: ignore[attr-defined]
        return "array:float"  # empty or unknown
    return f"scalar:{_leaf_name_from_pytype(value)}"


def _leaf_name_from_pytype(value: object) -> str:
    """Map a Python value to a leaf-type name for ``value_type``."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "any"


def _leaf_name(dtype: Any) -> str:
    """Map a numpy dtype to a leaf-type name for ``value_type``."""
    kind = getattr(dtype, "kind", None)
    if kind == "b":
        return "bool"
    if kind in ("i", "u"):
        return "int"
    if kind == "f":
        return "float"
    if kind in ("U", "S", "O"):
        return "str"
    return "any"


def _infer_schema(value: object) -> pa.Schema:
    """Build an Arrow schema from the first value written to a channel.

    Build item 14: leaf types preserve through to the column dtype.
    Build item 11b (C3a-pre): the payload column is named ``value``
    uniformly across shapes. For arrays its type is ``list<leaf>``;
    for scalars it's ``leaf``. One row = one channel write (one
    "sample"); the column carries that write's payload regardless of
    inner shape.

    - scalar (int/float/bool/str) → ``received_at`` + ``sampled_at``
      + ``value`` (typed scalar)
    - list/tuple → ``received_at`` + ``sampled_at`` + ``value``
      (typed ``list<leaf>``) + ``sample_interval`` (inner spacing)
    - numpy array → same as list (typed via numpy dtype)
    - dict → ``received_at`` + ``sampled_at`` + per-key columns
    - tuple ``([items], dt)`` is a legacy waveform, converted before
      calling this

    ``sampled_at`` (build item 11) is nullable for hardware-side
    sampling time; ``sample_interval`` (array shape only) is the
    inter-value time within a single write's payload.
    """
    fields: list[pa.Field] = [
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("sampled_at", pa.timestamp("us", tz="UTC"), nullable=True),
    ]

    if isinstance(value, dict):
        for k, v in value.items():
            fields.append(pa.field(k, _infer_field_type(v)))
    elif isinstance(value, (list, tuple)) or hasattr(value, "tolist"):
        # Array channel — leaf type is inferred from first element /
        # numpy dtype (item 14: no more hardcoded float64 erasure).
        array_type = _infer_field_type(value)
        # ``_infer_field_type`` returns the FULL list type for arrays
        # (e.g., list<bool>). Use it directly as the ``value`` column.
        if pa.types.is_list(array_type):
            fields.append(pa.field("value", array_type))
        else:
            # Defensive: if the array inference returned non-list,
            # fall back to wrapping with float64.
            fields.append(pa.field("value", pa.list_(pa.float64())))
        fields.append(pa.field("sample_interval", pa.float64()))
    else:
        # scalar (int/float/bool/str) — delegate to the single-value
        # inference so leaf-type rules live in exactly one place
        fields.append(pa.field("value", _infer_field_type(value)))

    fields.append(pa.field("source_method", pa.utf8()))
    fields.append(pa.field("session_id", pa.utf8()))
    fields.append(pa.field("sample_offset", pa.int64()))
    return pa.schema(fields)


# Legacy schemas — kept for reading old files and as empty-result
# fallbacks. ``SCALAR_SCHEMA`` is used by ``ChannelStore.query`` when
# no writer schema is available; ``ARRAY_SCHEMA`` is the parallel
# fallback intended for array-type channels — see ROADMAP "Array
# channel empty-result schema" for the wiring plan.

SCALAR_SCHEMA = pa.schema(
    [
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("sampled_at", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("value", pa.float64()),
        pa.field("source_method", pa.utf8()),
        pa.field("session_id", pa.utf8()),
        pa.field("sample_offset", pa.int64()),
    ]
)

ARRAY_SCHEMA = pa.schema(
    [
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("sampled_at", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("value", pa.list_(pa.float64())),
        pa.field("sample_interval", pa.float64()),
        pa.field("source_method", pa.utf8()),
        pa.field("session_id", pa.utf8()),
        pa.field("sample_offset", pa.int64()),
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
            pa.field("channel_id", pa.utf8()),
            pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("sampled_at", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("value", pa.utf8()),  # JSON-encoded for flexibility
            pa.field("source_method", pa.utf8()),
            pa.field("unit", pa.utf8()),
            pa.field("sample_interval", pa.float64()),
            pa.field("session_id", pa.utf8()),
            pa.field("sample_offset", pa.int64()),
        ]
    )


def samples_to_batch(samples: list[ChannelSample]) -> pa.RecordBatch:
    """Convert N samples to ONE RecordBatch (the coalesced wire message).

    The columnar counterpart to :func:`sample_to_batch`: builds each column
    once for the whole list so N samples ride a single gRPC do_put instead of
    N one-row messages. All samples carry the same schema (``sample_schema``).
    """
    return pa.record_batch(
        {
            "channel_id": [s.channel_id for s in samples],
            "received_at": [s.received_at for s in samples],
            "sampled_at": [s.sampled_at for s in samples],
            "value": [encode_value(s.value) for s in samples],
            "source_method": [s.source_method for s in samples],
            "unit": [s.unit or "" for s in samples],
            "sample_interval": [s.sample_interval for s in samples],
            "session_id": [s.session_id for s in samples],
            "sample_offset": [s.sample_offset for s in samples],
        },
        schema=sample_schema(),
    )


def sample_to_batch(sample: ChannelSample) -> pa.RecordBatch:
    """Convert a :class:`ChannelSample` to a single-row RecordBatch."""
    value_str = encode_value(sample.value)
    return pa.record_batch(
        {
            "channel_id": [sample.channel_id],
            "received_at": [sample.received_at],
            "sampled_at": [sample.sampled_at],
            "value": [value_str],
            "source_method": [sample.source_method],
            "unit": [sample.unit or ""],
            "sample_interval": [sample.sample_interval],
            "session_id": [sample.session_id],
            "sample_offset": [sample.sample_offset],
        },
        schema=sample_schema(),
    )


def batch_row_to_sample(batch: pa.RecordBatch, i: int) -> ChannelSample:
    """Reconstruct a :class:`ChannelSample` from row ``i`` of a Flight batch.

    Inverse of :func:`sample_to_batch`. Used by both the in-process
    server (writing remote do_put batches into the local store) and
    the client (delivering subscription updates back to user
    callbacks). The ``value`` column is JSON-decoded; non-JSON
    strings pass through. Optional columns (``unit``,
    ``sample_interval``, ``source_method``, ``sampled_at``) read
    defensively for trimmed schemas.
    """
    columns = set(batch.schema.names)
    value_raw = batch.column("value")[i].as_py()
    try:
        value: Any = json.loads(value_raw)
    except (json.JSONDecodeError, TypeError):
        value = value_raw

    unit: str | None = None
    if "unit" in columns:
        unit = batch.column("unit")[i].as_py() or None

    sample_interval: float | None = None
    if "sample_interval" in columns:
        sample_interval = batch.column("sample_interval")[i].as_py()

    source_method = ""
    if "source_method" in columns:
        source_method = batch.column("source_method")[i].as_py() or ""

    sampled_at: datetime | None = None
    if "sampled_at" in columns:
        sampled_at = batch.column("sampled_at")[i].as_py()

    session_id: str | None = None
    if "session_id" in columns:
        session_id = batch.column("session_id")[i].as_py() or None

    sample_offset = -1
    if "sample_offset" in columns:
        seq_val = batch.column("sample_offset")[i].as_py()
        if seq_val is not None:
            sample_offset = seq_val

    return ChannelSample(
        channel_id=batch.column("channel_id")[i].as_py(),
        received_at=batch.column("received_at")[i].as_py(),
        sampled_at=sampled_at,
        value=value,
        unit=unit,
        sample_interval=sample_interval,
        source_method=source_method,
        session_id=session_id,
        sample_offset=sample_offset,
    )
