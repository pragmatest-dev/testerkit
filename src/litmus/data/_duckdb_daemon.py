"""DuckDB event index daemon.

Spawned as a detached process by ``DuckDBDaemonManager.acquire()``.
Maintains a persistent DuckDB index rebuilt incrementally from Arrow IPC
files. Clients push new events via ``do_put`` and query via ``do_get``.

Startup is O(new files since last run): the daemon opens the existing
``_index.duckdb``, signals ready immediately, then ingests only files not
yet recorded in the ``_ingested`` table via a background thread.

Usage: python -m litmus.data._duckdb_daemon <events_dir>
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from litmus.data._duckdb_flight_server import (
    DuckDBFlightServer,
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data._ipc_writer import read_ipc_batches
from litmus.data._session_reaper import reap_abandoned_sessions
from litmus.data.duckdb_manager import DuckDBDaemonManager
from litmus.data.events import TYPED_PAYLOAD_COLUMNS
from litmus.data.schema_dispatch import (
    SchemaVersionRefused,
    dispatch,
    stamp_from_arrow_metadata,
)
from litmus.data.schema_versions import SchemaStore

logger = logging.getLogger(__name__)

# Session-reaper cadence: a short bootstrap wait (let the ingest thread populate
# the table) then a periodic backstop. The daemon idles at 300s, so this fires a
# couple of times per daemon lifetime — most reaps happen on the NEXT spin.
_REAPER_BOOTSTRAP_DELAY = 5.0
_REAPER_INTERVAL = 60.0


def _json_is_derived(payload: str | None) -> bool:
    """True if an event's ``json`` payload is spine-derived (reaper close,
    materializer completion) — exempt from the terminal fence."""
    if not payload:
        return False
    try:
        return bool(json.loads(payload).get("derived"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return False


def _fence_post_seal(table: pa.Table, sealed: set[str]) -> tuple[pa.Table, int]:
    """Drop post-seal PRODUCER writes — rows whose session is already sealed
    (has a ``SessionEnded``) and which are NOT ``derived``. Revival is rejected;
    daemon completions (a run's async ``RunMaterialized``, a reaper ``RunEnded``)
    ride through. Cheap fast-path: returns the table untouched unless some row
    actually targets a sealed session (only then is the ``json`` parsed)."""
    if not sealed:
        return table, 0
    sids = table.column("session_id").to_pylist()
    if not any(s in sealed for s in sids):
        return table, 0
    jsons = table.column("json").to_pylist()
    keep = [not (s in sealed and not _json_is_derived(j)) for s, j in zip(sids, jsons, strict=True)]
    rejected = keep.count(False)
    if rejected == 0:
        return table, 0
    return table.filter(keep), rejected


# Columns to narrow an IPC-loaded Arrow table to before inserting. The
# IPC file's schema may be wider than what we INSERT — this narrows it to
# only the columns the daemon's events table cares about. The extra
# ``received_at`` column in the loaded table is carried but unreferenced
# (the daemon's ``now()`` overrides it on insert).
_EVENT_COLUMNS_FROM_IPC: list[str] = [
    "id",
    "event_type",
    "occurred_at",
    "received_at",
    "session_id",
    "run_id",
    "writer_key",
    "event_offset",
    "json",
    *TYPED_PAYLOAD_COLUMNS,
]

# ON CONFLICT DO NOTHING deduplicates events that arrived via do_put during
# the previous session and are now also present in the IPC files.
#
# Three columns answer three different questions:
#
# * ``occurred_at`` (in the JSON payload + top-level column) — client
#   wall-clock at event construction. The "when did this happen in the
#   source" answer for displays, analytics, time-bucket queries.
# * ``received_at`` — server-stamped via ``now()`` here. The trustworthy
#   "when did the daemon see it" answer for retention, staleness
#   detection, and any time-window operator query that can't trust
#   client clocks.
# * ``event_number`` — server-stamped via ``nextval('event_seq')`` here.
#   The "what commit-order did this land in" answer for the watcher's
#   cursor and replay ordering. Strictly monotonic with INSERT order
#   under the put-hook lock, so the ``event_number > last`` cursor never
#   advances past a row that hasn't yet been inserted. ``received_at``
#   was previously the cursor too, but ``now()`` (transaction-start
#   timestamp) has subtle wall-clock ordering issues across concurrent
#   put-hook batches even with the server lock, while ``nextval``
#   advances under the same lock as the INSERT and is bulletproof.
# Vectorized insert: register the Arrow batch as a view and
# ``INSERT ... SELECT`` it in ONE columnar statement. DuckDB is a
# columnar engine — row-by-row ``executemany`` against the VARCHAR
# primary-key index runs ~800x slower (measured 763 vs 617k rows/s on
# the same batch). ``received_at`` / ``event_number`` are server-stamped
# in the SELECT (now() + nextval); ``ON CONFLICT (id) DO NOTHING`` keeps
# re-ingest idempotent. Columns are referenced by name, so a wider IPC
# table (it also carries ``received_at``) is fine — the extra column is
# unreferenced. The typed payload columns trail the envelope; adding one
# is a single edit in ``events.py``.
_TYPED_COLS_SQL = ", ".join(TYPED_PAYLOAD_COLUMNS)
_INSERT_COLUMNS = (
    "id, event_type, occurred_at, received_at, event_number, "
    f"session_id, run_id, writer_key, event_offset, json, {_TYPED_COLS_SQL}"
)
_SELECT_EXPRS = (
    "id, event_type, occurred_at, now(), nextval('event_seq'), "
    f"session_id, run_id, writer_key, event_offset, json, {_TYPED_COLS_SQL}"
)


def _insert_sql(view: str) -> str:
    """Vectorized ``INSERT INTO events SELECT ... FROM <view>`` statement."""
    return (
        f"INSERT INTO events ({_INSERT_COLUMNS}) "
        f"SELECT {_SELECT_EXPRS} FROM {view} "
        "ON CONFLICT (id) DO NOTHING"
    )


def _insert_events(cur: duckdb.DuckDBPyConnection, table: pa.Table, *, attempts: int = 25) -> None:
    """Register ``table`` and insert it vectorized, retrying on conflicts.

    Lock-free writes (the put-hook on a per-Flight-thread cursor and the
    ingest thread on its own cursor) append to ``events`` concurrently
    under DuckDB's MVCC; the loser of a concurrent commit raises
    ``TransactionException``. Retry is safe and idempotent — the INSERT
    is ``ON CONFLICT (id) DO NOTHING``, so re-running rows already
    committed by the prior attempt is a no-op (it only burns ``event_seq``
    values; gaps in ``event_number`` are fine — only monotonicity matters,
    not density). The view name carries the thread id so concurrent
    cursors never clobber each other's registration.
    """
    view = f"_ins_{threading.get_ident()}"
    sql = _insert_sql(view)
    for i in range(attempts):
        try:
            cur.register(view, table)
            try:
                cur.execute(sql)
            finally:
                cur.unregister(view)
            return
        except duckdb.TransactionException:
            if i == attempts - 1:
                raise
            time.sleep(0.002 * (i + 1))


# ── Schema management ────────────────────────────────────────────────


def _open_index(index_path: Path) -> duckdb.DuckDBPyConnection:
    """Open the persistent DuckDB index and idempotently align the schema.

    No version checks, no drop-and-recreate. ``_ensure_schema`` uses
    ``CREATE TABLE IF NOT EXISTS`` and ``ALTER TABLE ADD COLUMN IF NOT
    EXISTS`` so adding a new column to the code below auto-migrates
    existing DBs on next spawn — no re-ingest, no version bump.
    """
    conn = duckdb.connect(str(index_path))
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Idempotently align the on-disk schema with the code.

    Critical: ``ORDER BY received_at DESC LIMIT N`` (the latest-N
    pattern the events page uses) is a full table scan + sort
    without an index on ``received_at``. With ~millions of rows /
    ~GB of data, that's ~10s per query — exactly the page-load
    pain operators hit.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id VARCHAR PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            event_number BIGINT,
            occurred_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ,
            session_id VARCHAR,
            run_id VARCHAR,
            writer_key VARCHAR,
            event_offset BIGINT,
            json VARCHAR
        )
    """)
    for col, sql_type in _EVENTS_COLUMNS:
        conn.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} {sql_type}")
    # Monotonic insert-order sequence. The watcher streams by
    # ``event_number > last`` which is bulletproof against the
    # wall-clock races that ``received_at >=`` had: ``received_at`` is
    # stamped via ``now()`` at transaction start, but multiple
    # concurrent put-hook batches could (in observed traces) finish
    # out of order vs. their stamped time. ``nextval()`` advances
    # under the same lock as the INSERT, so event_number is strictly
    # monotonic with commit order.
    conn.execute("CREATE SEQUENCE IF NOT EXISTS event_seq START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _ingested (
            path VARCHAR PRIMARY KEY,
            mtime DOUBLE NOT NULL,
            size BIGINT NOT NULL,
            row_count BIGINT NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL DEFAULT 'ok',
            error VARCHAR,
            last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_events_received_at ON events(received_at)",
        "CREATE INDEX IF NOT EXISTS idx_events_number ON events(event_number)",
        "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)",
        # Typed-payload-column indexes. Cover the high-traffic filters
        # (operator pages, role-scoped queries, pass/fail dashboards).
        # Low-cardinality enums (reason, format, dialog_type,
        # response_type) skip the index — DuckDB columnar scans are
        # cheap on them.
        "CREATE INDEX IF NOT EXISTS idx_events_channel_id ON events(channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_outcome ON events(outcome)",
        "CREATE INDEX IF NOT EXISTS idx_events_uut_serial_number ON events(uut_serial_number)",
        "CREATE INDEX IF NOT EXISTS idx_events_role ON events(role)",
        "CREATE INDEX IF NOT EXISTS idx_events_instrument_role ON events(instrument_role)",
        "CREATE INDEX IF NOT EXISTS idx_events_step_name ON events(step_name)",
        "CREATE INDEX IF NOT EXISTS idx_events_measurement_name ON events(measurement_name)",
    ):
        conn.execute(index_sql)


