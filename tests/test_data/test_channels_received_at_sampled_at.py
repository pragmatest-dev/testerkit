"""Build item 11 — schema rename + new ``sampled_at`` column.

Pre-item-11 ChannelStore rows had a single ``timestamp`` column
holding the system-side write time. Item 11 renames it for clarity
and adds a nullable hardware-side timestamp:

- ``received_at`` (was ``timestamp``): set by the store at write
  time. Always present.
- ``sampled_at``: when the instrument sampled the value at the
  source. Nullable — drivers that don't expose a hardware timestamp
  leave it ``None`` and analytics falls back to ``received_at``.

The two-column shape pairs with the design's two distinct
analytical needs:

- "what did we see, and when (system clock)?" → received_at
- "what did the device think, and when (hardware clock)?" → sampled_at

No backcompat for pre-item-11 data dirs (``rm -rf data/`` is the
migration). Per CLAUDE.md test conventions: ``resolve_data_dir()``
+ uuid4 session_ids for per-test isolation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from litmus.data.channels.models import (
    ARRAY_SCHEMA,
    SCALAR_SCHEMA,
    ChannelSample,
    _infer_schema,
    batch_row_to_sample,
    sample_schema,
    sample_to_batch,
)
from litmus.data.channels.store import ChannelStore


def _make_store(tmp_path: Path, flush_threshold: int = 1000) -> ChannelStore:
    """Match existing test_channel_store.py fixture pattern (serve default)."""
    store = ChannelStore(tmp_path, uuid4(), flush_threshold=flush_threshold)
    store.open()
    return store


# --------------------------------------------------------------------- #
# Schema shape — top-level fallbacks                                    #
# --------------------------------------------------------------------- #


def test_scalar_schema_has_received_at_and_nullable_sampled_at() -> None:
    """``SCALAR_SCHEMA`` matches the new column layout per item 11."""
    names = SCALAR_SCHEMA.names
    assert "received_at" in names
    assert "sampled_at" in names
    assert "timestamp" not in names  # legacy name gone

    received_field = SCALAR_SCHEMA.field("received_at")
    sampled_field = SCALAR_SCHEMA.field("sampled_at")
    assert received_field.nullable is False  # always set
    assert sampled_field.nullable is True  # optional


def test_array_schema_has_received_at_and_nullable_sampled_at() -> None:
    """``ARRAY_SCHEMA`` matches the new column layout per item 11."""
    names = ARRAY_SCHEMA.names
    assert "received_at" in names
    assert "sampled_at" in names
    assert "timestamp" not in names

    assert ARRAY_SCHEMA.field("received_at").nullable is False
    assert ARRAY_SCHEMA.field("sampled_at").nullable is True


def test_sample_schema_carries_both_timestamps() -> None:
    """The Flight wire schema also has the two-timestamp shape."""
    s = sample_schema()
    assert "received_at" in s.names
    assert "sampled_at" in s.names
    assert "timestamp" not in s.names
    assert s.field("sampled_at").nullable is True


# --------------------------------------------------------------------- #
# Inferred schema — every shape gets the two-timestamp prefix           #
# --------------------------------------------------------------------- #


def test_inferred_scalar_schema_has_both_timestamps() -> None:
    schema = _infer_schema(3.31)
    assert schema.names[:2] == ["received_at", "sampled_at"]
    assert schema.field("sampled_at").nullable is True


def test_inferred_array_schema_has_both_timestamps() -> None:
    schema = _infer_schema([1.0, 2.0, 3.0])
    assert schema.names[:2] == ["received_at", "sampled_at"]
    assert schema.field("sampled_at").nullable is True


def test_inferred_dict_schema_has_both_timestamps() -> None:
    schema = _infer_schema({"a": 1.0, "b": 2.0})
    assert schema.names[:2] == ["received_at", "sampled_at"]


# --------------------------------------------------------------------- #
# ChannelSample — Pydantic field rename + new field                      #
# --------------------------------------------------------------------- #


def test_channel_sample_has_received_at_field() -> None:
    sample = ChannelSample(
        channel_id="dmm.voltage",
        received_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        value=3.31,
    )
    assert sample.received_at == datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
    assert sample.sampled_at is None  # defaults to None
    assert not hasattr(sample, "timestamp")  # legacy field gone


def test_channel_sample_sampled_at_settable() -> None:
    """Hardware-timestamped sources set sampled_at independently of received_at."""
    received = datetime(2026, 5, 31, 12, 0, 5, tzinfo=UTC)
    sampled = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)  # 5 seconds earlier
    sample = ChannelSample(
        channel_id="scope.ch1",
        received_at=received,
        sampled_at=sampled,
        value=0.0,
    )
    assert sample.received_at == received
    assert sample.sampled_at == sampled


def test_channel_sample_constructor_rejects_old_timestamp_kwarg() -> None:
    """Pre-item-11 ``timestamp=`` kwarg no longer accepted (Pydantic extra='ignore' by default?).

    Pydantic v2 default is to silently ignore unknown kwargs unless
    ``model_config['extra'] = 'forbid'``. We don't enforce forbid on
    ChannelSample, so old callers passing ``timestamp=`` will get
    None for ``received_at`` and Pydantic will raise on the missing
    required field. Verify that the failure mode is loud.
    """
    with pytest.raises(Exception):  # noqa: B017 (loud-error coverage)
        ChannelSample(
            channel_id="x",
            timestamp=datetime.now(UTC),  # type: ignore[call-arg]
            value=1.0,
        )


# --------------------------------------------------------------------- #
# sample_to_batch / batch_row_to_sample — round-trip                     #
# --------------------------------------------------------------------- #


def test_sample_to_batch_includes_both_timestamps() -> None:
    received = datetime(2026, 5, 31, 12, 0, 5, tzinfo=UTC)
    sampled = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
    sample = ChannelSample(
        channel_id="scope.ch1",
        received_at=received,
        sampled_at=sampled,
        value=3.14,
    )
    batch = sample_to_batch(sample)
    assert "received_at" in batch.schema.names
    assert "sampled_at" in batch.schema.names
    assert batch.column("received_at")[0].as_py() == received
    assert batch.column("sampled_at")[0].as_py() == sampled


def test_sample_to_batch_handles_null_sampled_at() -> None:
    """No hardware timestamp → ``sampled_at`` round-trips as None."""
    received = datetime(2026, 5, 31, 12, 0, 5, tzinfo=UTC)
    sample = ChannelSample(
        channel_id="dmm.voltage",
        received_at=received,
        sampled_at=None,
        value=3.31,
    )
    batch = sample_to_batch(sample)
    assert batch.column("sampled_at")[0].as_py() is None


def test_batch_round_trip_preserves_both_timestamps() -> None:
    """End-to-end: ChannelSample → batch → ChannelSample preserves shape."""
    received = datetime(2026, 5, 31, 12, 0, 5, tzinfo=UTC)
    sampled = datetime(2026, 5, 31, 12, 0, 0, 500_000, tzinfo=UTC)
    original = ChannelSample(
        channel_id="scope.ch1",
        received_at=received,
        sampled_at=sampled,
        value=2.71,
        unit="V",
    )

    batch = sample_to_batch(original)
    recovered = batch_row_to_sample(batch, 0)

    assert recovered.channel_id == "scope.ch1"
    assert recovered.received_at == received
    assert recovered.sampled_at == sampled
    assert recovered.value == 2.71
    assert recovered.unit == "V"


def test_batch_round_trip_with_null_sampled_at() -> None:
    received = datetime(2026, 5, 31, 12, 0, 5, tzinfo=UTC)
    original = ChannelSample(
        channel_id="dmm.voltage",
        received_at=received,
        value=3.31,
    )
    batch = sample_to_batch(original)
    recovered = batch_row_to_sample(batch, 0)

    assert recovered.received_at == received
    assert recovered.sampled_at is None


# --------------------------------------------------------------------- #
# ChannelStore.write — acquire ``sampled_at`` kwarg                      #
# --------------------------------------------------------------------- #


def test_store_write_accepts_sampled_at_kwarg(tmp_path: Path) -> None:
    """``ChannelStore.write`` exposes the hardware-timestamp kwarg.

    Callers with a hardware-timestamped sample (scope acquisition,
    DAQ block) pass it through; the row's ``sampled_at`` column
    captures it.
    """
    store = _make_store(tmp_path)
    sampled = datetime(2026, 5, 31, 11, 59, 30, tzinfo=UTC)

    store.write("scope.ch1", 0.123, sampled_at=sampled)
    store.close()

    arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
    assert len(arrow_files) == 1

    reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
    table = reader.read_all()
    assert table.column("sampled_at").to_pylist() == [sampled]
    # received_at was stamped at write time — not the same as sampled
    received_values = table.column("received_at").to_pylist()
    assert received_values[0] is not None
    assert received_values[0] != sampled


def test_store_write_without_sampled_at_leaves_column_null(tmp_path: Path) -> None:
    """Default case: no hardware time → row's ``sampled_at`` is None."""
    store = _make_store(tmp_path)
    store.write("dmm.voltage", 3.31)
    store.close()

    arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
    reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
    table = reader.read_all()
    assert table.column("sampled_at").to_pylist() == [None]
    # But received_at is always present
    received_values = table.column("received_at").to_pylist()
    assert received_values[0] is not None


