"""Tests for EventStore — storage-agnostic event API."""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from uuid import uuid4

import pytest

from litmus.data.event_store import EventStore
from litmus.data.events import SessionStarted, StreamStarted


@pytest.fixture(scope="module")
def store() -> Generator[EventStore]:
    s = EventStore()
    yield s
    s.close()


def _make_session_started(session_id, station_id="test-station"):
    return SessionStarted(
        session_id=session_id,
        station_id=station_id,
        station_name="Test",
        session_type="test",
        pid=os.getpid(),
    )


class TestEmitAndQuery:
    def test_emit_and_query(self, store: EventStore):
        sid = uuid4()
        event = _make_session_started(sid)
        store.emit(event)
        results = store.events(session_id=sid)
        assert len(results) == 1
        assert results[0]["event_type"] == "session.started"

    def test_query_by_event_type(self, store: EventStore):
        sid = uuid4()
        event = _make_session_started(sid)
        store.emit(event)
        results = store.events(session_id=sid, event_type="session.started")
        assert len(results) == 1
        results = store.events(session_id=sid, event_type="instrument.read")
        assert len(results) == 0

    def test_cross_session_query(self, store: EventStore):
        s1 = uuid4()
        s2 = uuid4()
        store.emit(_make_session_started(s1, "station-a"))
        store.emit(_make_session_started(s2, "station-b"))
        r1 = store.events(session_id=s1)
        r2 = store.events(session_id=s2)
        assert len(r1) == 1
        assert len(r2) == 1

    def test_sessions_returns_session_started(self, store: EventStore):
        sid = uuid4()
        store.emit(_make_session_started(sid))
        sessions = store.events(session_id=sid, event_type="session.started")
        assert len(sessions) == 1
        assert sessions[0]["station_id"] == "test-station"


class TestSubscriptions:
    def test_on_event_replays_existing(self, store: EventStore):
        sid = uuid4()
        store.emit(_make_session_started(sid))
        # Verify the event landed before subscribing
        sanity = store.events(session_id=sid)
        assert len(sanity) == 1, f"sanity check failed: {sanity}"
        received = []
        unsub = store.on_event(lambda e: received.append(e), session_id=sid)
        # Replay runs in a background thread; poll briefly for the
        # callback to fire.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(received) == 0:
            time.sleep(0.05)
        assert len(received) == 1
        unsub()

    def test_on_event_receives_new(self, store: EventStore):
        sid = uuid4()
        received = []
        unsub = store.on_event(lambda e: received.append(e), session_id=sid)
        store.emit(_make_session_started(sid))
        assert len(received) == 1
        assert received[0]["event_type"] == "session.started"
        unsub()

    def test_unsubscribe_stops_delivery(self, store: EventStore):
        sid = uuid4()
        received = []
        unsub = store.on_event(lambda e: received.append(e), session_id=sid)
        unsub()
        store.emit(_make_session_started(sid))
        assert len(received) == 0

    def test_on_event_filters_by_type(self, store: EventStore):
        sid = uuid4()
        received = []
        unsub = store.on_event(
            lambda e: received.append(e),
            event_type="instrument.read",
            session_id=sid,
        )
        store.emit(_make_session_started(sid))
        assert len(received) == 0
        unsub()

    def test_on_event_filters_by_run_id(self, store: EventStore):
        """run_id filter: only events with matching run_id reach the subscriber.

        Live `/live/{run_id}` pages rely on this so operators only see
        streams from the run they navigated to.
        """
        sid = uuid4()
        my_run = uuid4()
        other_run = uuid4()
        received = []
        unsub = store.on_event(
            lambda e: received.append(e),
            event_type="stream.started",
            run_id=my_run,
            replay="none",  # only count live events for clarity
        )
        try:
            # Same session, different runs — only ``my_run`` should land
            store.emit(
                StreamStarted(
                    session_id=sid,
                    run_id=other_run,
                    stream_id=uuid4(),
                    name="other",
                    format="raw",
                )
            )
            store.emit(
                StreamStarted(
                    session_id=sid,
                    run_id=my_run,
                    stream_id=uuid4(),
                    name="mine",
                    format="raw",
                )
            )
            store.emit(
                StreamStarted(
                    session_id=sid,
                    run_id=other_run,
                    stream_id=uuid4(),
                    name="other2",
                    format="raw",
                )
            )

            # Drain — give the in-process dispatcher a tick
            time.sleep(0.05)

            assert len(received) == 1
            assert received[0]["name"] == "mine"
            assert received[0]["run_id"] == str(my_run)
        finally:
            unsub()

    def test_events_query_filters_by_run_id(self, store: EventStore):
        """``events(run_id=...)`` returns only that run's events."""
        sid = uuid4()
        my_run = uuid4()
        other_run = uuid4()
        store.emit(
            StreamStarted(
                session_id=sid,
                run_id=my_run,
                stream_id=uuid4(),
                name="mine_query",
                format="raw",
            )
        )
        store.emit(
            StreamStarted(
                session_id=sid,
                run_id=other_run,
                stream_id=uuid4(),
                name="other_query",
                format="raw",
            )
        )
        store.flush()

        rows = store.events(event_type="stream.started", run_id=my_run)
        names = {r.get("name") for r in rows}
        assert "mine_query" in names
        assert "other_query" not in names


class TestGetEventLog:
    def test_get_event_log_creates_log(self, store: EventStore):
        sid = uuid4()
        log = store.get_event_log(sid)
        assert log.session_id == sid

    def test_get_event_log_same_session(self, store: EventStore):
        sid = uuid4()
        log1 = store.get_event_log(sid)
        log2 = store.get_event_log(sid)
        assert log1 is log2


class TestClose:
    def test_close_cleans_up(self):
        store = EventStore()
        sid = uuid4()
        store.emit(_make_session_started(sid))
        store.on_event(lambda _: None, session_id=sid)
        store.close()
        assert len(store._event_logs) == 0
        assert len(store._subscriptions) == 0
