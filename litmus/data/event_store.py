"""Storage-agnostic event API.

Callers use ``emit()``, ``events()``, ``on_event()`` — never see paths,
files, or SQL.  Storage details (JSONL files, DuckDB queries, file watching)
are internal.

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

import threading
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb

from litmus.data import duckdb_manager
from litmus.data._event_filters import event_matches_role
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

        # Start daemon and get path to index.duckdb
        self._db_path = duckdb_manager.acquire(self._events_dir)

        # Read-only connection for queries
        self._conn: duckdb.DuckDBPyConnection | None = None

        # Local buffer for immediate read-after-write consistency.
        # The DuckDB index is built asynchronously by the daemon's ingest
        # loop (typically <1s lag).  Until ingested, emitted events live
        # here so that events() returns them immediately.  Buffer entries
        # are pruned once they appear in DuckDB query results.
        self._local_buffer: list[dict[str, Any]] = []
        self._local_buffer_lock = threading.Lock()
        self._MAX_BUFFER_SIZE = 50_000  # safety cap; should never be reached

        # Internal writer per session (created lazily via get_event_log)
        self._event_logs: dict[UUID, EventLog] = {}

        # In-process subscriptions
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.Lock()

        # Cross-process watcher
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop = threading.Event()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """Get or create a read-only DuckDB connection."""
        if self._conn is None:
            self._conn = duckdb.connect(str(self._db_path), read_only=True)
        return self._conn

    # -- Write path ----------------------------------------------------------

    def get_event_log(self, session_id: UUID) -> EventLog:
        """Get or create an EventLog for a session."""
        if session_id not in self._event_logs:
            self._event_logs[session_id] = EventLog(self._events_dir, session_id)
        return self._event_logs[session_id]

    def emit(self, event: EventBase, *, session_id: UUID | None = None) -> None:
        """Write event to JSONL and notify in-process subscribers.

        The daemon's ingest loop picks up the JSONL file for indexing.
        """
        sid = session_id or getattr(event, "session_id", None)
        if sid is None:
            raise ValueError("Event must have session_id or pass session_id to emit()")

        log = self.get_event_log(sid)
        log.emit(event)

        # Buffer for immediate read-after-write consistency
        evt_dict = event.model_dump(mode="json")
        with self._local_buffer_lock:
            self._local_buffer.append(evt_dict)
            if len(self._local_buffer) > self._MAX_BUFFER_SIZE:
                warnings.warn(
                    f"Event local buffer cap ({self._MAX_BUFFER_SIZE}) reached, "
                    "keeping newest entries — DuckDB daemon may be unavailable",
                    stacklevel=2,
                )
                self._local_buffer = self._local_buffer[-self._MAX_BUFFER_SIZE:]

        # Notify in-process subscribers
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
        """Query events from the DuckDB index."""
        conditions: list[str] = []
        if session_id:
            conditions.append(f"session_id = '{session_id}'")
        if event_type:
            conditions.append(f"event_type = '{event_type}'")
        if since:
            conditions.append(f"received_at >= '{since.isoformat()}'")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        # Query DuckDB index (daemon-ingested events)
        db_events: list[dict[str, Any]] = []
        db_ids: set[str] = set()
        try:
            conn = self._get_conn()
            query = f"""
                SELECT *
                FROM events
                {where}
                ORDER BY received_at ASC
            """
            result = conn.execute(query).fetchall()
            columns = [desc[0] for desc in conn.description or []]
            for row in result:
                evt = dict(zip(columns, row))
                if role and not event_matches_role(evt, role):
                    continue
                db_events.append(evt)
                eid = evt.get("id")
                if eid:
                    db_ids.add(str(eid))
        except (duckdb.IOException, duckdb.CatalogException):
            pass

        # Merge locally-buffered events not yet in DuckDB, pruning ingested ones
        with self._local_buffer_lock:
            if db_ids:
                self._local_buffer = [
                    e for e in self._local_buffer
                    if e.get("id") and str(e["id"]) not in db_ids
                ]
            for evt in self._local_buffer:
                # Apply filters
                if session_id and evt.get("session_id") != str(session_id):
                    continue
                if event_type and evt.get("event_type") != event_type:
                    continue
                if since and _is_before(evt, since):
                    continue
                if role and not event_matches_role(evt, role):
                    continue
                db_events.append(evt)

        # Sort by received_at
        db_events.sort(key=lambda e: str(e.get("received_at", "")))
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
        """Poll for new events from other processes using DuckDB index."""
        last_received_at: str | None = None

        while not self._watcher_stop.is_set():
            try:
                conn = self._get_conn()
                condition = (
                    f" WHERE received_at > '{last_received_at}'"
                    if last_received_at
                    else ""
                )
                query = f"""
                    SELECT *
                    FROM events
                    {condition}
                    ORDER BY received_at ASC
                """
                result = conn.execute(query).fetchall()
                columns = [desc[0] for desc in conn.description or []]
            except (duckdb.IOException, duckdb.CatalogException):
                result = []
                columns = []

            for row_values in result:
                evt = dict(zip(columns, row_values))
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

        for log in self._event_logs.values():
            log.close()
        self._event_logs.clear()

        with self._lock:
            self._subscriptions.clear()

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                warnings.warn(
                    f"Failed to close DuckDB read connection: {exc}",
                    stacklevel=2,
                )
            self._conn = None

        duckdb_manager.release(self._events_dir)
