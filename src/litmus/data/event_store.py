"""Storage-agnostic event API.

Callers use ``emit()``, ``events()``, ``on_event()`` — never see paths,
files, or SQL.  Storage details (Arrow IPC files, DuckDB queries)
are internal.

Dual-write pattern: emit() writes Arrow IPC file (crash safety) and
pushes to in-memory DuckDB daemon via Flight (immediate queryability).

Usage::

    from litmus.data.event_store import EventStore

    store = EventStore()          # resolves storage location automatically
    store.emit(some_event)        # write + notify in-process subscribers
    store.events(role="dmm")     # query
    unsub = store.on_event(cb)   # catch-up subscription
    unsub()                       # stop receiving
    store.close()
"""

from __future__ import annotations

import json as json_mod
import logging
import threading
import warnings
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data import duckdb_manager
from litmus.data._duckdb_flight_server import FlightPutStream
from litmus.data._event_filters import event_matches_role
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_log import EventLog
from litmus.data.events import EventBase

logger = logging.getLogger(__name__)


def _parse_event_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed event dict from a DB row, falling back to the raw row."""
    json_str = row.get("json")
    if json_str:
        try:
            return json_mod.loads(json_str)
        except (json_mod.JSONDecodeError, TypeError):
            pass
    return row


def _parse_timestamp(ts: object) -> datetime | None:
    """Parse a timestamp value to datetime. Returns None if unparseable."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None


def _is_before(evt: dict, cutoff: datetime) -> bool:
    """Return True if the event's timestamp is before ``cutoff``."""
    ts = evt.get("received_at") or evt.get("occurred_at")
    if ts is None:
        return False
    parsed = _parse_timestamp(ts)
    return parsed is not None and parsed < cutoff


class _Subscription:
    """Internal subscription record."""

    __slots__ = ("callback", "event_type", "role", "run_id", "session_id", "since")

    def __init__(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
        since: datetime | None = None,
    ) -> None:
        self.callback = callback
        self.event_type = event_type
        self.role = role
        self.session_id = session_id
        self.run_id = run_id
        self.since = since

    def matches(self, evt: dict) -> bool:
        if self.event_type and evt.get("event_type") != self.event_type:
            return False
        if self.role and not event_matches_role(evt, self.role):
            return False
        if self.session_id and evt.get("session_id") != str(self.session_id):
            return False
        if self.run_id and evt.get("run_id") != str(self.run_id):
            return False
        if self.since and _is_before(evt, self.since):
            return False
        return True


