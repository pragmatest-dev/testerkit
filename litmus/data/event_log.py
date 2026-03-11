"""Event log writer and subscriber protocol.

The EventLog buffers typed events and flushes them as multi-row Arrow
batches to IPC files via ``BufferedIPCWriter`` — shared infrastructure
with ChannelStore's ``_ChannelWriter``.

Subscribers are dispatched immediately on emit; IPC writes are batched.

Storage: ``results/events/{date}/{session_id}.arrow``
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import pyarrow as pa

from litmus.data._event_filters import event_matches_role
from litmus.data._ipc_writer import BufferedIPCWriter, read_ipc_batches
from litmus.data.backends._row_helpers import save_ref_to_dir
from litmus.data.events import EventBase

# Schema for the index columns stored in IPC files.
# Full event JSON is kept in the ``json`` column for lossless replay.
_IPC_SCHEMA = pa.schema([
    ("id", pa.string()),
    ("event_type", pa.string()),
    ("occurred_at", pa.timestamp("us", tz="UTC")),
    ("received_at", pa.timestamp("us", tz="UTC")),
    ("session_id", pa.string()),
    ("run_id", pa.string()),
    ("json", pa.string()),
])

_DEFAULT_FLUSH_THRESHOLD = 50


class _EventIPCWriter(BufferedIPCWriter):
    """IPC writer that calls on_flush after each batch is written."""

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        flush_threshold: int = 50,
        on_flush: Callable[[pa.RecordBatch], None] | None = None,
    ) -> None:
        super().__init__(path, schema, flush_threshold=flush_threshold)
        self._on_flush_cb = on_flush

    def _on_flush(self, batch: pa.RecordBatch) -> None:
        if self._on_flush_cb is not None:
            try:
                self._on_flush_cb(batch)
            except Exception as exc:
                warnings.warn(f"on_flush callback failed: {exc}", stacklevel=2)


@runtime_checkable
class EventSubscriber(Protocol):
    """Protocol for event log subscribers."""

    format_name: str
    event_types: set[type]

    def open(self) -> None: ...
    def on_event(self, event: EventBase) -> None: ...
    def close(self) -> None: ...


class EventLog:
    """Buffers events, flushes as batched Arrow IPC writes.

    One file per session, date-partitioned. Each event gets
    ``received_at`` stamped on emit. IPC writes are batched at
    ``flush_threshold`` rows via shared ``BufferedIPCWriter``.
    """

    def __init__(
        self,
        log_dir: Path,
        session_id: UUID,
        flush_threshold: int = _DEFAULT_FLUSH_THRESHOLD,
        on_emit: Callable[[EventBase], None] | None = None,
        on_flush: Callable[[pa.RecordBatch], None] | None = None,
    ) -> None:
        self.log_dir = log_dir
        self.session_id = session_id
        self._on_emit = on_emit
        self._on_flush = on_flush
        date_dir = self.log_dir / date.today().isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)
        self._ipc = _EventIPCWriter(
            path=date_dir / f"{session_id}.arrow",
            schema=_IPC_SCHEMA,
            flush_threshold=flush_threshold,
            on_flush=on_flush,
        )
        self._subscribers: list[EventSubscriber] = []
        self._failed: set[EventSubscriber] = set()
        self._ref_dir = date_dir / f"{session_id}_ref"

    @property
    def path(self) -> Path:
        return self._ipc.path

    def emit(self, event: EventBase) -> pa.RecordBatch | None:
        """Stamp received_at, buffer for IPC, dispatch to subscribers.

        Returns the flushed batch if the buffer hit the threshold, or
        None if the event was just buffered.
        """
        event.received_at = datetime.now(UTC)

        batch = self._ipc.append({
            "id": str(event.id),
            "event_type": event.event_type,  # type: ignore[attr-defined]
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "session_id": str(event.session_id),
            "run_id": str(event.run_id) if event.run_id else None,
            "json": event.model_dump_json(),
        })

        for sub in self._subscribers:
            if sub in self._failed:
                continue
            if type(event) not in sub.event_types:
                continue
            try:
                sub.on_event(event)
            except Exception as exc:
                self._failed.add(sub)
                warnings.warn(
                    f"EventSubscriber '{sub.format_name}' failed on "
                    f"{type(event).__name__}: {exc}",
                    stacklevel=2,
                )

        # Notify store-level callback (bridges EventLog → EventStore subscribers)
        if self._on_emit is not None:
            try:
                self._on_emit(event)
            except Exception as exc:
                warnings.warn(f"on_emit callback failed: {exc}", stacklevel=2)

        return batch

    def flush(self) -> pa.RecordBatch | None:
        """Flush buffered events to IPC file. Returns the batch, or None."""
        return self._ipc.flush()

    def add_subscriber(self, sub: EventSubscriber) -> None:
        """Register a subscriber and call open().

        If open() raises, the subscriber is not registered and the
        exception propagates to the caller.
        """
        sub.open()
        self._subscribers.append(sub)

    def save_ref(self, vector_id: str, key: str, value: Any) -> str:
        """Save large data to _ref/ subdirectory."""
        self._ref_dir.mkdir(exist_ok=True)
        return save_ref_to_dir(self._ref_dir, vector_id, key, value)

    def events(
        self,
        *,
        event_type: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        """Read events from this session's Arrow IPC file + buffer.

        Args:
            event_type: Filter by event_type (e.g. "instrument.read").
            role: Filter by instrument role (checks role,
                  instrument_role, and channel_id prefix).

        Returns:
            List of event dicts, oldest first.
        """
        import json

        events: list[dict] = []

        # Read flushed IPC data
        table = read_ipc_batches(self._ipc.path)
        if table is not None:
            json_col = table.column("json")
            type_col = table.column("event_type")
            for j in range(table.num_rows):
                if event_type and type_col[j].as_py() != event_type:
                    continue
                try:
                    evt = json.loads(json_col[j].as_py())
                except (json.JSONDecodeError, TypeError):
                    continue
                if role and not event_matches_role(evt, role):
                    continue
                events.append(evt)

        # Include unflushed buffer
        for row in self._ipc.buffer:
            json_str = row.get("json")
            if not json_str:
                continue
            if event_type and row.get("event_type") != event_type:
                continue
            try:
                evt = json.loads(str(json_str))
            except (json.JSONDecodeError, TypeError):
                continue
            if role and not event_matches_role(evt, role):
                continue
            events.append(evt)

        return events

    def close(self) -> None:
        """Close all subscribers, flush buffer, close the Arrow IPC file."""
        for sub in self._subscribers:
            try:
                sub.close()
            except Exception as exc:
                warnings.warn(
                    f"EventSubscriber '{sub.format_name}' close failed: {exc}",
                    stacklevel=2,
                )
        self._ipc.close()