# Columns that should exist on ``events`` regardless of when the
# on-disk DB was created. Drives ``ALTER TABLE ADD COLUMN IF NOT
# EXISTS`` for upgrade migration; ``CREATE TABLE IF NOT EXISTS``
# above covers the fresh case. Adding a typed payload column is a
# single edit in ``events.TYPED_PAYLOAD_COLUMNS`` — it flows through
# both the IPC schema and this migration list automatically.
_EVENTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "VARCHAR"),
    ("event_type", "VARCHAR"),
    ("occurred_at", "TIMESTAMPTZ"),
    ("received_at", "TIMESTAMPTZ"),
    ("event_number", "BIGINT"),
    ("session_id", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("writer_key", "VARCHAR"),
    ("event_offset", "BIGINT"),
    ("json", "VARCHAR"),
    *((col, "VARCHAR") for col in TYPED_PAYLOAD_COLUMNS),
)


# ── Background ingest ────────────────────────────────────────────────


def _ingest_ipc_files(
    conn: duckdb.DuckDBPyConnection,
    events_dir: Path,
    lock: threading.Lock,
) -> None:
    """Background thread: ingest new/changed IPC files into the events index.

    Uses the daemon's main DuckDB connection — protected by ``lock`` —
    for the same reason the runs daemon does: a single connection +
    single lock eliminates the catalog-lock deadlock that two-connection
    ingest exposes under GIL contention. Per-file ingest releases the
    lock between files so Flight queries can interleave.
    """
    disk_entries: list[tuple[str, float, int, os.stat_result]] = []
    for fpath in sorted(events_dir.glob("*/*.arrow")):
        try:
            stat = fpath.stat()
            disk_entries.append((str(fpath), stat.st_mtime, stat.st_size, stat))
        except OSError:
            continue

    if not disk_entries:
        return

    with lock:
        ingested_keys: set[tuple[str, float, int]] = {
            (row[0], row[1], row[2])
            for row in conn.execute("SELECT path, mtime, size FROM _ingested").fetchall()
        }
    stat_map = {e[0]: e[3] for e in disk_entries}
    needs_ingest = [e[0] for e in disk_entries if (e[0], e[1], e[2]) not in ingested_keys]
    for path_str in needs_ingest:
        stat = stat_map.get(path_str)
        if stat is None:
            continue
        try:
            with lock:
                _ingest_one_file(conn, Path(path_str), stat)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"Ingest skipped {path_str}: {exc}", stacklevel=2)


