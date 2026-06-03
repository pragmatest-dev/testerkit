"""Event log writer and subscriber protocol.

The EventLog buffers typed events and flushes them as multi-row Arrow
batches to IPC files via ``BufferedIPCWriter`` — shared infrastructure
with ChannelStore's ``_ChannelWriter``.

Subscribers are dispatched immediately on emit; IPC writes are batched.

Storage: ``results/events/{date}/{session_id}-{pid}[_{segment}].arrow``
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa

from litmus.data._event_filters import event_matches_role
from litmus.data._ipc_writer import BufferedIPCWriter, read_ipc_batches
from litmus.data.events import TYPED_PAYLOAD_COLUMNS, EventBase

# Schema for the index columns stored in IPC files.
#
# Envelope fields (id, event_type, occurred_at, received_at,
# session_id, run_id) and the lossless ``json`` payload always exist.
# In addition, every identifier and name field used for cross-event
# traversal is promoted into its own VARCHAR column — see
# :data:`litmus.data.events.TYPED_PAYLOAD_COLUMNS` for the rationale
# and the list. Promotion duplicates the value (it remains inside
# ``json``) but lets the daemon push WHERE filters down into DuckDB
# instead of returning rows for Python to post-filter.
_IPC_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("event_type", pa.string()),
        ("occurred_at", pa.timestamp("us", tz="UTC")),
        ("received_at", pa.timestamp("us", tz="UTC")),
        ("session_id", pa.string()),
        ("run_id", pa.string()),
        ("json", pa.string()),
        *((col, pa.string()) for col in TYPED_PAYLOAD_COLUMNS),
    ]
)

_DEFAULT_FLUSH_THRESHOLD = 50


_DEFAULT_MAX_ROWS_PER_SEGMENT = 10_000


class _EventIPCWriter(BufferedIPCWriter):
    """IPC writer with segment rotation and an optional post-flush callback.

    Rotation: when a flush brings the cumulative row count for the current
    segment to or above ``max_rows_per_segment``, the stream is closed
    (writing a valid Arrow EOS) and the next flush opens a new segment file.
    Crash loss is bounded to at most ``max_rows_per_segment + flush_threshold - 1``
    rows (one flush batch beyond the threshold before rotation triggers).

    Each process keeps its own writer (path includes PID) so concurrent
    orchestrator + worker processes never clobber each other's streams.
    """

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        flush_threshold: int = 50,
        max_rows_per_segment: int = _DEFAULT_MAX_ROWS_PER_SEGMENT,
        on_flush: Callable[[pa.RecordBatch], None] | None = None,
    ) -> None:
        super().__init__(path, schema, flush_threshold=flush_threshold)
        self._on_flush_cb = on_flush
        self._path_template = path
        self._segment = 0
        self._closed_paths: list[Path] = []
        self._max_rows_per_segment = max_rows_per_segment

    @property
    def path(self) -> Path:
        """Current segment path."""
        if self._segment == 0:
            return self._path_template
        stem = self._path_template.stem
        return self._path_template.with_name(f"{stem}_{self._segment:04d}.arrow")

    @property
    def all_paths(self) -> list[Path]:
        """Closed segment paths (each has a valid Arrow EOS and is fully readable)."""
        return list(self._closed_paths)

    def _on_flush(self, batch: pa.RecordBatch) -> None:
        if self._on_flush_cb is not None:
            try:
                self._on_flush_cb(batch)
            except Exception as exc:
                warnings.warn(f"on_flush callback failed: {exc}", stacklevel=2)
        # Rotate when the current segment hits the row limit.
        if self._max_rows_per_segment > 0 and self._row_count >= self._max_rows_per_segment:
            if self._writer is not None:
                self._closed_paths.append(self.path)
                self._writer.close()
                self._writer = None
                self._segment += 1


class EventSubscriber:
    """Internal base class for event log materializers.

    Used by the ``litmus export`` CLI replay path (per-format
    converters in :mod:`litmus.data.exporters` — csv/json/stdf/hdf5/
    tdms/mdf4/atml). The canonical run materializer (parquet + DuckDB
    index) is now owned by the runs daemon and doesn't go through this
    base class.

    **Not a public extension protocol.** The set of supported formats
    is fixed by the package. Third-party packages should not subclass
    this; adding a new format requires editing
    :mod:`litmus.data.exporters`.
    """

    format_name: str
    event_types: set[type]

    _registry: dict[str, type[EventSubscriber]] = {}
    """Maps format_name → subscriber class. Populated by __init_subclass__."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "format_name") and cls.format_name:
            existing = EventSubscriber._registry.get(cls.format_name)
            if existing is not None and existing is not cls:
                warnings.warn(
                    f"EventSubscriber.format_name={cls.format_name!r} is "
                    f"already registered to {existing.__module__}."
                    f"{existing.__qualname__}; overriding with "
                    f"{cls.__module__}.{cls.__qualname__}",
                    stacklevel=2,
                )
            EventSubscriber._registry[cls.format_name] = cls

    @staticmethod
    def _short_run_id(run_id: UUID | None) -> str:
        """Return the first 8 characters of a run ID, for use in output filenames."""
        return str(run_id)[:8] if run_id else "unknown"

    def open(self) -> None: ...

    def on_event(self, event: EventBase) -> None:  # noqa: ARG002
        raise NotImplementedError

    def close(self) -> None: ...


