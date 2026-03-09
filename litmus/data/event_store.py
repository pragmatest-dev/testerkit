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
import threading
import time
import warnings
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
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.event_log import EventLog
from litmus.data.events import EventBase


def _resolve_results_dir(explicit: Path | None = None) -> Path:
    """Resolve the results directory.

    Resolution chain:
    1. ``explicit`` parameter (if provided)
    2. ``litmus.yaml`` in CWD ancestors → project ``results_dir``
    3. ``LITMUS_HOME`` env var
    4. ``platformdirs.user_data_dir("litmus")``
    """
    if explicit is not None:
        explicit.mkdir(parents=True, exist_ok=True)
        return explicit

    from litmus.connect import _find_project_config

    found = _find_project_config()
    if found:
        root, project = found
        if project.results_dir:
            d = root / project.results_dir
            d.mkdir(parents=True, exist_ok=True)
            return d

    import os

    import platformdirs

    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    d = home / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        self._results_dir = _resolve_results_dir(_results_dir)
        self._events_dir = self._results_dir / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)

        # Start daemon and get gRPC location for Flight queries
        self._location = duckdb_manager.acquire(self._events_dir)

        # Lazy Flight client (for queries)
        self._client: flight.FlightClient | None = None

        # Persistent do_put stream (for writes)
        self._put_stream = FlightPutStream(self._location, "events", "events")

        # Internal writer per session (created lazily via get_event_log)
        self._event_logs: dict[UUID, EventLog] = {}

        # In-process subscriptions
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.Lock()

        # Cross-process watcher
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop = threading.Event()

    def _get_client(self) -> flight.FlightClient:
        """Get or create a Flight client to the DuckDB daemon."""
        if self._client is None:
            self._client = flight.connect(self._location)
        return self._client

    def _flight_query(self, sql: str, *, _retries: int = 2) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts.

        Retries on transient gRPC errors (e.g. daemon restart).
        """
        last_exc: Exception | None = None
        for attempt in range(_retries + 1):
            try:
                client = self._get_client()
                ticket = flight.Ticket(f"events\0{sql}".encode())
                reader = client.do_get(ticket)
                table = reader.read_all()
                return table.to_pylist()
            except Exception as exc:
                last_exc = exc
                self._client = None
                if attempt < _retries:
                    time.sleep(0.2)
                    # Re-acquire in case daemon restarted with new port
                    try:
                        self._location = duckdb_manager.acquire(self._events_dir)
                    except Exception:
                        pass
        warnings.warn(
            f"EventStore Flight query failed after {_retries + 1} attempts: {last_exc}",
            stacklevel=2,
        )
        return []

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

    def get_event_log(self, session_id: UUID) -> EventLog:
        """Get or create an EventLog for a session."""
        if session_id not in self._event_logs:
            self._event_logs[session_id] = EventLog(self._events_dir, session_id)
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
        batch = log.emit(event)

        # If the log flushed (buffer hit threshold), push batch to daemon
        if batch is not None:
            self._flight_put(batch)

        # Notify in-process subscribers
        evt_dict = event.model_dump(mode="json")
        with self._lock:
            for sub in self._subscriptions:
                if sub.matches(evt_dict):
                    try:
                        sub.callback(evt_dict)
                    except Exception as exc:
                        warnings.warn(
                            f"Event subscriber failed: {exc}",
                            stacklevel=2,
                        )

    # -- Read path -----------------------------------------------------------

    def events(
        self,
        *,
        session_id: UUID | None = None,
        event_type: str | None = None,
        role: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        """Query events from the DuckDB index via Flight."""
        # Flush any buffered events to IPC + Flight before querying
        for log in self._event_logs.values():
            batch = log.flush()
            if batch is not None:
                self._flight_put(batch)
        try:
            self._put_stream.drain()
        except Exception:
            pass
        conditions: list[str] = []
        if session_id:
            conditions.append(f"session_id = '{_sql_escape(str(session_id))}'")
        if event_type:
            conditions.append(f"event_type = '{_sql_escape(event_type)}'")
        if since:
            conditions.append(f"received_at >= '{_sql_escape(since.isoformat())}'")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT *
            FROM events
            {where}
            ORDER BY received_at ASC
        """
        rows = self._flight_query(query)

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
        """Poll for new events from other processes using Flight queries."""
        last_received_at: str | None = None

        while not self._watcher_stop.is_set():
            condition = (
                f" WHERE received_at > '{_sql_escape(last_received_at)}'"
                if last_received_at
                else ""
            )
            query = f"""
                SELECT *
                FROM events
                {condition}
                ORDER BY received_at ASC
            """
            rows = self._flight_query(query)

            for evt in rows:
                ts = evt.get("received_at")
                if ts is not None:
                    last_received_at = str(ts)
                with self._lock:
                    for sub in self._subscriptions:
                        if sub.matches(evt):
                            try:
                                sub.callback(evt)
                            except Exception as exc:
                                warnings.warn(
                                    f"Event subscriber failed: {exc}",
                                    stacklevel=2,
                                )

            self._watcher_stop.wait(timeout=0.5)

    # -- Lifecycle -----------------------------------------------------------

    @property
    def events_dir(self) -> Path:
        """Internal events directory (for backwards compat during migration)."""
        return self._events_dir

    def close(self) -> None:
        """Stop watchers, release resources."""
        self._watcher_stop.set()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=2.0)
            self._watcher_thread = None

        # Flush remaining buffered events to Flight before closing
        for log in self._event_logs.values():
            batch = log.flush()
            if batch is not None:
                self._flight_put(batch)

        self._put_stream.close()

        for log in self._event_logs.values():
            log.close()
        self._event_logs.clear()

        with self._lock:
            self._subscriptions.clear()

        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                warnings.warn(
                    f"Failed to close Flight client: {exc}",
                    stacklevel=2,
                )
            self._client = None

        duckdb_manager.release(self._events_dir)
