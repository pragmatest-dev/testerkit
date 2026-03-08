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

import json
import threading
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb

from litmus.data._event_filters import event_matches_role
from litmus.data._event_reader import EventReader
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
        if self.since:
            ts = evt.get("received_at") or evt.get("occurred_at")
            if ts is not None:
                try:
                    evt_time = datetime.fromisoformat(str(ts)) if isinstance(ts, str) else ts
                    if evt_time < self.since:
                        return False
                except (ValueError, TypeError):
                    pass
        return True


class EventStore:
    """Storage-agnostic event API. Callers never see paths, files, or SQL."""

    def __init__(self, *, _results_dir: Path | None = None) -> None:
        """Resolve storage location internally.

        The ``_results_dir`` parameter is for testing only — production
        callers should use ``EventStore()`` with no arguments.
        """
        self._results_dir = _resolve_results_dir(_results_dir)
        self._events_dir = self._results_dir / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)

        # Internal writer per session (created lazily via get_event_log)
        self._event_logs: dict[UUID, EventLog] = {}

        # In-process subscriptions
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.Lock()

        # Cross-process watcher
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop = threading.Event()

    # -- Write path ----------------------------------------------------------

    def get_event_log(self, session_id: UUID) -> EventLog:
        """Get or create an EventLog for a session.

        The EventLog handles JSONL writing and subscriber dispatch.
        """
        if session_id not in self._event_logs:
            self._event_logs[session_id] = EventLog(self._events_dir, session_id)
        return self._event_logs[session_id]

    def emit(self, event: EventBase, *, session_id: UUID | None = None) -> None:
        """Write event to storage and notify in-process subscribers.

        If ``session_id`` is provided, writes to that session's log.
        Otherwise uses the session_id from the event itself.
        """
        sid = session_id or getattr(event, "session_id", None)
        if sid is None:
            raise ValueError("Event must have session_id or pass session_id to emit()")

        log = self.get_event_log(sid)
        log.emit(event)

        # Notify in-process subscribers
        evt_dict = json.loads(event.model_dump_json())
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
        """Query events using DuckDB over JSONL files."""
        jsonl_pattern = str(self._events_dir / "*" / "*.jsonl")

        # Build WHERE clauses — values are trusted internal types (UUID,
        # event_type literals, ISO datetimes), not user input.
        conditions: list[str] = []
        if session_id:
            conditions.append(f"session_id = '{session_id}'")
        if event_type:
            conditions.append(f"event_type = '{event_type}'")
        if since:
            conditions.append(f"received_at >= '{since.isoformat()}'")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        try:
            conn = duckdb.connect(":memory:")
            query = f"""
                SELECT *
                FROM read_json_auto('{jsonl_pattern}',
                     format='newline_delimited',
                     ignore_errors=true,
                     union_by_name=true)
                {where}
                ORDER BY received_at ASC
            """
            result = conn.execute(query).fetchall()
            columns = [desc[0] for desc in conn.description or []]
            conn.close()
        except duckdb.IOException:
            # No files match the glob
            return []

        events: list[dict[str, Any]] = []
        for row in result:
            evt = dict(zip(columns, row))
            # Apply role filter (complex logic, not in SQL)
            if role and not event_matches_role(evt, role):
                continue
            events.append(evt)
        return events

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
        Cross-process: internal polling detects new data.

        Returns an unsubscribe callable.
        """
        # Replay existing events
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

        # Register for future events
        sub = _Subscription(
            callback,
            event_type=event_type,
            role=role,
            session_id=session_id,
            since=since,
        )
        with self._lock:
            self._subscriptions.append(sub)

        # Start cross-process watcher if not running
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
        """Poll for new JSONL files and events from other processes."""
        readers: dict[Path, EventReader] = {}
        known_files: set[Path] = set()

        while not self._watcher_stop.is_set():
            # Discover new JSONL files
            try:
                current_files = set(self._events_dir.glob("*/*.jsonl"))
            except OSError:
                current_files = set()

            new_files = current_files - known_files
            for path in new_files:
                # Skip files owned by our own EventLog instances
                if not any(
                    log.path == path for log in self._event_logs.values()
                ):
                    readers[path] = EventReader(path)
            known_files = current_files

            # Read new events from external files
            for path, reader in list(readers.items()):
                if not path.exists():
                    del readers[path]
                    continue
                new_events = reader.read_new()
                for evt in new_events:
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