class EventLog:
    """Buffers events, flushes as batched Arrow IPC writes.

    Files are date-partitioned and scoped to the writing process:
    ``{date}/{session_id}-{pid}.arrow``. Each process gets its own file,
    so concurrent orchestrator + worker processes never clobber each other.
    Large sessions rotate into numbered segments to bound crash loss.
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
            path=date_dir / f"{session_id}-{os.getpid()}.arrow",
            schema=_IPC_SCHEMA,
            flush_threshold=flush_threshold,
            on_flush=on_flush,
        )
        self._subscribers: list[EventSubscriber] = []
        self._failed: set[EventSubscriber] = set()
        # Item 1d: events no longer own a ref dir. Pre-Position-2 events
        # held raw blob payloads and ``save_ref`` wrote them under
        # ``events/{date}/{session_id}_ref/``. Under Position 2 + C-3b
        # all blobs route through FileStore at the verb layer
        # (Context.observe / observer._store_value); events carry the
        # ``file://`` URI string and the EventLog has nothing of its own
        # to claim-check.

    @property
    def path(self) -> Path:
        return self._ipc.path

    def emit(self, event: EventBase) -> pa.RecordBatch | None:
        """Stamp received_at, buffer for IPC, dispatch to subscribers.

        Returns the flushed batch if the buffer hit the threshold, or
        None if the event was just buffered.
        """
        event.received_at = datetime.now(UTC)

        row = {
            "id": str(event.id),
            "event_type": event.event_type,  # type: ignore[attr-defined]
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "session_id": str(event.session_id),
            "run_id": str(event.run_id) if event.run_id else None,
            "json": event.model_dump_json(),
        }
        row.update(event.typed_payload_values())
        batch = self._ipc.append(row)

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
                    f"EventSubscriber '{sub.format_name}' failed on {type(event).__name__}: {exc}",
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
        events: list[dict] = []

        # Read all closed segments (each has a valid Arrow EOS) plus the
        # current (possibly still-open) segment.
        seg_paths = self._ipc.all_paths + [self._ipc.path]
        seen: set[str] = set()
        for seg_path in seg_paths:
            table = read_ipc_batches(seg_path)
            if table is None:
                continue
            json_col = table.column("json")
            type_col = table.column("event_type")
            id_col = table.column("id")
            for j in range(table.num_rows):
                row_id = id_col[j].as_py()
                if row_id in seen:
                    continue
                seen.add(row_id)
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
            if not isinstance(json_str, str) or not json_str:
                continue
            if event_type and row.get("event_type") != event_type:
                continue
            try:
                evt = json.loads(json_str)
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
