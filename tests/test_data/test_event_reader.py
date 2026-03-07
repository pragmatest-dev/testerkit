"""Tests for EventReader incremental JSONL reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litmus.data.event_reader import EventReader, find_session_log


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    return tmp_path / "events" / "2026-03-06" / "abc123.jsonl"


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")


class TestEventReader:
    def test_read_new_empty_file(self, jsonl_path: Path):
        _write_events(jsonl_path, [])
        reader = EventReader(jsonl_path)
        assert reader.read_new() == []

    def test_read_new_returns_events(self, jsonl_path: Path):
        events = [{"event_type": "session.started", "station_id": "S1"}]
        _write_events(jsonl_path, events)
        reader = EventReader(jsonl_path)
        result = reader.read_new()
        assert len(result) == 1
        assert result[0]["event_type"] == "session.started"

    def test_incremental_reads(self, jsonl_path: Path):
        _write_events(jsonl_path, [{"event_type": "a"}])
        reader = EventReader(jsonl_path)
        first = reader.read_new()
        assert len(first) == 1

        _write_events(jsonl_path, [{"event_type": "b"}, {"event_type": "c"}])
        second = reader.read_new()
        assert len(second) == 2
        assert second[0]["event_type"] == "b"

    def test_read_all_resets_offset(self, jsonl_path: Path):
        _write_events(jsonl_path, [{"event_type": "a"}, {"event_type": "b"}])
        reader = EventReader(jsonl_path)
        reader.read_new()  # consume all

        result = reader.read_all()
        assert len(result) == 2

    def test_missing_file(self, tmp_path: Path):
        reader = EventReader(tmp_path / "nonexistent.jsonl")
        assert reader.read_new() == []

    def test_partial_write_skips_bad_json(self, jsonl_path: Path):
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write('{"event_type": "a"}\n')
            f.write('{"incomplete\n')
            f.write('{"event_type": "c"}\n')
        reader = EventReader(jsonl_path)
        result = reader.read_new()
        assert len(result) == 2
        assert result[0]["event_type"] == "a"
        assert result[1]["event_type"] == "c"


class TestFindSessionLog:
    def test_finds_most_recent(self, tmp_path: Path):
        import os
        import time

        events_dir = tmp_path / "events"
        path1 = events_dir / "2026-03-05" / "old.jsonl"
        path2 = events_dir / "2026-03-06" / "new.jsonl"
        _write_events(path1, [{"a": 1}])
        # Ensure path1 has an older mtime
        past = time.time() - 10
        os.utime(path1, (past, past))
        _write_events(path2, [{"b": 2}])
        result = find_session_log(events_dir)
        assert result == path2

    def test_returns_none_for_missing_dir(self, tmp_path: Path):
        assert find_session_log(tmp_path / "nope") is None

    def test_returns_none_for_empty_dir(self, tmp_path: Path):
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        assert find_session_log(events_dir) is None
