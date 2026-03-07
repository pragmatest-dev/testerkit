"""Incremental JSONL event reader for the UI.

Reads events from disk efficiently by tracking file position,
so each poll only reads newly appended lines.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path


class EventReader:
    """Incremental JSONL event reader for a session."""

    def __init__(self, jsonl_path: Path) -> None:
        self._path = jsonl_path
        self._offset: int = 0

    def read_new(self) -> list[dict]:
        """Read events added since last call."""
        if not self._path.exists():
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                f.seek(self._offset)
                raw = f.read()
                self._offset = f.tell()
        except OSError as exc:
            warnings.warn(
                f"EventReader failed to read '{self._path}' at offset "
                f"{self._offset}: {exc}",
                stacklevel=2,
            )
            return []

        events: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def read_all(self) -> list[dict]:
        """Read all events from the beginning."""
        self._offset = 0
        return self.read_new()


def find_session_log(events_dir: Path) -> Path | None:
    """Find the most recent JSONL file in the events directory.

    Scans date-partitioned directories (events/{date}/{session}.jsonl)
    and returns the newest file by modification time.
    """
    if not events_dir.exists():
        return None
    jsonl_files = list(events_dir.glob("*/*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)
