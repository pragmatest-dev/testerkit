"""Event log writer and subscriber protocol.

The EventLog writes typed events to a JSONL file (one per session) and
dispatches them to registered subscribers. Failed subscribers are
disabled for the remainder of the session.

Storage: ``results/events/{date}/{session_id}.jsonl``
"""

from __future__ import annotations

import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from litmus.data.backends._row_helpers import save_ref_to_dir
from litmus.data.events import EventBase


@runtime_checkable
class EventSubscriber(Protocol):
    """Protocol for event log subscribers."""

    format_name: str
    event_types: set[type]

    def open(self) -> None: ...
    def on_event(self, event: EventBase) -> None: ...
    def close(self) -> None: ...


class EventLog:
    """Writes events to JSONL and dispatches to subscribers.

    One file per session, date-partitioned. Each event gets
    ``received_at`` stamped on emit.
    """

    def __init__(self, log_dir: Path, session_id: UUID) -> None:
        self.log_dir = log_dir
        self.session_id = session_id
        date_dir = self.log_dir / date.today().isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)
        self._path = date_dir / f"{session_id}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8")
        self._subscribers: list[EventSubscriber] = []
        self._failed: set[EventSubscriber] = set()
        self._ref_dir = date_dir / f"{session_id}_ref"

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: EventBase) -> None:
        """Stamp received_at, write to JSONL, dispatch to subscribers."""
        event.received_at = datetime.now(UTC)
        line = event.model_dump_json()
        self._file.write(line + "\n")
        self._file.flush()

        for sub in self._subscribers:
            if sub in self._failed:
                continue
            if type(event) not in sub.event_types:
                continue
            try:
                sub.on_event(event)
            except Exception as exc:
                self._failed.add(sub)
                warnings.warn(
                    f"EventSubscriber '{sub.format_name}' failed on "
                    f"{type(event).__name__}: {exc}",
                    stacklevel=2,
                )

    def add_subscriber(self, sub: EventSubscriber) -> None:
        """Register a subscriber and call open().

        If open() raises, the subscriber is not registered and the
        exception propagates to the caller.
        """
        sub.open()
        self._subscribers.append(sub)

    def save_ref(self, vector_id: str, key: str, value: Any) -> str:
        """Save large data to _ref/ subdirectory."""
        self._ref_dir.mkdir(exist_ok=True)
        return save_ref_to_dir(self._ref_dir, vector_id, key, value)

    def close(self) -> None:
        """Close all subscribers, then close the JSONL file."""
        for sub in self._subscribers:
            try:
                sub.close()
            except Exception as exc:
                warnings.warn(
                    f"EventSubscriber '{sub.format_name}' close failed: {exc}",
                    stacklevel=2,
                )
        if self._file and not self._file.closed:
            self._file.close()
