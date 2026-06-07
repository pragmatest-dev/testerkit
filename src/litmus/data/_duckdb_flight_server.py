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

import queue
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

# do_get ticket marker (in the SQL slot) that requests a live push
# subscription instead of a one-shot query: ``db_name\0__SUBSCRIBE__``.
# A real SQL query never equals this sentinel.
_SUBSCRIBE = "__SUBSCRIBE__"

# Bounded per-subscriber queue. A subscriber that can't keep up is
# dropped (its stream ends) rather than blocking the publisher — same
# back-pressure policy as the channel Flight server.
_SUB_QUEUE_MAX = 10_000


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
        parallel: bool = False,
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
        # Parallel mode (opt-in, per store): each Flight handler thread
        # runs on its own ``conn.cursor()`` (thread-local) and takes NO
        # ``self._lock`` — concurrent SELECT/INSERT interleave under
        # DuckDB's MVCC instead of a Python mutex. ONLY safe when every
        # multi-statement write is already atomic on its own: the events
        # store qualifies (one ``executemany`` per batch). The runs store
        # does NOT (a run is 4-6 statements across 3 tables) — it stays
        # in locked mode (``parallel=False``) until its per-run
        # transaction lands. See the invariants doc, rule E1 vs R1.
        self._parallel = parallel
        self._tls = threading.local()
        self._put_hooks: dict[str, Callable[[pa.Table], pa.Table | None]] = {}
        self._pre_query_hooks: dict[str, Callable[[duckdb.DuckDBPyConnection], None]] = {}
        # Live push: per-db subscriber queues + the Arrow schema each
        # subscription stream yields. A ``do_get`` with the
        # ``__SUBSCRIBE__`` ticket registers a queue here; ``_publish``
        # (called from ``do_put`` after each insert) fans new rows out
        # to them. No polling — the subscriber's generator blocks on its
        # queue. Guarded by its own lock, independent of the DB lock, so
        # fan-out never holds up DuckDB access.
        self._sub_lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[pa.RecordBatch | None]]] = {}
        self._subscribe_schemas: dict[str, pa.Schema] = {}
        # Optional per-db replay SQL with a ``{cursor}`` placeholder. When
        # set, a new subscriber first receives every row past its cursor
        # from the warm index (gap-free catch-up) before live rows — so
        # push is lossless across the subscribe boundary.
        self._subscribe_replay: dict[str, str] = {}

    def register(self, name: str, conn: duckdb.DuckDBPyConnection) -> None:
        """Register a named DuckDB connection."""
        self._databases[name] = conn

    def register_subscribe_schema(
        self,
        db_name: str,
        schema: pa.Schema,
        *,
        replay_sql: str | None = None,
    ) -> None:
        """Enable live push subscriptions for ``db_name``.

        ``schema`` is what a ``do_get`` subscription stream yields — it
        must match both the replayed rows and the rows handed to
        :meth:`publish`. Without it, a ``__SUBSCRIBE__`` ticket for
        ``db_name`` is rejected (query-only databases don't call this).

        ``replay_sql`` is an optional template with a single ``{cursor}``
        placeholder (an integer). On subscribe the server runs it to
        stream the backlog past the caller's cursor before live rows.
        """
        self._subscribe_schemas[db_name] = schema
        if replay_sql is not None:
            self._subscribe_replay[db_name] = replay_sql

    def has_subscribers(self, db_name: str) -> bool:
        """True if at least one live subscriber is attached to ``db_name``.

        Lets a put-hook skip the cost of building canonical rows to
        publish when nobody is listening.
        """
        with self._sub_lock:
            return bool(self._subscribers.get(db_name))

    def publish(self, db_name: str, table: pa.Table) -> None:
        """Public entry point for :meth:`_publish` (fan rows to subscribers)."""
        self._publish(db_name, table)

    def _publish(self, db_name: str, table: pa.Table) -> None:
        """Fan newly-inserted rows out to live subscribers of ``db_name``.

        Called from ``do_put`` after each batch commits. Non-blocking
        ``put_nowait`` with drop-on-full back-pressure: a subscriber
        that can't keep up is removed and its stream ends, rather than
        stalling the writer. No-op when nobody is subscribed.
        """
        with self._sub_lock:
            subs = self._subscribers.get(db_name)
            if not subs:
                return
            batches = table.to_batches()
            dead: list[queue.Queue[pa.RecordBatch | None]] = []
            for q in subs:
                try:
                    for b in batches:
                        q.put_nowait(b)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                subs.remove(q)

    def register_put_hook(self, db_name: str, hook: Callable[[pa.Table], pa.Table | None]) -> None:
        """Register a custom do_put handler for a database name.

        The hook receives the Arrow table and is responsible for inserting
        it into DuckDB. Used by the runs daemon to read parquet files
        from paths sent via do_put.

        It may RETURN an Arrow table of canonical rows to fan out to live
        subscribers (e.g. the events hook returns the just-inserted rows
        stamped with ``event_number``); returning ``None`` publishes
        nothing. For the no-hook path, the inserted batch is published as-is.
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

    def _cursor_for(self, conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
        """Return this thread's cursor on ``conn``, creating one on first touch.

        A cursor is a cheap handle on the shared connection that carries
        its own session (prepared statements, ``register`` views) while
        sharing the same MVCC store. Each Flight handler thread gets its
        own, so concurrent SELECT/INSERT run without a Python mutex.
        Only used in ``parallel`` mode.
        """
        by_db: dict[int, duckdb.DuckDBPyConnection] = getattr(self._tls, "by_db", {})
        if not by_db:
            self._tls.by_db = by_db
        cursor = by_db.get(id(conn))
        if cursor is None:
            cursor = conn.cursor()
            by_db[id(conn)] = cursor
        return cursor

    def do_get(
        self,
        _context: flight.ServerCallContext,
        ticket: flight.Ticket,
    ) -> flight.RecordBatchStream:
        """Execute SQL query from ticket, OR open a live push subscription.

        Ticket formats:
          * ``db_name\\0SQL`` — one-shot query, returns the result.
          * ``db_name\\0__SUBSCRIBE__`` — held-open stream; the server
            pushes each newly-inserted batch as it lands (no polling).
        """
        raw = ticket.ticket.decode("utf-8")
        if "\0" not in raw:
            raise flight.FlightServerError(
                f"Invalid ticket format — expected 'db_name\\0SQL', got: {raw[:80]}"
            )
        db_name, sql = raw.split("\0", 1)
        if sql == _SUBSCRIBE or sql.startswith(_SUBSCRIBE + "\0"):
            cursor = 0
            if "\0" in sql:
                _, cur_str = sql.split("\0", 1)
                try:
                    cursor = int(cur_str)
                except ValueError:
                    cursor = 0
            return self._do_subscribe(db_name, cursor)

        conn = self._databases.get(db_name)
        if conn is None:
            raise flight.FlightServerError(f"Unknown database: {db_name!r}")
        pre_query = self._pre_query_hooks.get(db_name)

        if self._parallel:
            # Lock-free: per-thread cursor reads an MVCC snapshot,
            # unaffected by a concurrent write on another cursor.
            cursor = self._cursor_for(conn)
            self._run_pre_query(db_name, pre_query, cursor)
            result = cursor.execute(sql).fetch_arrow_table()
            return flight.RecordBatchStream(result)

        with self._lock:
            self._run_pre_query(db_name, pre_query, conn)
            result = conn.execute(sql).fetch_arrow_table()

        return flight.RecordBatchStream(result)

    @staticmethod
    def _run_pre_query(
        db_name: str,
        pre_query: Callable[[duckdb.DuckDBPyConnection], None] | None,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        if pre_query is None:
            return
        try:
            pre_query(conn)
        except Exception as exc:  # noqa: BLE001 — never break a query because the hook failed
            import logging

            logging.getLogger(__name__).warning("pre_query_hook for %s failed: %s", db_name, exc)

    def _do_subscribe(self, db_name: str, cursor: int = 0) -> flight.GeneratorStream:
        """Open a held-open push stream for ``db_name`` (no polling).

        Lossless catch-up + live, in one stream:

        1. Register the queue FIRST, so any row committed from here on is
           captured live (no gap).
        2. If the db has a replay template, stream the backlog past
           ``cursor`` from the warm index.
        3. Then block on the queue and yield live rows as ``publish`` fans
           them in.

        A row committed between the snapshot and registration appears in
        both the replay and the live queue — the client dedups by id, so
        delivery is at-least-once and gap-free.
        """
        schema = self._subscribe_schemas.get(db_name)
        if schema is None:
            raise flight.FlightServerError(f"Database {db_name!r} does not support subscriptions")
        q: queue.Queue[pa.RecordBatch | None] = queue.Queue(maxsize=_SUB_QUEUE_MAX)
        with self._sub_lock:
            self._subscribers.setdefault(db_name, []).append(q)
        replay_sql = self._subscribe_replay.get(db_name)
        conn = self._databases.get(db_name)

        def _generate():  # type: ignore[no-untyped-def]
            try:
                if replay_sql is not None and conn is not None:
                    try:
                        sql = replay_sql.format(cursor=int(cursor))
                        if self._parallel:
                            table = self._cursor_for(conn).execute(sql).fetch_arrow_table()
                        else:
                            with self._lock:
                                table = conn.execute(sql).fetch_arrow_table()
                        yield from table.to_batches()
                    except Exception as exc:  # noqa: BLE001 — replay failure must not kill live
                        import logging

                        logging.getLogger(__name__).warning(
                            "subscribe replay for %s failed: %s", db_name, exc
                        )
                while True:
                    try:
                        batch = q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if batch is None:
                        break
                    yield batch
            finally:
                with self._sub_lock:
                    subs = self._subscribers.get(db_name, [])
                    try:
                        subs.remove(q)
                    except ValueError:
                        pass

        return flight.GeneratorStream(schema, _generate())

    def do_put(
        self,
        _context: flight.ServerCallContext,
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

        # In ``parallel`` mode the write path is lock-free: each Flight
        # handler thread inserts on its own cursor so concurrent writers
        # interleave under DuckDB's MVCC instead of one Python mutex. The
        # hook owns its own thread-local cursor (and the conflict-retry it
        # needs); the no-hook fall-through uses this thread's cursor and a
        # per-thread view name so ``register`` doesn't clobber across
        # threads. Locked mode (runs) keeps the shared conn + lock until
        # its per-run transaction lands.
        parallel = self._parallel
        reg_target = self._cursor_for(conn) if (parallel and conn is not None) else conn
        view_name = f"_put_batch_{threading.get_ident()}" if parallel else "_put_batch"

        while True:
            try:
                batch, _ = reader.read_chunk()
            except StopIteration:
                break

            table = pa.Table.from_batches([batch])
            published: pa.Table | None = None
            if parallel:
                if hook is not None:
                    # Hook returns canonical rows to fan out (events rows
                    # stamped with event_number) or None. It manages its
                    # own thread-local cursor + retry — no lock here.
                    published = hook(table)
                elif reg_target is not None:
                    reg_target.register(view_name, table)
                    reg_target.execute(
                        f"INSERT INTO {table_name} BY NAME SELECT * FROM {view_name}"
                    )
                    reg_target.unregister(view_name)
                    published = table
            else:
                with self._lock:
                    if hook is not None:
                        published = hook(table)
                    elif conn is not None:
                        conn.register("_put_batch", table)
                        conn.execute(f"INSERT INTO {table_name} BY NAME SELECT * FROM _put_batch")
                        conn.unregister("_put_batch")
                        published = table

            # Push the just-committed rows to any live subscribers.
            # No-op unless someone holds a __SUBSCRIBE__ stream for this
            # db. Outside the DB lock so fan-out never blocks writes.
            if published is not None and self._subscribers.get(db_name):
                self._publish(db_name, published)

            # Ack: batch committed, safe to query
            writer.write(pa.py_buffer(_ACK))

    def shutdown(self) -> None:
        """Stop the server, ending any live subscription streams first.

        Each open subscription holds a generator blocked on its queue;
        ``FlightServerBase.shutdown()`` would wait on those in-flight
        ``do_get`` RPCs forever. Push a ``None`` sentinel into every
        subscriber queue so the generators break and the streams close,
        then shut the server down.
        """
        with self._sub_lock:
            for subs in self._subscribers.values():
                for q in subs:
                    try:
                        q.put_nowait(None)
                    except queue.Full:
                        pass
            self._subscribers.clear()
        super().shutdown()


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
    parallel: bool = False,
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
    server = DuckDBFlightServer("grpc://127.0.0.1:0", lock=lock, parallel=parallel)
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
