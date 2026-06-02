"""Tests for EventReader incremental Arrow IPC reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from litmus.data._event_reader import EventReader, find_session_log
from litmus.data.event_log import _IPC_SCHEMA


@pytest.fixture
def arrow_path(tmp_path: Path) -> Path:
    return tmp_path / "events" / "2026-03-06" / "abc123.arrow"


def _write_events(path: Path, events: list[dict]) -> None:
    """Write events to an Arrow IPC file.

    Item 21: the IPC schema includes typed payload columns for every
    primitive-typed field on every ``EventBase`` subclass. Fill them
    with ``None`` here — the test fixture only cares about envelope
    + JSON fallback round-trip; the typed-column path is exercised
    in the EventStore + EventLog tests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink:
        writer = ipc.new_stream(sink, _IPC_SCHEMA)
        for evt in events:
            row: dict[str, list] = {
                "id": [evt.get("id", "test")],
                "event_type": [evt.get("event_type", "unknown")],
                "occurred_at": [evt.get("occurred_at", datetime(2026, 1, 1, tzinfo=UTC))],
                "received_at": [evt.get("received_at", datetime(2026, 1, 1, tzinfo=UTC))],
                "session_id": [evt.get("session_id", "sid")],
                "run_id": [evt.get("run_id")],
                "json": [json.dumps(evt)],
            }
            # Fill remaining (typed payload) columns with None
            envelope = set(row)
            for field in _IPC_SCHEMA.names:
                if field not in envelope:
                    row[field] = [None]
            batch = pa.record_batch(row, schema=_IPC_SCHEMA)
            writer.write_batch(batch)
        writer.close()


class TestEventReader:
    def test_read_new_empty_file(self, arrow_path: Path):
        _write_events(arrow_path, [])
        reader = EventReader(arrow_path)
        assert reader.read_new() == []

    def test_read_new_returns_events(self, arrow_path: Path):
        events = [{"event_type": "session.started", "station_id": "S1"}]
        _write_events(arrow_path, events)
        reader = EventReader(arrow_path)
        result = reader.read_new()
        assert len(result) == 1
        assert result[0]["event_type"] == "session.started"

    def test_read_all_resets_offset(self, arrow_path: Path):
        _write_events(arrow_path, [{"event_type": "a"}, {"event_type": "b"}])
        reader = EventReader(arrow_path)
        reader.read_new()  # consume all

        result = reader.read_all()
        assert len(result) == 2

    def test_missing_file(self, tmp_path: Path):
        reader = EventReader(tmp_path / "nonexistent.arrow")
        assert reader.read_new() == []


class TestFindSessionLog:
    def test_finds_most_recent(self, tmp_path: Path):
        import os
        import time

        events_dir = tmp_path / "events"
        path1 = events_dir / "2026-03-05" / "old.arrow"
        path2 = events_dir / "2026-03-06" / "new.arrow"
        _write_events(path1, [{"event_type": "a"}])
        # Ensure path1 has an older mtime
        past = time.time() - 10
        os.utime(path1, (past, past))
        _write_events(path2, [{"event_type": "b"}])
        result = find_session_log(events_dir)
        assert result == path2

    def test_returns_none_for_missing_dir(self, tmp_path: Path):
        assert find_session_log(tmp_path / "nope") is None

    def test_returns_none_for_empty_dir(self, tmp_path: Path):
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        assert find_session_log(events_dir) is None
