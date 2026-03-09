"""Arrow Flight server and persistent-stream client for DuckDB.

Server: wraps multiple named DuckDB connections, serves SQL queries
via do_get, accepts Arrow inserts via do_put with per-batch acks.

Client: ``FlightPutStream`` keeps a single gRPC stream open across
writes, avoiding per-call setup overhead (~1.6ms → ~0.02ms per write).
Each write blocks until the server confirms the INSERT via the
metadata ack channel.

Ticket format: ``db_name\0SQL``
do_put descriptor format: ``db_name\0table_name``
"""

from __future__ import annotations

import threading
import warnings
from collections.abc import Callable

import duckdb
import pyarrow as pa
import pyarrow.flight as flight

_ACK = b"\x01"


# ---------------------------------------------------------------------------
# Client: persistent do_put stream with per-batch acks
# ---------------------------------------------------------------------------

class FlightPutStream:
    """Persistent do_put stream with deferred delivery guarantees.

    Keeps a single gRPC/HTTP2 stream open across writes, avoiding
    ~1.6ms per-call setup overhead. ``write()`` sends a batch without
    waiting (~0.01ms). ``drain()`` blocks until the server has confirmed
    all pending INSERTs via the metadata ack channel — call before
    querying for read-after-write consistency.

    Thread-safe via internal lock.
    """

    def __init__(self, location: str, db_name: str, table_name: str) -> None:
        self._location = location
        self._command = f"{db_name}\0{table_name}".encode()
        self._client: flight.FlightClient | None = None
        self._writer: flight.FlightStreamWriter | None = None
        self._reader: flight.FlightMetadataReader | None = None
        self._pending_acks: int = 0
        self._lock = threading.Lock()

    def write(self, batch: pa.RecordBatch) -> None:
        """Write a batch to the persistent stream. Does not wait for ack."""
        with self._lock:
            try:
                writer = self._writer
                if writer is None:
                    client = flight.connect(self._location)
                    self._client = client
                    descriptor = flight.FlightDescriptor.for_command(self._command)
                    writer, reader = client.do_put(descriptor, batch.schema)
                    self._writer = writer
                    self._reader = reader
                writer.write_batch(batch)
                self._pending_acks += 1
            except Exception:
                self._reset()
                raise

    def drain(self) -> None:
        """Block until all pending writes are confirmed by the server."""
        with self._lock:
            if self._reader is None or self._pending_acks == 0:
                return
            try:
                for _ in range(self._pending_acks):
                    self._reader.read()
                self._pending_acks = 0
            except Exception:
                self._reset()
                raise

    def close(self) -> None:
        """Close the stream and client."""
        with self._lock:
            self._reset()

    def _reset(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as exc:
                warnings.warn(f"FlightPutStream: failed to close writer: {exc}", stacklevel=2)
            self._writer = None
        self._reader = None
        self._pending_acks = 0
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                warnings.warn(f"FlightPutStream: failed to close client: {exc}", stacklevel=2)
            self._client = None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class DuckDBFlightServer(flight.FlightServerBase):
    """Flight server that routes SQL queries to named DuckDB connections.

    Each connection is registered under a name (e.g. "events", "runs").
    Tickets use ``name\\0SQL`` format to select the target database.
    do_put descriptors use ``name\\0table_name`` to insert Arrow batches.
    Server sends a metadata ack after each batch is committed.
    """

    def __init__(self, location: str = "grpc://127.0.0.1:0") -> None:
        super().__init__(location)
        self._databases: dict[str, duckdb.DuckDBPyConnection] = {}
        self._lock = threading.Lock()
        self._put_hooks: dict[str, Callable[[pa.Table], None]] = {}

    def register(self, name: str, conn: duckdb.DuckDBPyConnection) -> None:
        """Register a named DuckDB connection."""
        self._databases[name] = conn

    def register_put_hook(
        self, db_name: str, hook: Callable[[pa.Table], None]
    ) -> None:
        """Register a custom do_put handler for a database name.

        The hook receives the Arrow table and is responsible for inserting
        it into DuckDB. Used by the runs daemon to read parquet files
        from paths sent via do_put.
        """
        self._put_hooks[db_name] = hook

    def do_get(
        self,
        context: flight.ServerCallContext,
        ticket: flight.Ticket,
    ) -> flight.RecordBatchStream:
        """Execute SQL query from ticket, return Arrow stream.

        Ticket format: ``db_name\\0SQL``
        """
        raw = ticket.ticket.decode("utf-8")
        if "\0" not in raw:
            raise flight.FlightServerError(
                f"Invalid ticket format — expected 'db_name\\0SQL', got: {raw[:80]}"
            )
        db_name, sql = raw.split("\0", 1)
        conn = self._databases.get(db_name)
        if conn is None:
            raise flight.FlightServerError(f"Unknown database: {db_name!r}")

        with self._lock:
            result = conn.execute(sql).fetch_arrow_table()

        return flight.RecordBatchStream(result)

    def do_put(
        self,
        context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
        reader: flight.MetadataRecordBatchReader,
        writer: flight.FlightMetadataWriter,
    ) -> None:
        """Insert Arrow batches into a named table.

        Processes batches incrementally as they arrive from the client,
        supporting both one-shot and persistent-stream patterns.
        Sends a metadata ack after each batch is committed.

        Descriptor command format: ``db_name\\0table_name``
        """
        raw = descriptor.command.decode("utf-8")
        if "\0" not in raw:
            raise flight.FlightServerError(
                f"Invalid descriptor — expected 'db_name\\0table_name', got: {raw[:80]}"
            )
        db_name, table_name = raw.split("\0", 1)

        hook = self._put_hooks.get(db_name)
        conn = self._databases.get(db_name) if hook is None else None
        if hook is None and conn is None:
            raise flight.FlightServerError(f"Unknown database: {db_name!r}")

        # Process batches as they arrive (streaming-compatible)
        while True:
            try:
                batch, _ = reader.read_chunk()
            except StopIteration:
                break

            table = pa.Table.from_batches([batch])
            with self._lock:
                if hook is not None:
                    hook(table)
                elif conn is not None:
                    conn.register("_put_batch", table)
                    conn.execute(
                        f"INSERT INTO {table_name} BY NAME SELECT * FROM _put_batch"
                    )
                    conn.unregister("_put_batch")

            # Ack: batch committed, safe to query
            writer.write(pa.py_buffer(_ACK))
