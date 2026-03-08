"""Tests for EventStore — storage-agnostic event API."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.event_store import EventStore
from litmus.data.events import SessionStarted


@pytest.fixture
def store(tmp_path: Path) -> Generator[EventStore]:
    s = EventStore(_results_dir=tmp_path / "results")
    yield s
    s.close()


@pytest.fixture
def session_id():
    return uuid4()


def _make_session_started(session_id, station_id="test-station"):
    return SessionStarted(
        session_id=session_id,
        station_id=station_id,
        station_name="Test",
        dut_serial="DUT001",
        session_type="test",
        pid=1234,
    )


class TestEmitAndQuery:
    def test_emit_and_query(self, store: EventStore, session_id):
        event = _make_session_started(session_id)
        store.emit(event)
        results = store.events(session_id=session_id)
        assert len(results) == 1
        assert results[0]["event_type"] == "session.started"

    def test_query_by_event_type(self, store: EventStore, session_id):
        event = _make_session_started(session_id)
        store.emit(event)
        results = store.events(event_type="session.started")
        assert len(results) == 1
        results = store.events(event_type="instrument.read")
        assert len(results) == 0

    def test_cross_session_query(self, store: EventStore):
        s1 = uuid4()
        s2 = uuid4()
        store.emit(_make_session_started(s1, "station-a"))
        store.emit(_make_session_started(s2, "station-b"))
        all_events = store.events()
        assert len(all_events) == 2

    def test_sessions_returns_session_started(self, store: EventStore, session_id):
        store.emit(_make_session_started(session_id))
        sessions = store.sessions()
        assert len(sessions) == 1
        assert sessions[0]["station_id"] == "test-station"


class TestSubscriptions:
    def test_on_event_replays_existing(self, store: EventStore, session_id):
        store.emit(_make_session_started(session_id))
        received = []
        unsub = store.on_event(lambda e: received.append(e))
        assert len(received) == 1
        unsub()

    def test_on_event_receives_new(self, store: EventStore, session_id):
        received = []
        unsub = store.on_event(lambda e: received.append(e))
        store.emit(_make_session_started(session_id))
        assert len(received) == 1
        assert received[0]["event_type"] == "session.started"
        unsub()

    def test_unsubscribe_stops_delivery(self, store: EventStore, session_id):
        received = []
        unsub = store.on_event(lambda e: received.append(e))
        unsub()
        store.emit(_make_session_started(session_id))
        assert len(received) == 0

    def test_on_event_filters_by_type(self, store: EventStore, session_id):
        received = []
        unsub = store.on_event(
            lambda e: received.append(e),
            event_type="instrument.read",
        )
        store.emit(_make_session_started(session_id))
        assert len(received) == 0
        unsub()


class TestGetEventLog:
    def test_get_event_log_creates_log(self, store: EventStore, session_id):
        log = store.get_event_log(session_id)
        assert log.session_id == session_id

    def test_get_event_log_same_session(self, store: EventStore, session_id):
        log1 = store.get_event_log(session_id)
        log2 = store.get_event_log(session_id)
        assert log1 is log2


class TestClose:
    def test_close_cleans_up(self, tmp_path: Path):
        store = EventStore(_results_dir=tmp_path / "results")
        sid = uuid4()
        store.emit(_make_session_started(sid))
        store.on_event(lambda e: None)  # starts watcher
        store.close()
        assert len(store._event_logs) == 0
        assert len(store._subscriptions) == 0