def _ingest_one_file(
    conn: duckdb.DuckDBPyConnection,
    fpath: Path,
    stat: os.stat_result,
) -> None:
    """Ingest a single IPC file. Records status in _ingested."""
    path_str = str(fpath)

    def _mark(status: str, error: str | None = None, row_count: int = 0) -> None:
        conn.execute(
            "INSERT INTO _ingested (path, mtime, size, row_count, status, error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?, ?, now()) "
            "ON CONFLICT (path) DO UPDATE SET "
            "mtime=excluded.mtime, size=excluded.size, row_count=excluded.row_count, "
            "status=excluded.status, error=excluded.error, last_attempt=now()",
            [path_str, stat.st_mtime, stat.st_size, row_count, status, error],
        )

    table = read_ipc_batches(fpath)
    if table is None:
        warnings.warn(f"Skipping unreadable IPC file: {fpath.name}", stacklevel=2)
        _mark("quarantined", "no valid batches")
        return

    # Whitelist-dispatch both of events' coordinates (§1/§3) before ingest. Read
    # the stamps off the file's schema metadata (present on the table returned by
    # read_ipc_batches, before the column-select below drops it). Unknown/absent
    # versions quarantine, keeping the ingest thread alive.
    try:
        meta = table.schema.metadata
        dispatch(SchemaStore.EVENTS_ENVELOPE, stamp_from_arrow_metadata(meta))
        dispatch(
            SchemaStore.EVENT_CATALOG,
            stamp_from_arrow_metadata(meta, key=b"event_catalog_version"),
        )
    except SchemaVersionRefused as exc:
        warnings.warn(f"Skipping unsupported schema in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))
        return

    try:
        table = table.select(_EVENT_COLUMNS_FROM_IPC)
    except (KeyError, pa.ArrowInvalid) as exc:
        warnings.warn(f"Skipping bad schema in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))
        return

    try:
        _insert_events(conn, table)
        _mark("ok", row_count=table.num_rows)
    except (duckdb.Error, UnicodeDecodeError, pa.ArrowException, ValueError) as exc:
        # ``UnicodeDecodeError`` happens when an IPC file has a torn /
        # partial string buffer (mid-write crash, FS bit-flip).
        # ``pa.ArrowException`` covers other arrow decode failures.
        # Quarantining keeps the ingest thread alive — a single bad
        # file can otherwise stop ALL future event ingestion (was
        # observed in pytest suite runs: events daemon's ingest
        # thread died on first 0xff byte, runs daemon's subscriber
        # then saw zero new events for the rest of the session).
        warnings.warn(f"Skipping bad data in {fpath.name}: {exc}", stacklevel=2)
        _mark("quarantined", str(exc))


# ── Daemon entry point ───────────────────────────────────────────────


def daemon_run(events_dir: Path) -> None:
    """Entry point for the events daemon process. Blocks until idle timeout.

    Ready-ordering: signal ready BEFORE background ingest. Events are
    write-heavy and callers emit new events immediately — blocking on
    a historical replay of potentially hundreds of IPC files would
    blow the 10 s spawn deadline. The runs daemon inverts this on a
    fresh/rebuild start because its first query is typically
    ``list_runs()``, which needs the index populated.
    """
    mgr = DuckDBDaemonManager(events_dir)

    index_path = events_dir / "_index.duckdb"
    conn = _open_index(index_path)

    # Fully lock-free (``parallel=True``): reads AND writes run on
    # per-thread cursors under DuckDB MVCC, no Python mutex. Events
    # qualify because one batch = one atomic ``executemany`` (invariants
    # rule E1) — there is no multi-statement unit to protect. Concurrent
    # appends from sibling cursors are reconciled by ``_insert_events``'s
    # ``TransactionException`` retry.
    srv_cell: dict[str, DuckDBFlightServer] = {}
    _put_tls = threading.local()

    # Terminal fence — sealed session_ids (a SessionEnded landed). Loaded from the
    # durable index at startup; each incoming SessionEnded adds to it. A post-seal
    # producer write is rejected; daemon completions (derived) ride through.
    _sealed_sessions: set[str] = set()
    _sealed_lock = threading.Lock()
    try:
        for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM events "
            "WHERE event_type = 'session.ended' AND session_id IS NOT NULL"
        ).fetchall():
            _sealed_sessions.add(str(sid))
    except duckdb.Error:
        pass

    def _events_cursor() -> duckdb.DuckDBPyConnection:
        # One write cursor per Flight handler thread, reused across calls.
        cur = getattr(_put_tls, "cur", None)
        if cur is None:
            cur = conn.cursor()
            _put_tls.cur = cur
        return cur

    def _events_put_hook(table: pa.Table) -> pa.Table | None:
        # Vectorized register + INSERT ... SELECT (columnar, ~800x faster
        # than the old row-by-row executemany; no large-string segfault in
        # DuckDB >= 1.5).
        cur = _events_cursor()
        # Terminal fence: reject post-seal producer writes (revival) before they
        # land. Fast-path returns the table untouched unless a row targets a
        # sealed session. Snapshot the set under the lock only when there's a hit.
        with _sealed_lock:
            sealed = set(_sealed_sessions) if _sealed_sessions else None
        if sealed is not None:
            table, rejected = _fence_post_seal(table, sealed)
            if rejected:
                logger.info("Terminal fence rejected %d post-seal event(s)", rejected)
            if table.num_rows == 0:
                return None
        _insert_events(cur, table)
        # Absorb any SessionEnded in this batch into the sealed set so later
        # writes to that session are fenced.
        ets = table.column("event_type").to_pylist()
        if "session.ended" in ets:
            sids = table.column("session_id").to_pylist()
            with _sealed_lock:
                _sealed_sessions.update(
                    str(s) for e, s in zip(ets, sids, strict=True) if e == "session.ended" and s
                )
        srv = srv_cell.get("s")
        if srv is None or not srv.has_subscribers("events"):
            return None
        # A live subscriber is attached: fan out exactly THIS batch's rows
        # (selected by id, robust against concurrent writers on sibling
        # cursors) stamped with their server-side event_number, so push is
        # gap-free and the client's reconnect cursor advances.
        view = f"_pub_ids_{threading.get_ident()}"
        cur.register(view, table.select(["id"]))
        canonical = cur.execute(
            f"SELECT e.* FROM events e WHERE e.id IN (SELECT id FROM {view}) "
            "ORDER BY e.event_number"
        ).to_arrow_table()
        cur.unregister(view)
        return canonical

    def _register_events_sub(server: DuckDBFlightServer) -> None:
        # Enable lossless push: a __SUBSCRIBE__ stream replays every events
        # row past the caller's event_number cursor, then streams live rows
        # (handed in by _events_put_hook). Schema = the events table.
        # Registered via extra_setup so the subscribe surface is live BEFORE
        # the daemon accepts connections — a subscriber that connects the
        # instant it's ready must not race the registration.
        sub_schema = conn.execute("SELECT * FROM events LIMIT 0").to_arrow_table().schema
        server.register_subscribe_schema(
            "events",
            sub_schema,
            replay_sql="SELECT * FROM events WHERE event_number > {cursor} ORDER BY event_number",
        )

    server, port_file, _location = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=events_dir,
        db_name="events",
        conn=conn,
        put_hook=_events_put_hook,
        port_file_name="_duckdb_flight_port",
        thread_name="duckdb-flight",
        extra_setup=_register_events_sub,
        parallel=True,
    )
    srv_cell["s"] = server

    # Ingest IPC files that aren't yet in the index via a background
    # thread on its OWN cursor (concurrent with the put-hook cursors;
    # _insert_events retry reconciles append conflicts). The lock is a
    # private, uncontended guard for ingest's own sequential ops.
    threading.Thread(
        target=_ingest_ipc_files,
        args=(conn.cursor(), events_dir, threading.Lock()),
        daemon=True,
        name="duckdb-ingest",
    ).start()

    # Session reaper — derive abandonment from the spine (recency vs the will)
    # and emit synthetic SessionEnded. Stateless: re-derives from the durable
    # table every iteration, so a daemon spin (incl. this startup) catches
    # sessions abandoned while no daemon ran.
    reaper_stop = threading.Event()

    def _reaper_loop() -> None:
        reaper_stop.wait(_REAPER_BOOTSTRAP_DELAY)  # let ingest populate the table
        cur = conn.cursor()
        while not reaper_stop.is_set():
            try:
                reap_abandoned_sessions(cur, events_dir, now=datetime.now(UTC))
            except Exception as exc:  # noqa: BLE001 — a bad reap must not kill the daemon
                logger.warning("Session reaper iteration failed: %s", exc)
            reaper_stop.wait(_REAPER_INTERVAL)

    threading.Thread(target=_reaper_loop, daemon=True, name="session-reaper").start()

    # Block until idle timeout
    mgr.monitor_refs()

    # Final scan before exit: catch sessions already past their lease that went
    # quiet while other sessions kept the daemon busy (eager vs next-spin).
    reaper_stop.set()
    try:
        reap_abandoned_sessions(conn.cursor(), events_dir, now=datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Shutdown session reap failed: %s", exc)

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