class EventStore:
    """Storage-agnostic event API. Callers never see paths, files, or SQL."""

    _shared: dict[Path, EventStore] = {}

    @classmethod
    def get_shared(cls, data_dir: Path | None = None) -> EventStore:
        """Return a process-wide shared instance for ``data_dir``.

        Multiple callers for the same directory share one watcher thread
        instead of each spawning their own. UI page handlers should use
        this instead of ``EventStore(...)`` so the thread count stays flat.
        """
        key = resolve_data_dir(data_dir)
        if key not in cls._shared:
            cls._shared[key] = cls(_data_dir=key)
        return cls._shared[key]

    def __init__(self, *, _data_dir: Path | None = None) -> None:
        self._data_dir = resolve_data_dir(_data_dir)
        self._events_dir = self._data_dir / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)

        # Start daemon and get gRPC location for Flight queries
        self._location = duckdb_manager.acquire(self._events_dir)

        # Flight query client (shared retry logic with RunStore)
        self._flight = FlightQueryClient(
            self._location,
            "events",
            reacquire=lambda: duckdb_manager.acquire(self._events_dir),
            label="EventStore",
        )

        # Persistent do_put stream (for writes)
        self._put_stream = FlightPutStream(self._location, "events", "events")

        # Internal writer per session (created lazily via get_event_log)
        self._event_logs: dict[UUID, EventLog] = {}

        # In-process subscriptions
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.RLock()

        # Track event IDs delivered in-process to avoid duplicate delivery
        # from the cross-process watcher. Bounded LRU (FIFO eviction) so
        # long-running orchestrators don't leak memory; the watcher polls
        # every 500ms with a ``received_at`` cursor, so old IDs are never
        # re-fetched and don't need to be retained.
        self._delivered_ids: OrderedDict[str, None] = OrderedDict()
        self._delivered_ids_max = 10000

        # Cross-process watcher
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop = threading.Event()

    def _flight_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts.

        Retry policy lives in :class:`FlightQueryClient` —
        TRANSIENT errors retry with exponential backoff, PERMANENT
        errors (Binder, Catalog, syntax) raise immediately as
        :class:`FlightPermanentError`.
        """
        return self._flight.query(sql)

    def _flight_put(self, batch: pa.RecordBatch) -> None:
        """Push an Arrow batch to the daemon via persistent do_put stream.

        Does not block for server confirmation — call ``_drain_puts()``
        before querying for read-after-write consistency.
        """
        try:
            self._put_stream.write(batch)
        except Exception as exc:
            # Non-fatal: data is in IPC file, daemon will rebuild on restart
            warnings.warn(f"Flight put failed (non-fatal): {exc}", stacklevel=2)

    # -- Write path ----------------------------------------------------------

    def _dispatch_to_subscribers(self, evt_dict: dict) -> None:
        """Dispatch an event dict to matching subscribers (caller holds lock)."""
        for sub in self._subscriptions:
            if sub.matches(evt_dict):
                try:
                    sub.callback(evt_dict)
                except Exception as exc:
                    warnings.warn(
                        f"Event subscriber failed: {exc}",
                        stacklevel=2,
                    )

    def _notify_subscribers(self, event: EventBase) -> None:
        """Notify in-process subscribers about a new event."""
        evt_dict = event.model_dump(mode="json")
        event_id = str(event.id)
        with self._lock:
            self._delivered_ids[event_id] = None
            while len(self._delivered_ids) > self._delivered_ids_max:
                self._delivered_ids.popitem(last=False)
            self._dispatch_to_subscribers(evt_dict)

    def get_event_log(self, session_id: UUID) -> EventLog:
        """Get or create an EventLog for a session.

        The returned EventLog is wired to notify this store's subscribers
        on every emit, bridging per-session writes to store-level subscriptions.
        """
        if session_id not in self._event_logs:
            self._event_logs[session_id] = EventLog(
                self._events_dir,
                session_id,
                on_emit=self._notify_subscribers,
                on_flush=self._flight_put,
            )
        return self._event_logs[session_id]

    def emit(self, event: EventBase, *, session_id: UUID | None = None) -> None:
        """Buffer event for batched IPC write, push to DuckDB on flush.

        Dual-write: Arrow IPC file for crash safety, Flight do_put for
        queryability. Both writes are batched at flush threshold — same
        pattern as ChannelStore.
        """
        sid = session_id or getattr(event, "session_id", None)
        if sid is None:
            raise ValueError("Event must have session_id or pass session_id to emit()")

        log = self.get_event_log(sid)
        log.emit(event)

        # Note: _notify_subscribers is called by EventLog.on_emit callback,
        # and _flight_put is called by EventLog.on_flush callback.

    def flush(self) -> None:
        """Flush all buffered events to IPC files and Flight.

        Call this after emitting events that must be visible to other
        processes immediately (e.g., sync events). Drains the
        persistent do_put stream too so the events daemon has acked
        every batch before flush returns — without that drain, IPC is
        on disk but the events daemon's table may not yet contain the
        rows that subsequent cross-process queries / subscribers
        depend on.
        """
        for log in list(self._event_logs.values()):
            log.flush()
        try:
            self._put_stream.drain()
        except Exception as exc:  # noqa: BLE001 — drain is best-effort; data is already in IPC
            logger.debug("put-stream drain failed (non-fatal): %s", exc)

    # -- Read path -----------------------------------------------------------

    def events(
        self,
        *,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
        event_type: str | None = None,
        role: str | None = None,
        since: datetime | None = None,
        until: str | None = None,
        until_event_number: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Query events from the DuckDB index via Flight.

        ``limit`` pushes the row cap into the SQL so the daemon
        returns at most ``limit`` rows instead of streaming the
        full event log over Flight. Critical for projects with
        large IPC histories — without it, even a "show me the
        latest 100 events" page pulls millions of rows.

        ``limit`` is applied to the **most recent** rows (the SQL
        sorts ``received_at DESC`` under the limit, then re-sorts
        ASC for the caller). ``None`` returns all matching rows.
        """
        # Flush any buffered events to IPC + Flight before querying
        # (on_flush callback pushes batches to Flight automatically)
        for log in list(self._event_logs.values()):
            log.flush()
        try:
            self._put_stream.drain()
        except Exception as exc:  # noqa: BLE001
            logger.debug("put-stream drain failed (non-fatal): %s", exc)
        # Build SQL via f-string — safe because inputs are typed:
        # session_id is UUID (validated by caller), event_type is a known
        # enum string, since is a datetime. sql_escape guards against quotes.
        # Flight do_get does not support parameterized queries.
        conditions: list[str] = []
        if session_id:
            conditions.append(f"session_id = '{_sql_escape(str(session_id))}'")
        if run_id:
            conditions.append(f"run_id = '{_sql_escape(str(run_id))}'")
        if event_type:
            conditions.append(f"event_type = '{_sql_escape(event_type)}'")
        if since:
            conditions.append(f"received_at >= '{_sql_escape(since.isoformat())}'")
        if until:
            conditions.append(f"received_at <= '{_sql_escape(until)}'")
        if until_event_number is not None:
            conditions.append(f"event_number <= {int(until_event_number)}")
        if role:
            # Mirrors :func:`event_matches_role` in SQL: ``role``,
            # ``instrument_role``, and the ``channel_id`` prefix all
            # qualify. All three columns are promoted typed columns
            # so the daemon plans an index/columnar scan on each
            # branch — no per-row Python parse.
            r = _sql_escape(role)
            conditions.append(
                f"(role = '{r}' OR instrument_role = '{r}' OR channel_id LIKE '{r}.%')"
            )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        # Narrow projection — the typed payload columns exist purely
        # to push down WHERE filters; their values are duplicates of
        # fields inside ``json``, and parsing JSON reconstitutes the
        # full event. ``SELECT *`` would ship every typed column
        # back over Flight (mostly NULL) and inflate row size by 22
        # extra strings. Envelope-only keeps the network payload
        # constant regardless of how many typed columns we add later.
        projection = (
            "id, event_type, event_number, occurred_at, received_at, session_id, run_id, json"
        )

        # Latest-N pushdown: SQL sorts received_at DESC under the
        # LIMIT (cheap with the index on received_at), then re-sorts
        # ASC in the outer SELECT so the caller still sees
        # chronological order. Without this, "show me the last 100
        # events" pulls every event over Flight.
        if limit is not None and limit > 0:
            sql = f"""
                SELECT * FROM (
                    SELECT {projection}
                    FROM events
                    {where}
                    ORDER BY received_at DESC
                    LIMIT {int(limit)}
                )
                ORDER BY received_at ASC
            """
        else:
            sql = f"""
                SELECT {projection}
                FROM events
                {where}
                ORDER BY received_at ASC
            """
        rows = self._flight_query(sql)

        return [_parse_event_row(row) for row in rows]

    def sessions(self) -> list[dict]:
        """List known sessions with metadata from SessionStarted events."""
        return self.events(event_type="session.started")

    def events_for_unmaterialized_runs(
        self,
        *,
        since: datetime | None = None,
        until: str | None = None,
        until_event_number: int | None = None,
    ) -> list[dict]:
        """Return events for runs that have ``RunStarted`` but no ``RunMaterialized``.

        The dual of "runs whose derived view is durable in a query-optimized
        backend." A run is either:

        * **Materialized** — a ``RunMaterialized`` event exists. The run is
          durably stored somewhere (today: parquet ingested into the runs
          daemon's ``runs_materialized`` table; future: Postgres, Snowflake,
          etc.). The materializer pool no longer needs to track it.
        * **Unmaterialized** — no ``RunMaterialized`` yet. The run's events
          are persisted in the EventStore but no derived view exists. The
          materializer pool tracks it via per-run accumulator state.

        Called by the runs daemon on attach to replay the unmaterialized set
        into its in-memory accumulator pool. The replay set is naturally
        bounded by the rate at which runs are materialized; in production
        steady state, it's the count of currently-in-flight runs plus a
        small tail of just-finished runs awaiting materialization.

        ``since`` further bounds the result to events received after that
        timestamp.
        """
        for log in list(self._event_logs.values()):
            log.flush()
        try:
            self._put_stream.drain()
        except Exception as exc:  # noqa: BLE001
            logger.debug("put-stream drain failed (non-fatal): %s", exc)
        clauses: list[str] = []
        if since:
            clauses.append(f"received_at >= '{_sql_escape(since.isoformat())}'")
        if until:
            clauses.append(f"received_at <= '{_sql_escape(until)}'")
        if until_event_number is not None:
            clauses.append(f"event_number <= {int(until_event_number)}")
        where_extra = (" AND " + " AND ".join(clauses)) if clauses else ""
        # Envelope-only projection — see ``events()`` for the rationale.
        sql = f"""
            SELECT id, event_type, event_number, occurred_at, received_at,
                   session_id, run_id, json
            FROM events
            WHERE run_id IS NOT NULL
              AND run_id IN (
                SELECT DISTINCT run_id FROM events WHERE event_type = 'run.started'
                EXCEPT
                SELECT DISTINCT run_id FROM events WHERE event_type = 'run.materialized'
              ){where_extra}
            ORDER BY event_number ASC
        """
        rows = self._flight_query(sql)
        return [_parse_event_row(row) for row in rows]

    # -- Watch path ----------------------------------------------------------

    def on_event(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
        since: datetime | None = None,
        replay: str = "matching",
    ) -> Callable[[], None]:
        """Catch-up subscription.

        Replays past events on attach, then pushes new ones as they arrive.

        ``replay`` controls the catch-up strategy:

        * ``"matching"`` (default) — replay every event matching the
          ``event_type`` / ``role`` / ``session_id`` / ``since`` filters.
        * ``"unmaterialized_runs"`` — replay events for runs with
          ``RunStarted`` but no ``RunMaterialized``. The materializer's
          replay set: every run still tracked in-memory because its
          derived view hasn't been written yet. See
          :meth:`events_for_unmaterialized_runs`.
        * ``"none"`` — skip replay entirely; deliver only future events.

        In-process: instant dispatch on ``emit()``.
        Cross-process: internal polling detects new data via DuckDB index.

        Returns an unsubscribe callable.
        """
        # Capture the watcher's cursor BEFORE running replay. The cursor
        # is a monotonic ``event_number`` (insert-order sequence stamped
        # by the events daemon's ``nextval('event_seq')`` under the
        # same lock as the INSERT). Replay covers
        # ``event_number <= snapshot``; watcher polls
        # ``event_number > snapshot``. ``event_number`` is bulletproof
        # against the wall-clock race ``received_at`` had — two
        # put-hook batches under the same lock can finish out of order
        # vs. their transaction-start timestamps, but ``nextval()``
        # advances under the lock and is strictly monotonic with
        # commit order.
        try:
            cursor_rows = self._flight_query("SELECT MAX(event_number) AS m FROM events")
            cursor_event_number = (
                int(cursor_rows[0]["m"]) if cursor_rows and cursor_rows[0].get("m") else 0
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cursor snapshot before replay failed: %s", exc)
            cursor_event_number = 0

        # Register the subscription and start the watcher BEFORE running
        # replay. New events arriving from the cursor onwards flow through
        # the watcher into the callback immediately. Historical (pre-cursor)
        # events flow through replay in a background thread.
        #
        # Why async replay: when the events DB has accumulated many
        # unmaterialized runs (e.g., a long-running dev environment with
        # historical test cruft), synchronous replay can take seconds-to-
        # minutes before the watcher gets started. New events emitted
        # during that window aren't visible to the subscriber until
        # replay completes — which can cause test timeouts and operator
        # UI staleness on cold daemon start. Decoupling lets the live
        # path keep up with real-time events while the materializer
        # catches up on backlog in the background.
        #
        # ``_delivered_ids`` deduplicates between the two paths: each
        # dispatched event id is stamped, and both paths check before
        # delivering, so each event reaches the callback exactly once
        # regardless of which path saw it first.
        sub = _Subscription(
            callback,
            event_type=event_type,
            role=role,
            session_id=session_id,
            run_id=run_id,
            since=since,
        )
        with self._lock:
            self._subscriptions.append(sub)

        self._ensure_watcher(initial_cursor=cursor_event_number)

        if replay != "none":
            # Replay covers event_number <= cursor (inclusive); watcher
            # covers event_number > cursor. No boundary overlap (replay
            # is ``<=``, watcher is ``>``) — ``_delivered_ids`` still
            # dedups any in-process emit that raced replay.
            replay_until_event_number = cursor_event_number

            def _replay_in_background() -> None:
                if replay == "unmaterialized_runs":
                    existing = self.events_for_unmaterialized_runs(
                        until_event_number=replay_until_event_number,
                    )
                else:
                    existing = self.events(
                        session_id=session_id,
                        run_id=run_id,
                        event_type=event_type,
                        role=role,
                        since=since,
                        until_event_number=replay_until_event_number,
                    )
                # Dispatch each event to the new subscriber's callback,
                # then stamp ``_delivered_ids`` so the watcher dedups
                # against the boundary on its first poll. Mirrors the
                # sync-replay behaviour, just in a background thread so
                # the watcher can serve real-time events concurrently.
                for evt in existing:
                    try:
                        callback(evt)
                        evt_id = str(evt.get("id") or "")
                        if evt_id:
                            with self._lock:
                                self._delivered_ids[evt_id] = None
                                while len(self._delivered_ids) > self._delivered_ids_max:
                                    self._delivered_ids.popitem(last=False)
                    except Exception as exc:  # noqa: BLE001
                        warnings.warn(
                            f"Event subscriber failed during replay: {exc}",
                            stacklevel=2,
                        )

            threading.Thread(
                target=_replay_in_background,
                daemon=True,
                name=f"event-replay-{replay}",
            ).start()

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscriptions.remove(sub)
                except ValueError:
                    pass

        return unsubscribe

    def _ensure_watcher(self, *, initial_cursor: int = 0) -> None:
        """Start the cross-process file watcher thread if not already running.

        ``initial_cursor`` seeds ``_watch_loop`` to a known
        ``event_number`` from the caller — the monotonic insert-order
        sequence stamped by the events daemon under the put-hook lock.
        The watcher polls ``WHERE event_number > initial_cursor`` so
        any event with a higher position is picked up. Position values
        are strictly monotonic with commit order, so there's no
        boundary race against wall-clock ordering.
        """
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return

        self._watcher_stop.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            args=(initial_cursor,),
            daemon=True,
            name="litmus-event-watcher",
        )
        self._watcher_thread.start()

    def _watch_loop(self, initial_cursor: int = 0) -> None:
        """Receive cross-process events via a held-open push stream (no poll).

        Opens a ``__SUBSCRIBE__`` do_get on the events daemon: the server
        replays every row past the cursor from the warm index, then pushes
        each new row as it lands — the blocking iterator is woken by the
        server, so there is no 500ms floor.

        Each delivered row carries ``event_number``; the highest seen is
        the reconnection cursor. If the daemon dies or restarts, the
        reader re-acquires the daemon location and re-subscribes from that
        cursor — the server's replay fills the downtime gap, so delivery
        is lossless across restarts (overlap deduped by ``_delivered_ids``).

        ``initial_cursor`` is the ``on_event`` snapshot: history at or
        below it is delivered by the background replay there; this stream
        carries everything strictly past it.
        """
        # Ticket: ``events\0__SUBSCRIBE__\0<cursor>`` — see DuckDBFlightServer.
        last_event_number: int = initial_cursor
        location = self._location
        backoff = 0.1
        while not self._watcher_stop.is_set():
            client: flight.FlightClient | None = None
            try:
                client = flight.connect(location)
                ticket = flight.Ticket(f"events\0__SUBSCRIBE__\0{int(last_event_number)}".encode())
                reader = client.do_get(ticket)
                backoff = 0.1  # connected — reset backoff
                for chunk in reader:
                    if self._watcher_stop.is_set():
                        break
                    for row in chunk.data.to_pylist():
                        last_event_number = self._deliver_watched_row(row, last_event_number)
            except Exception as exc:  # noqa: BLE001 — reconnect on any stream error
                if self._watcher_stop.is_set():
                    break
                logger.debug("Event subscription dropped (will reconnect): %s", exc)
                try:
                    location = duckdb_manager.acquire(self._events_dir)
                except Exception as racq:  # noqa: BLE001 — keep old location, retry
                    logger.debug("Re-acquire events daemon failed: %s", racq)
                self._watcher_stop.wait(timeout=backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001 — best-effort close
                        pass

    def _deliver_watched_row(self, row: dict, last_event_number: int) -> int:
        """Dispatch one pushed/replayed event row; return the advanced cursor.

        Deduped against in-process delivery (``_notify_subscribers``) and
        replay by event id, so each event reaches subscribers once.
        """
        evt = _parse_event_row(row)
        event_id = str(evt.get("id") or row.get("id", ""))
        try:
            with self._lock:
                if event_id not in self._delivered_ids:
                    self._delivered_ids[event_id] = None
                    while len(self._delivered_ids) > self._delivered_ids_max:
                        self._delivered_ids.popitem(last=False)
                    self._dispatch_to_subscribers(evt)
        except Exception as exc:  # noqa: BLE001 — never let one bad dispatch kill the watcher
            logger.debug("Watcher dispatch failed for event id=%s: %s", event_id, exc)
        en = row.get("event_number")
        if en is not None:
            return max(last_event_number, int(en))
        return last_event_number

    # -- Lifecycle -----------------------------------------------------------

    @property
    def events_dir(self) -> Path:
        """Internal events directory (for backwards compat during migration)."""
        return self._events_dir

    def close(self) -> None:
        """Stop watchers, release resources. Safe to call multiple times."""
        self._watcher_stop.set()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=2.0)
            self._watcher_thread = None

        # Close event logs — their on_flush callback pushes final batches to Flight
        for log in list(self._event_logs.values()):
            try:
                log.close()
            except Exception as exc:
                warnings.warn(f"EventLog close failed: {exc}", stacklevel=2)
        self._event_logs.clear()

        try:
            self._put_stream.close()
        except Exception as exc:
            warnings.warn(f"FlightPutStream close failed: {exc}", stacklevel=2)

        with self._lock:
            self._subscriptions.clear()
            self._delivered_ids.clear()

        try:
            self._flight.close()
        except Exception as exc:
            warnings.warn(f"FlightQueryClient close failed: {exc}", stacklevel=2)
        try:
            duckdb_manager.release(self._events_dir)
        except Exception as exc:
            warnings.warn(f"duckdb_manager.release failed: {exc}", stacklevel=2)
