"""Incremental Arrow IPC event reader for the UI.

Reads events from disk efficiently by tracking batch position,
so each poll only reads newly appended batches.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

logger = logging.getLogger(__name__)


class EventReader:
    """Incremental Arrow IPC event reader for a session."""

    def __init__(self, arrow_path: Path) -> None:
        self._path = arrow_path
        self._batch_offset: int = 0

    def read_new(self) -> list[dict]:
        """Read events added since last call."""
        if not self._path.exists():
            return []
        try:
            reader = ipc.open_file(str(self._path))
        except (pa.ArrowInvalid, OSError):
            return []

        events: list[dict] = []
        for i in range(self._batch_offset, reader.num_record_batches):
            batch = reader.get_batch(i)
            json_col = batch.column("json")
            for j in range(batch.num_rows):
                try:
                    events.append(json.loads(json_col[j].as_py()))
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Skipping malformed event in batch %d row %d: %s", i, j, exc)
                    continue
        self._batch_offset = reader.num_record_batches
        return events

    def read_all(self) -> list[dict]:
        """Read all events from the beginning."""
        self._batch_offset = 0
        return self.read_new()


def find_unclosed_sessions(events_dir: Path) -> list[dict]:
    """Find sessions that have SessionStarted but no SessionEnded.

    Returns a list of dicts with session metadata (session_id, station_id, etc.).
    Useful for diagnostics — shows sessions interrupted by crashes.
    """
    if not events_dir.exists():
        return []

    unclosed: list[dict] = []
    for arrow_path in events_dir.glob("*/*.arrow"):
        reader = EventReader(arrow_path)
        events = reader.read_all()
        started = [e for e in events if e.get("event_type") == "session.started"]
        ended = {e.get("session_id") for e in events if e.get("event_type") == "session.ended"}
        for s in started:
            if s.get("session_id") not in ended:
                unclosed.append(s)
    return unclosed


def find_session_log(events_dir: Path) -> Path | None:
    """Find the most recent Arrow IPC file in the events directory.

    Scans date-partitioned directories (events/{date}/{session}.arrow)
    and returns the newest file by modification time.
    """
    if not events_dir.exists():
        return None
    arrow_files = list(events_dir.glob("*/*.arrow"))
    if not arrow_files:
        return None
    return max(arrow_files, key=lambda p: p.stat().st_mtime)
