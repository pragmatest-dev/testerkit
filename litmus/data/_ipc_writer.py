"""Shared buffered Arrow IPC writer.

Both EventLog and ChannelStore's _ChannelWriter use identical
append→threshold→flush→record_batch→clear logic. This module
extracts that pattern into a reusable base.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc


class BufferedIPCWriter:
    """Buffers rows and flushes as multi-row Arrow batches to IPC files.

    Subclasses may override ``_on_flush()`` to add post-flush behavior
    (e.g. segment rotation, Flight push).
    """

    __slots__ = (
        "_buffer",
        "_flush_threshold",
        "_path",
        "_row_count",
        "_schema",
        "_writer",
    )

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        flush_threshold: int = 50,
    ) -> None:
        self._path = path
        self._schema = schema
        self._flush_threshold = flush_threshold
        self._buffer: list[dict[str, object]] = []
        self._writer: ipc.RecordBatchFileWriter | None = None
        self._row_count: int = 0

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
        """Read-only access to the unflushed buffer."""
        return self._buffer

    def _ensure_writer(self) -> ipc.RecordBatchFileWriter:
        if self._writer is None:
            p = self.path  # Use property — subclasses may override (e.g. segments)
            p.parent.mkdir(parents=True, exist_ok=True)
            sink = pa.OSFile(str(p), "wb")
            self._writer = ipc.new_file(sink, self._schema)
        return self._writer

    def append(self, row: dict[str, object]) -> pa.RecordBatch | None:
        """Buffer a row. Returns the flushed batch if threshold was hit."""
        self._buffer.append(row)
        if len(self._buffer) >= self._flush_threshold:
            return self.flush()
        return None

    def flush(self) -> pa.RecordBatch | None:
        """Flush buffered rows to IPC file. Returns the batch, or None."""
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
        """Flush remaining buffer and close the IPC writer. Returns row count."""
        self.flush()
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as exc:
                warnings.warn(f"Failed to close IPC writer: {exc}", stacklevel=2)
            self._writer = None
        return self._row_count


def read_ipc_batches(path: Path) -> pa.Table | None:
    """Read all batches from an Arrow IPC file. Returns None on error."""
    if not path.exists():
        return None
    try:
        reader = ipc.open_file(str(path))
        batches = [reader.get_batch(i) for i in range(reader.num_record_batches)]
        if not batches:
            return None
        return pa.Table.from_batches(batches)
    except (pa.ArrowInvalid, OSError):
        return None
