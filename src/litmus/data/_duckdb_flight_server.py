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

import collections
import logging
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

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

# Bounded per-subscriber buffer depth.
_SUB_QUEUE_MAX = 10_000


def _parse_subscribe(qs: str) -> tuple[int, bool, dict[str, str]]:
    """Parse a ``__SUBSCRIBE__`` options querystring into (cursor, conflate,
    predicates).

    One urlencoded querystring carries every subscribe option. Reserved control
    keys: ``cursor`` (replay position, int) and ``conflate`` (``latest`` → keep
    only the newest batch — a gauge). Every other key is an equality filter
    predicate (``channel_id=…``, ``event_type=…``). Empty → no replay, no
    conflation, no filter (the whole db's live stream)."""
    parsed = {k: v[0] for k, v in parse_qs(qs, keep_blank_values=True).items() if v}
    cursor = 0
    raw_cursor = parsed.pop("cursor", "")
    if raw_cursor:
        try:
            cursor = int(raw_cursor)
        except ValueError:
            cursor = 0
    conflate = parsed.pop("conflate", "") == "latest"
    return cursor, conflate, parsed


def _apply_filter(table: pa.Table, predicates: dict[str, str]) -> pa.Table | None:
    """Rows of ``table`` matching ALL equality predicates, or ``None`` if none.

    Empty predicates returns ``table`` unchanged — the no-filter path costs
    nothing (the events materializer, a channels ``*`` wildcard). A predicate on
    an absent column matches no rows. The all-match and no-match cases skip the
    pyarrow ``filter`` copy, so a channels single-``channel_id`` batch keeps or
    skips whole."""
    if not predicates:
        return table
    keep = [True] * table.num_rows
    for col, val in predicates.items():
        if col not in table.column_names:
            return None
        values = table.column(col).to_pylist()
        keep = [k and v == val for k, v in zip(keep, values, strict=True)]
    matched = sum(keep)
    if matched == 0:
        return None
    if matched == table.num_rows:
        return table
    return table.filter(pa.array(keep, type=pa.bool_()))


class _SubscriberBuffer:
    """Per-subscriber batch buffer for a live ``__SUBSCRIBE__`` stream.

    ``drain`` returns ALL queued batches at once, so a lagging consumer catches
    up in one read (drain-coalesce — the LMAX effect). Overflow behavior is
    derived from whether the subscription is replay-backed:

    - **lossless** (the db registered ``replay_sql``): on overflow ``put``
      signals removal — the stream ends and the client reconnects + replays
      from its cursor (events → runs).
    - **lossy** (no ``replay_sql``): on overflow drop the oldest batch + count a
      gap, keep the subscriber; the consumer re-syncs from the durable store
      (channels / files frames — live = from-now). The in-memory drop never
      touches the durable record.
    """

    def __init__(
        self,
        *,
        lossy: bool,
        maxsize: int = _SUB_QUEUE_MAX,
        predicates: dict[str, str] | None = None,
        conflate: bool = False,
    ) -> None:
        self._lossy = lossy
        self._max = maxsize
        self._predicates = predicates or {}
        self._conflate = conflate
        self._batches: collections.deque[pa.RecordBatch] = collections.deque()
        self._cond = threading.Condition()
        self._gaps = 0
        self._closed = False

    @property
    def predicates(self) -> dict[str, str]:
        """Server-side equality filter for this subscription ({} = all rows)."""
        return self._predicates

    @property
    def gaps(self) -> int:
        """Batches dropped under lossy overflow — the gap signal a consumer sees."""
        return self._gaps

    def put(self, batch: pa.RecordBatch) -> bool:
        """Enqueue one batch. Returns ``False`` iff the subscriber should be
        removed (lossless overflow), ``True`` otherwise."""
        with self._cond:
            if self._closed:
                return True
            if self._conflate:
                # Gauge: keep only the newest batch. Intentional, not overflow —
                # no gap count (channels' LATEST policy).
                self._batches.clear()
                self._batches.append(batch)
            elif len(self._batches) >= self._max:
                if not self._lossy:
                    return False  # lossless: drop batch + drop subscriber (→ replay)
                self._batches.append(batch)
                self._batches.popleft()
                self._gaps += 1
            else:
                self._batches.append(batch)
            self._cond.notify()
            return True

    def drain(self, timeout: float) -> list[pa.RecordBatch] | None:
        """Block up to ``timeout`` for batches; return all queued at once, an
        empty list on timeout, or ``None`` once closed and drained."""
        with self._cond:
            if not self._batches and not self._closed:
                self._cond.wait(timeout)
            if self._closed and not self._batches:
                return None
            out = list(self._batches)
            self._batches.clear()
            return out

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# Client: persistent do_put stream with per-batch acks
# ---------------------------------------------------------------------------


