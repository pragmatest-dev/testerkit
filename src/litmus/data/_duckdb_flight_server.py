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
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa
import pyarrow.flight as flight

if TYPE_CHECKING:
    from litmus.data._daemon_lifecycle import DaemonManager

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
            except (OSError, flight.FlightError, pa.ArrowException):
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
            except (OSError, flight.FlightError, pa.ArrowException):
                self._reset()
                raise

    def close(self) -> None:
        """Drain pending acks, then close the stream and client."""
        with self._lock:
            if self._reader is not None and self._pending_acks > 0:
                try:
                    for _ in range(self._pending_acks):
                        self._reader.read()
                    self._pending_acks = 0
                except (OSError, flight.FlightError, pa.ArrowException):
                    pass
            self._reset()

    def _reset(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as exc:  # noqa: BLE001 — cleanup: drop the broken stream regardless
                warnings.warn(f"FlightPutStream: failed to close writer: {exc}", stacklevel=2)
            self._writer = None
        self._reader = None
        self._pending_acks = 0
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001 — cleanup: drop the broken client regardless
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

    def __init__(
        self,
        location: str = "grpc://127.0.0.1:0",
        *,
        lock: threading.Lock | None = None,
    ) -> None:
        super().__init__(location)
        self._databases: dict[str, duckdb.DuckDBPyConnection] = {}
        # Optional shared lock — when provided, the daemon's background
        # ingest thread can use the same lock to serialize all DuckDB
        # access on the daemon's main connection. Without this, a
        # background thread opening its own connection deadlocks
        # against the Flight server's pre_query_hook on DuckDB's
        # global catalog lock under GIL contention.
        self._lock = lock if lock is not None else threading.Lock()
        self._put_hooks: dict[str, Callable[[pa.Table], None]] = {}
        self._pre_query_hooks: dict[str, Callable[[duckdb.DuckDBPyConnection], None]] = {}

    def register(self, name: str, conn: duckdb.DuckDBPyConnection) -> None:
        """Register a named DuckDB connection."""
        self._databases[name] = conn

    def register_put_hook(self, db_name: str, hook: Callable[[pa.Table], None]) -> None:
        """Register a custom do_put handler for a database name.

        The hook receives the Arrow table and is responsible for inserting
        it into DuckDB. Used by the runs daemon to read parquet files
        from paths sent via do_put.
        """
        self._put_hooks[db_name] = hook

    def register_pre_query_hook(
        self,
        db_name: str,
        hook: Callable[[duckdb.DuckDBPyConnection], None],
    ) -> None:
        """Register a hook that runs before each ``do_get`` query.

        The hook receives the database's connection and runs under
        the same lock as the query itself. Used by the runs daemon
        to refresh in-memory overlay tables (in-flight runs / steps)
        from the accumulator pool right before the query executes,
        so the UNION views see a current snapshot.
        """
        self._pre_query_hooks[db_name] = hook

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
        pre_query = self._pre_query_hooks.get(db_name)

        with self._lock:
            if pre_query is not None:
                try:
                    pre_query(conn)
                except Exception as exc:  # noqa: BLE001 — never break a query because the hook failed
                    import logging

                    logging.getLogger(__name__).warning(
                        "pre_query_hook for %s failed: %s", db_name, exc
                    )
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
                    conn.execute(f"INSERT INTO {table_name} BY NAME SELECT * FROM _put_batch")
                    conn.unregister("_put_batch")

            # Ack: batch committed, safe to query
            writer.write(pa.py_buffer(_ACK))


# ---------------------------------------------------------------------------
# Daemon-side runtime helpers
# ---------------------------------------------------------------------------


def start_flight_server_in_daemon(
    *,
    mgr: DaemonManager,
    daemon_dir: Path,
    db_name: str,
    conn: duckdb.DuckDBPyConnection,
    put_hook: Callable[[pa.Table], None] | None,
    port_file_name: str,
    thread_name: str,
    pre_ready: Callable[[], None] | None = None,
    pre_query_hook: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
    lock: threading.Lock | None = None,
) -> tuple[DuckDBFlightServer, Path, str]:
    """Start a DuckDBFlightServer inside a daemon process, signal ready.

    Shared scaffolding between the events daemon and the runs daemon.
    Both:

    1. Bind a Flight server on a random localhost port.
    2. Register the daemon's DuckDB connection (and an optional do_put
       hook for tuple-bind / parquet-paths semantics).
    3. Write the bound location to ``daemon_dir / port_file_name`` so
       :meth:`DaemonManager._post_spawn_state` can read it.
    4. Start the serve thread (daemon).
    5. Optionally run ``pre_ready()`` synchronously — used by the runs
       daemon for fresh-rebuild ingest before the first query lands.
    6. Signal ready via ``mgr.write_ready()`` and stamp the location
       into state via ``mgr.update_state(location=...)``.

    Returns ``(server, port_file_path, location)``. Caller is
    responsible for ``server.shutdown()`` and ``port_file.unlink``
    on teardown — see :func:`shutdown_flight_server_in_daemon`.
    """
    server = DuckDBFlightServer("grpc://127.0.0.1:0", lock=lock)
    server.register(db_name, conn)
    if put_hook is not None:
        server.register_put_hook(db_name, put_hook)
    if pre_query_hook is not None:
        server.register_pre_query_hook(db_name, pre_query_hook)

    location = f"grpc://127.0.0.1:{server.port}"
    port_file = daemon_dir / port_file_name
    port_file.write_text(location)

    threading.Thread(target=server.serve, daemon=True, name=thread_name).start()

    if pre_ready is not None:
        pre_ready()

    mgr.write_ready()
    mgr.update_state(location=location)

    return server, port_file, location


def shutdown_flight_server_in_daemon(
    server: DuckDBFlightServer,
    port_file: Path,
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Tear down a Flight server started by :func:`start_flight_server_in_daemon`.

    Stops the server, removes the port file, and closes the DuckDB
    connection. Connection-close errors are warned, not raised — the
    daemon is on its way out either way.
    """
    server.shutdown()
    port_file.unlink(missing_ok=True)
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001 — daemon-shutdown best-effort
        warnings.warn(f"Failed to close DuckDB connection: {exc}", stacklevel=2)