def test_store_write_array_with_sampled_at(tmp_path: Path) -> None:
    """Array channels carry both timestamps too."""
    store = _make_store(tmp_path)
    sampled = datetime(2026, 5, 31, 11, 59, 30, tzinfo=UTC)
    store.write("scope.waveform", [1.0, 2.0, 3.0], sampled_at=sampled)
    store.close()

    arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
    reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
    table = reader.read_all()
    assert table.column("sampled_at").to_pylist() == [sampled]


# --------------------------------------------------------------------- #
# Query path uses received_at (not sampled_at) for time-range filtering #
# --------------------------------------------------------------------- #


def test_query_start_end_filters_on_received_at(tmp_path: Path) -> None:
    """``start=``/``end=`` filter on ``received_at`` (always present).

    Filtering on ``sampled_at`` would silently drop rows whose
    drivers don't expose a hardware timestamp; ``received_at`` is
    the unambiguous time-axis for "when did we know about it."
    """
    store = _make_store(tmp_path)
    # Write three samples close together — all received within ms
    for i in range(3):
        store.write("ts.ch", float(i))

    # Snapshot a mid-point received_at to use as the start filter
    table = store.query("ts.ch")
    received_values = table.column("received_at").to_pylist()
    assert len(received_values) == 3

    mid = received_values[1]
    windowed = store.query("ts.ch", start=mid)
    # rows >= mid → at least 2 (mid itself + later); first should be excluded
    assert len(windowed) == 2

    store.close()


# --------------------------------------------------------------------- #
# Defensive batch_row_to_sample — old schema without sampled_at         #
# --------------------------------------------------------------------- #


def test_batch_row_to_sample_handles_trimmed_schema_missing_sampled_at() -> None:
    """``batch_row_to_sample`` reads ``sampled_at`` defensively.

    A trimmed schema (no ``sampled_at`` column) deserializes with
    ``sampled_at=None`` so older readers / partial subscribers stay
    parseable.
    """
    trimmed = pa.schema(
        [
            ("channel_id", pa.utf8()),
            ("received_at", pa.timestamp("us", tz="UTC")),
            ("value", pa.utf8()),
        ]
    )
    received = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
    batch = pa.record_batch(
        {
            "channel_id": ["x"],
            "received_at": [received],
            "value": [json.dumps(1.0)],
        },
        schema=trimmed,
    )

    sample = batch_row_to_sample(batch, 0)
    assert sample.received_at == received
    assert sample.sampled_at is None
    assert sample.value == 1.0
