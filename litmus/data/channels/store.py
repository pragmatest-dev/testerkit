"""Channel store — materializes instrument events into Arrow IPC time-series.

Implements EventSubscriber. Buffers InstrumentRead/InstrumentSet events
in memory, writes one Arrow IPC file per channel per session on close().
Maintains a ``_index.parquet`` with segment metadata per date partition.
"""

from __future__ import annotations

import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from litmus.data.events import EventBase, InstrumentRead, InstrumentSet

_WRITE_ERRORS = (OSError, pa.ArrowException)  # type: ignore[attr-defined]

CHANNEL_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("session_id", pa.utf8()),
    ("run_id", pa.utf8()),
    ("value", pa.float64()),
    ("units", pa.utf8()),
    ("source_method", pa.utf8()),
])

INDEX_SCHEMA = pa.schema([
    ("channel_id", pa.utf8()),
    ("started_at", pa.timestamp("us", tz="UTC")),
    ("ended_at", pa.timestamp("us", tz="UTC")),
    ("row_count", pa.int64()),
    ("file_path", pa.utf8()),
])


class ChannelStore:
    """EventSubscriber that materializes instrument events to Arrow IPC.

    One file per channel per session, date-partitioned:
    ``channels/{date}/{channel_id}_{session_short}.arrow``
    """

    format_name: str = "channels"
    event_types: set[type] = {InstrumentRead, InstrumentSet}

    def __init__(self, channels_dir: Path, session_id: UUID) -> None:
        self._channels_dir = channels_dir
        self._session_id = session_id
        self._buffers: dict[str, list[dict]] = {}

    def open(self) -> None:
        self._channels_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: EventBase) -> None:
        # Only read/set produce numeric channel data.
        # InstrumentConfigure is logged in the JSONL event log but not
        # materialized to channels (no numeric value to store).
        if isinstance(event, InstrumentRead):
            channel_id = event.channel_id
            method = event.method
            value = event.value
        elif isinstance(event, InstrumentSet):
            channel_id = event.channel_id
            method = event.attribute
            value = event.value
        else:
            return

        try:
            float_value = float(value) if value is not None else None
        except (TypeError, ValueError):
            float_value = None
            warnings.warn(
                f"Channel {channel_id}: cannot coerce "
                f"{type(value).__name__} to float",
                stacklevel=2,
            )

        row = {
            "timestamp": event.occurred_at,
            "session_id": str(event.session_id),
            "run_id": str(event.run_id) if event.run_id else None,
            "value": float_value,
            "units": event.units,
            "source_method": method,
        }

        self._buffers.setdefault(channel_id, []).append(row)

    def close(self) -> None:
        if not self._buffers:
            return

        try:
            today = date.today().isoformat()
            date_dir = self._channels_dir / today
            date_dir.mkdir(parents=True, exist_ok=True)

            session_short = str(self._session_id)[:8]
            index_rows: list[dict] = []

            for channel_id, rows in self._buffers.items():
                if not rows:
                    continue
                try:
                    table = pa.table(
                        {
                            col: [r[col] for r in rows]
                            for col in CHANNEL_SCHEMA.names
                        },
                        schema=CHANNEL_SCHEMA,
                    )

                    filename = f"{channel_id}_{session_short}.arrow"
                    filepath = date_dir / filename

                    with ipc.new_file(str(filepath), CHANNEL_SCHEMA) as writer:
                        writer.write_table(table)

                    timestamps = [r["timestamp"] for r in rows]
                    index_rows.append({
                        "channel_id": channel_id,
                        "started_at": min(timestamps),
                        "ended_at": max(timestamps),
                        "row_count": len(rows),
                        "file_path": str(
                            filepath.relative_to(self._channels_dir),
                        ),
                    })
                except _WRITE_ERRORS as exc:
                    warnings.warn(
                        f"ChannelStore failed to write '{channel_id}': {exc}",
                        stacklevel=2,
                    )

            # Write per-session index (no shared file → no race condition)
            if index_rows:
                try:
                    index_path = (
                        date_dir / f"_index_{session_short}.parquet"
                    )
                    idx_table = pa.table(
                        {
                            col: [r[col] for r in index_rows]
                            for col in INDEX_SCHEMA.names
                        },
                        schema=INDEX_SCHEMA,
                    )
                    pq.write_table(idx_table, index_path)
                except _WRITE_ERRORS as exc:
                    warnings.warn(
                        f"ChannelStore failed to write index: {exc}",
                        stacklevel=2,
                    )
        finally:
            self._buffers.clear()

    @classmethod
    def query(
        cls,
        channels_dir: Path,
        channel_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pa.Table:
        """Query channel data by ID and optional time range.

        Reads all Arrow IPC files matching the channel ID across date
        partitions. If ``start`` or ``end`` are provided, rows outside
        the range are filtered out. Naive datetimes are treated as UTC.
        """
        tables: list[pa.Table] = []

        for arrow_file in sorted(channels_dir.glob(f"*/{channel_id}_*.arrow")):
            reader = ipc.open_file(str(arrow_file))
            table = reader.read_all()
            tables.append(table)

        if not tables:
            return pa.table(
                {col: [] for col in CHANNEL_SCHEMA.names},
                schema=CHANNEL_SCHEMA,
            )

        result = pa.concat_tables(tables)

        # Filter by time range using pandas-style conversion
        if start is not None or end is not None:
            timestamps = result.column("timestamp").to_pylist()
            start_utc = (
                start.astimezone(UTC) if start and start.tzinfo
                else start.replace(tzinfo=UTC) if start else None
            )
            end_utc = (
                end.astimezone(UTC) if end and end.tzinfo
                else end.replace(tzinfo=UTC) if end else None
            )
            keep = []
            for ts in timestamps:
                if start_utc and ts < start_utc:
                    keep.append(False)
                elif end_utc and ts > end_utc:
                    keep.append(False)
                else:
                    keep.append(True)
            result = result.filter(keep)

        return result
