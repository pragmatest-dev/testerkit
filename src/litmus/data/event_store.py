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

from litmus.data import duckdb_manager
from litmus.data._duckdb_flight_server import FlightPutStream
from litmus.data._event_filters import event_matches_role
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.event_log import EventLog
from litmus.data.events import EventBase
from litmus.data.results_dir import resolve_results_dir

logger = logging.getLogger(__name__)


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

    __slots__ = ("callback", "event_type", "role", "session_id", "since")

    def __init__(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        session_id: UUID | None = None,
        since: datetime | None = None,
    ) -> None:
        self.callback = callback
        self.event_type = event_type
        self.role = role
        self.session_id = session_id
        self.since = since

    def matches(self, evt: dict) -> bool:
        if self.event_type and evt.get("event_type") != self.event_type:
            return False
        if self.role and not event_matches_role(evt, self.role):
            return False
        if self.session_id and evt.get("session_id") != str(self.session_id):
            return False
        if self.since and _is_before(evt, self.since):
            return False
        return True


class EventStore:
    """Storage-agnostic event API. Callers never see paths, files, or SQL."""

    def __init__(self, *, _results_dir: Path | None = None) -> None:
        self._results_dir = resolve_results_dir(_results_dir)
        self._events_dir = self._results_dir / "events"
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
        for log in self._event_logs.values():
            log.flush()
        try:
            self._put_stream.drain()
        except Exception:  # noqa: BLE001 — drain is best-effort; data is already in IPC
            pass

    # -- Read path -----------------------------------------------------------

    def events(
        self,
        *,
        session_id: UUID | None = None,
        event_type: str | None = None,
        role: str | None = None,
        since: datetime | None = None,
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
        for log in self._event_logs.values():
            log.flush()
        try:
            self._put_stream.drain()
        except Exception:
            # Non-fatal: Flight stream may be closed or have no pending data
            pass
        # Build SQL via f-string — safe because inputs are typed:
        # session_id is UUID (validated by caller), event_type is a known
        # enum string, since is a datetime. sql_escape guards against quotes.
        # Flight do_get does not support parameterized queries.
        conditions: list[str] = []
        if session_id:
            conditions.append(f"session_id = '{_sql_escape(str(session_id))}'")
        if event_type:
            conditions.append(f"event_type = '{_sql_escape(event_type)}'")
        if since:
            conditions.append(f"received_at >= '{_sql_escape(since.isoformat())}'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        # Latest-N pushdown: SQL sorts received_at DESC under the
        # LIMIT (cheap with the index on received_at), then re-sorts
        # ASC in the outer SELECT so the caller still sees
        # chronological order. Without this, "show me the last 100
        # events" pulls every event over Flight.
        if limit is not None and limit > 0:
            sql = f"""
                SELECT * FROM (
                    SELECT *
                    FROM events
                    {where}
                    ORDER BY received_at DESC
                    LIMIT {int(limit)}
                )
                ORDER BY received_at ASC
            """
        else:
            sql = f"""
                SELECT *
                FROM events
                {where}
                ORDER BY received_at ASC
            """
        rows = self._flight_query(sql)

        db_events: list[dict] = []
        for row in rows:
            json_str = row.get("json")
            if json_str:
                try:
                    db_events.append(json_mod.loads(json_str))
                except (json_mod.JSONDecodeError, TypeError):
                    db_events.append(row)
            else:
                db_events.append(row)

        # Apply role filter (can't be done in SQL — needs event field inspection)
        if role:
            db_events = [e for e in db_events if event_matches_role(e, role)]

        return db_events

    def sessions(self) -> list[dict]:
        """List known sessions with metadata from SessionStarted events."""
        return self.events(event_type="session.started")

    # -- Watch path ----------------------------------------------------------

    def on_event(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        session_id: UUID | None = None,
        since: datetime | None = None,
    ) -> Callable[[], None]:
        """Catch-up subscription.

        Replays matching events from ``since``, then pushes new ones
        as they arrive.

        In-process: instant dispatch on ``emit()``.
        Cross-process: internal polling detects new data via DuckDB index.

        Returns an unsubscribe callable.
        """
        existing = self.events(
            session_id=session_id,
            event_type=event_type,
            role=role,
            since=since,
        )
        for evt in existing:
            try:
                callback(evt)
            except Exception as exc:
                warnings.warn(
                    f"Event subscriber failed during replay: {exc}",
                    stacklevel=2,
                )

        sub = _Subscription(
            callback,
            event_type=event_type,
            role=role,
            session_id=session_id,
            since=since,
        )
        with self._lock:
            self._subscriptions.append(sub)

        self._ensure_watcher()

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscriptions.remove(sub)
                except ValueError:
                    pass

        return unsubscribe

    def _ensure_watcher(self) -> None:
        """Start the cross-process file watcher thread if not already running."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return

        self._watcher_stop.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="litmus-event-watcher",
        )
        self._watcher_thread.start()

    def _watch_loop(self) -> None:
        """Poll for new events from other processes using Flight queries.

        Skips events already delivered in-process (via ``_notify_subscribers``).
        Parses the ``json`` column so subscribers get full event dicts.

        Wraps each Flight query *and* each subscriber dispatch in a
        try/except so a transient failure (events daemon
        mid-restart, schema not yet created, a single dispatcher
        UPSERT raising) doesn't kill the watcher thread silently.
        Without that guard the thread dies on the first bad
        dispatch, every later cross-process event is dropped, and
        the live UPSERT path looks broken with no log trace.
        """
        last_received_at: str | None = None

        while not self._watcher_stop.is_set():
            # ``>=`` not ``>``: two events emitted in the same
            # microsecond (e.g., back-to-back RunStarted +
            # RunEnded with no flush between) share a
            # ``received_at`` timestamp. ``>`` would skip the
            # second one; ``>=`` re-fetches the boundary event
            # but ``_delivered_ids`` deduplicates downstream so
            # subscribers see each event exactly once.
            condition = (
                f" WHERE received_at >= '{_sql_escape(last_received_at)}'"
                if last_received_at
                else ""
            )
            query = f"""
                SELECT *
                FROM events
                {condition}
                ORDER BY received_at ASC
            """
            try:
                rows = self._flight_query(query)
            except Exception as exc:  # noqa: BLE001 — log and retry on next tick
                logger.debug("Watcher poll failed (will retry): %s", exc)
                self._watcher_stop.wait(timeout=0.5)
                continue

            # Advance the cursor only past rows we successfully
            # dispatched. If a dispatch raises (transient daemon
            # contention, locked DuckDB conn, etc.) the next poll
            # re-fetches that row and retries — at-least-once
            # delivery, deduped by ``_delivered_ids``.
            for row in rows:
                # Parse the full event from the json column
                json_str = row.get("json")
                if json_str:
                    try:
                        evt = json_mod.loads(json_str)
                    except (json_mod.JSONDecodeError, TypeError):
                        evt = row
                else:
                    evt = row

                # Skip events already delivered in-process
                event_id = str(evt.get("id") or row.get("id", ""))
                try:
                    with self._lock:
                        if event_id not in self._delivered_ids:
                            self._dispatch_to_subscribers(evt)
                except Exception as exc:  # noqa: BLE001 — never let one bad dispatch kill the watcher
                    logger.debug(
                        "Watcher dispatch failed for event id=%s (will retry): %s",
                        event_id,
                        exc,
                    )
                    # Don't advance ``last_received_at`` past a row
                    # we failed to dispatch — leave the cursor where
                    # it was and re-fetch on the next poll.
                    break

                ts = row.get("received_at")
                if ts is not None:
                    last_received_at = str(ts)

            self._watcher_stop.wait(timeout=0.5)

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
        for log in self._event_logs.values():
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