# Cap on the held un-acked resend buffer. Past this the oldest batch is
# dropped from the buffer — it is already durable in the writer's IPC file,
# so the daemon's startup sweep re-ingests it; this only bounds the
# in-memory resend window, it never loses data.
_MAX_UNACKED_BATCHES = 256


class FlightPutStream:
    """Persistent do_put stream with deferred delivery + self-healing writes.

    Keeps a single gRPC/HTTP2 stream open across writes, avoiding
    ~1.6ms per-call setup overhead. ``write()`` sends a batch without
    waiting (~0.01ms). ``drain()`` blocks until the server has confirmed
    all pending INSERTs via the metadata ack channel — call before
    querying for read-after-write consistency.

    Resilience (only on the failure path — the normal write/drain flow is
    untouched): if the daemon is killed mid-stream (e.g. an upgrade where a
    newer client restarts an older daemon), ``write``/``drain`` reacquire a
    fresh daemon via ``reacquire`` and **resend** the un-acked batches. The
    resend is safe because the events insert is ``ON CONFLICT (id) DO
    NOTHING`` — rows the dead daemon already committed are no-ops. This is
    the writer-side mirror of the query client's reacquire-and-retry.

    Thread-safe via internal lock.
    """

    def __init__(
        self,
        location: str,
        db_name: str,
        table_name: str,
        *,
        reacquire: Callable[[], str] | None = None,
    ) -> None:
        self._location = location
        self._command = f"{db_name}\0{table_name}".encode()
        self._reacquire = reacquire
        self._client: flight.FlightClient | None = None
        self._writer: flight.FlightStreamWriter | None = None
        self._reader: flight.FlightMetadataReader | None = None
        self._schema: pa.Schema | None = None
        self._pending_acks: int = 0
        # Batches written since the last successful drain, kept ONLY for the
        # failure path: resend them to a fresh daemon after a kill. Capped;
        # dropped-oldest entries stay durable in the IPC file.
        self._unacked: list[pa.RecordBatch] = []
        self._lock = threading.Lock()

    def write(self, batch: pa.RecordBatch) -> None:
        """Write a batch to the persistent stream. Does not wait for ack."""
        with self._lock:
            if self._schema is None:
                self._schema = batch.schema
            self._unacked.append(batch)
            if len(self._unacked) > _MAX_UNACKED_BATCHES:
                del self._unacked[0]  # oldest is durable in IPC; bound memory
            try:
                self._ensure_open_locked()
                if self._writer is not None:
                    self._writer.write_batch(batch)
                    self._pending_acks += 1
            except (OSError, flight.FlightError, pa.ArrowException):
                # Daemon may be gone — reacquire a fresh one and resend the
                # un-acked window so this batch isn't stranded in IPC-only.
                if not self._recover_locked():
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
                self._unacked.clear()
            except (OSError, flight.FlightError, pa.ArrowException):
                # Daemon died mid-drain — reacquire, resend, confirm once more.
                if self._recover_locked():
                    try:
                        for _ in range(self._pending_acks):
                            self._reader.read()
                        self._pending_acks = 0
                        self._unacked.clear()
                        return
                    except (OSError, flight.FlightError, pa.ArrowException):
                        pass
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
            self._unacked.clear()
            self._reset()

    # -- internals (caller holds self._lock) --------------------------------

    def _ensure_open_locked(self) -> None:
        """Open the stream against the current location if not already open."""
        if self._writer is not None or self._schema is None:
            return
        client = flight.connect(self._location)
        self._client = client
        descriptor = flight.FlightDescriptor.for_command(self._command)
        self._writer, self._reader = client.do_put(descriptor, self._schema)

    def _recover_locked(self) -> bool:
        """Reacquire a fresh daemon and resend the un-acked window.

        Returns ``True`` if the resend landed on a live daemon (acks now
        pending), ``False`` if recovery isn't possible (no reacquire
        callback, or the fresh daemon is also unreachable) — the caller
        then falls back to IPC durability.
        """
        if self._reacquire is None:
            self._reset()
            return False
        self._reset()  # drop the broken stream; KEEP _unacked for resend
        try:
            self._location = self._reacquire()
            self._ensure_open_locked()
            if self._writer is None:
                return False
            for b in self._unacked:
                self._writer.write_batch(b)
            self._pending_acks = len(self._unacked)
            return True
        except (OSError, flight.FlightError, pa.ArrowException):
            self._reset()
            return False

    def _reset(self) -> None:
        """Drop the broken stream/client. Keeps ``_unacked`` for resend.

        Close failures are expected here (``_reset`` runs when the stream is
        already broken), so they log at debug rather than warn.
        """
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as exc:  # noqa: BLE001 — cleanup: drop the broken stream regardless
                logging.getLogger(__name__).debug("FlightPutStream: close writer failed: %s", exc)
            self._writer = None
        self._reader = None
        self._pending_acks = 0
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001 — cleanup: drop the broken client regardless
                logging.getLogger(__name__).debug("FlightPutStream: close client failed: %s", exc)
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
        # against the Flight server's query handlers on DuckDB's
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
        self._query_hooks: dict[str, Callable[[str], pa.Table]] = {}
        # Live push: per-db subscriber queues + the Arrow schema each
        # subscription stream yields. A ``do_get`` with the
        # ``__SUBSCRIBE__`` ticket registers a queue here; ``_publish``
        # (called from ``do_put`` after each insert) fans new rows out
        # to them. No polling — the subscriber's generator blocks on its
        # queue. Guarded by its own lock, independent of the DB lock, so
        # fan-out never holds up DuckDB access.
        self._sub_lock = threading.Lock()
        self._subscribers: dict[str, list[_SubscriberBuffer]] = {}
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

    def _publish(self, db_name: str, table: pa.Table) -> None:
        """Fan newly-inserted rows out to live subscribers of ``db_name``.

        Called from ``do_put`` after each batch commits. Non-blocking, never
        stalls the writer. Per-subscriber overflow behavior is set by the buffer
        (lossless → drop the subscriber so it reconnects + replays; lossy → drop
        oldest + count a gap, keep it). No-op when nobody is subscribed.
        """
        with self._sub_lock:
            subs = self._subscribers.get(db_name)
            if not subs:
                return
            dead: list[_SubscriberBuffer] = []
            for buf in subs:
                matched = _apply_filter(table, buf.predicates)
                if matched is None:
                    continue  # no rows for this subscriber's filter
                for b in matched.to_batches():
                    if not buf.put(b):
                        dead.append(buf)
                        break
            for buf in dead:
                subs.remove(buf)

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

    def register_query_hook(self, db_name: str, hook: Callable[[str], pa.Table]) -> None:
        """Register a custom do_get handler for a database name (read-side
        parallel to :meth:`register_put_hook`).

        When set, a ``do_get`` ticket ``db_name\\0<payload>`` routes ``<payload>``
        to the hook — which parses it however the store wants (NOT necessarily
        SQL) and returns an Arrow table to stream back — instead of executing it
        as DuckDB SQL. This lets a store whose read is a typed verb
        (range / last-N / decimate / discovery) rather than SQL-over-one-table
        serve through the shared server, and such a db need not register a DuckDB
        connection at all. ``__SUBSCRIBE__`` still takes precedence; opt-in, so
        plain query-only dbs never call this.
        """
        self._query_hooks[db_name] = hook

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
            # __SUBSCRIBE__\0<options-querystring> — one querystring carries the
            # cursor, conflate, and filter predicates (all optional).
            qs = sql.split("\0", 1)[1] if "\0" in sql else ""
            cursor, conflate, predicates = _parse_subscribe(qs)
            return self._do_subscribe(db_name, cursor, predicates, conflate)

        # Typed read hook (range / last-N / decimate / discovery) — the store
        # parses the payload itself, so a query-hooked db need not register a
        # DuckDB connection. Checked before the SQL path.
        query_hook = self._query_hooks.get(db_name)
        if query_hook is not None:
            return flight.RecordBatchStream(query_hook(sql))

        conn = self._databases.get(db_name)
        if conn is None:
            raise flight.FlightServerError(f"Unknown database: {db_name!r}")

        if self._parallel:
            # Lock-free: per-thread cursor reads an MVCC snapshot,
            # unaffected by a concurrent write on another cursor.
            cursor = self._cursor_for(conn)
            result = cursor.execute(sql).to_arrow_table()
            return flight.RecordBatchStream(result)

        with self._lock:
            result = conn.execute(sql).to_arrow_table()

        return flight.RecordBatchStream(result)

    def _do_subscribe(
        self,
        db_name: str,
        cursor: int = 0,
        predicates: dict[str, str] | None = None,
        conflate: bool = False,
    ) -> flight.GeneratorStream:
        """Open a held-open push stream for ``db_name`` (no polling).

        Lossless catch-up + live, in one stream:

        1. Register the queue FIRST, so any row committed from here on is
           captured live (no gap).
        2. If the db has a replay template, stream the backlog past
           ``cursor`` from the warm index.
        3. Then block on the queue and yield live rows as ``publish`` fans
           them in.

        ``predicates`` is the per-subscription server-side equality filter
        ({} = every row); it is applied to both the replay backlog and the live
        fan-out so the consumer only ever receives its matching rows.

        A row committed between the snapshot and registration appears in
        both the replay and the live queue — the client dedups by id, so
        delivery is at-least-once and gap-free.
        """
        schema = self._subscribe_schemas.get(db_name)
        if schema is None:
            raise flight.FlightServerError(f"Database {db_name!r} does not support subscriptions")
        replay_sql = self._subscribe_replay.get(db_name)
        predicates = predicates or {}
        # Overflow behavior follows the recovery capability: a replay-backed db
        # (has replay_sql) is lossless (drop-subscriber → client replays); one
        # without is lossy (drop-oldest + gap, recover from the durable store).
        # ``conflate`` (channels' LATEST gauge) keeps only the newest batch.
        buf = _SubscriberBuffer(lossy=replay_sql is None, predicates=predicates, conflate=conflate)
        with self._sub_lock:
            self._subscribers.setdefault(db_name, []).append(buf)
        conn = self._databases.get(db_name)

        def _generate():  # type: ignore[no-untyped-def]
            try:
                if replay_sql is not None and conn is not None:
                    try:
                        sql = replay_sql.format(cursor=int(cursor))
                        # Stream the backlog in bounded chunks via a
                        # RecordBatchReader instead of materializing the
                        # whole result — replay memory stays flat no matter
                        # how far behind the cursor is (scale-to-full-disk).
                        rcur = self._cursor_for(conn) if self._parallel else conn
                        reader = rcur.execute(sql).fetch_record_batch()
                        while True:
                            try:
                                replayed = reader.read_next_batch()
                            except StopIteration:
                                break
                            matched = _apply_filter(pa.table(replayed), predicates)
                            if matched is not None:
                                yield from matched.to_batches()
                    except Exception as exc:  # noqa: BLE001 — replay failure must not kill live
                        logging.getLogger(__name__).warning(
                            "subscribe replay for %s failed: %s", db_name, exc
                        )
                while True:
                    # Drain-coalesce: one read returns every queued batch, so a
                    # lagging consumer catches up in a single pass.
                    batches = buf.drain(1.0)
                    if batches is None:
                        break
                    yield from batches
            finally:
                with self._sub_lock:
                    subs = self._subscribers.get(db_name, [])
                    try:
                        subs.remove(buf)
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
            raise flight.FlightServerError(
                f"Unknown database: {db_name!r} "
                f"(registered hooks={sorted(self._put_hooks)}, dbs={sorted(self._databases)})"
            )

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

        Each open subscription holds a generator blocked on its buffer;
        ``FlightServerBase.shutdown()`` would wait on those in-flight
        ``do_get`` RPCs forever. ``close()`` every subscriber buffer so the
        generators break and the streams close, then shut the server down.
        """
        with self._sub_lock:
            for subs in self._subscribers.values():
                for buf in subs:
                    buf.close()
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
    put_hook: Callable[[pa.Table], pa.Table | None] | None,
    port_file_name: str,
    thread_name: str,
    extra_setup: Callable[[DuckDBFlightServer], None] | None = None,
    pre_ready: Callable[[], None] | None = None,
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
    # Register every extra db / hook (e.g. the files frames fan-out) BEFORE the
    # serve thread starts and ``write_ready`` fires — otherwise a client that
    # connects the instant the daemon is "ready" can do_put to a not-yet-
    # registered db and get "Unknown database". The server must never accept
    # connections in a half-configured state.
    if extra_setup is not None:
        extra_setup(server)

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
