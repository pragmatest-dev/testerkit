"""Shared buffered Arrow IPC writer.

Both EventLog and ChannelStore's _ChannelWriter use identical
append→threshold→flush→record_batch→clear logic. This module
extracts that pattern into a reusable base.
"""

from __future__ import annotations

import threading
import warnings
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc


class BufferedIPCWriter:
    """Buffers rows and flushes as multi-row Arrow batches to IPC files.

    Flushes when the buffer hits ``flush_threshold`` rows OR when
    ``flush_interval`` seconds have passed since the last append
    (whichever comes first). The time-based flush ensures interactive
    use cases (single instrument reads) don't wait for 50 events.

    Subclasses may override ``_on_flush()`` to add post-flush behavior
    (e.g. segment rotation, Flight push).
    """

    __slots__ = (
        "_buffer",
        "_flush_interval",
        "_flush_threshold",
        "_lock",
        "_path",
        "_row_count",
        "_schema",
        "_timer",
        "_writer",
    )

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        flush_threshold: int = 50,
        flush_interval: float = 1.0,
    ) -> None:
        self._path = path
        self._schema = schema
        self._flush_threshold = flush_threshold
        self._flush_interval = flush_interval
        self._buffer: list[dict[str, object]] = []
        self._writer: ipc.RecordBatchStreamWriter | None = None
        self._row_count: int = 0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def buffer(self) -> list[dict[str, object]]:
        """Thread-safe snapshot of the unflushed buffer."""
        with self._lock:
            return list(self._buffer)

    def _ensure_writer(self) -> ipc.RecordBatchStreamWriter:
        if self._writer is None:
            p = self.path  # Use property — subclasses may override (e.g. segments)
            p.parent.mkdir(parents=True, exist_ok=True)
            sink = pa.OSFile(str(p), "wb")
            self._writer = ipc.new_stream(sink, self._schema)
        return self._writer

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _start_timer(self) -> None:
        self._cancel_timer()
        self._timer = threading.Timer(self._flush_interval, self._timer_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timer_flush(self) -> None:
        """Called by the timer thread — flush under lock."""
        with self._lock:
            self._timer = None
            self._flush_unlocked()

    def append(self, row: dict[str, object]) -> pa.RecordBatch | None:
        """Buffer a row. Returns the flushed batch if threshold was hit."""
        with self._lock:
            self._buffer.append(row)
            if len(self._buffer) >= self._flush_threshold:
                self._cancel_timer()
                return self._flush_unlocked()
            # Schedule a time-based flush if not already pending
            if self._timer is None:
                self._start_timer()
            return None

    def flush(self) -> pa.RecordBatch | None:
        """Flush buffered rows to IPC file. Returns the batch, or None."""
        with self._lock:
            return self._flush_unlocked()

    def _flush_unlocked(self) -> pa.RecordBatch | None:
        """Flush without acquiring lock (caller must hold it)."""
        if not self._buffer:
            return None
        batch = pa.record_batch(
            {col: [r[col] for r in self._buffer] for col in self._schema.names},
            schema=self._schema,
        )
        writer = self._ensure_writer()
        writer.write_batch(batch)
        self._row_count += len(self._buffer)
        self._buffer.clear()
        self._on_flush(batch)
        return batch

    def _on_flush(self, batch: pa.RecordBatch) -> None:
        """Hook called after each flush. Override for post-flush behavior."""

    def close(self) -> int:
        """Flush remaining buffer and close the stream writer."""
        with self._lock:
            self._cancel_timer()
            self._flush_unlocked()
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as exc:  # noqa: BLE001 — cleanup: best-effort writer close
                warnings.warn(f"Failed to close IPC writer: {exc}", stacklevel=2)
            self._writer = None
        return self._row_count


def read_ipc_batches(path: Path) -> pa.Table | None:
    """Read all batches from an Arrow IPC stream. Returns None on error."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        reader = ipc.open_stream(pa.OSFile(str(path), "rb"))
        batches = []
        while True:
            try:
                batch = reader.read_next_batch()
                batches.append(batch)
            except StopIteration:
                break
            except pa.ArrowInvalid:
                break  # Truncated — return complete batches we got
        return pa.Table.from_batches(batches) if batches else None
    except (pa.ArrowInvalid, OSError):
        return None
