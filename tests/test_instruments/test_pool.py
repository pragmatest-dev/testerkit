"""Tests for InstrumentPool lifecycle management."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import InstrumentConnected, InstrumentDisconnected
from litmus.instruments.models import InstrumentRecord
from litmus.instruments.pool import InstrumentPool


class CollectingLog:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)


def _make_record(role: str = "dmm") -> InstrumentRecord:
    return InstrumentRecord(
        role=role,
        instrument_id=f"{role}-001",
        resource="",
        mocked=True,
    )


class TestAcquireRelease:
    def test_acquire_returns_proxy(self):
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(), event_log=log, channel_store=None, mock_all=True,
        )
        record = _make_record()
        inst = pool.acquire("dmm", record)

        assert "dmm" in pool.active
        assert inst is pool.active["dmm"]
        # InstrumentConnected event emitted
        connected = [e for e in log.events if isinstance(e, InstrumentConnected)]
        assert len(connected) == 1
        assert connected[0].role == "dmm"

    def test_release_disconnects(self):
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(), event_log=log, channel_store=None, mock_all=True,
        )
        pool.acquire("dmm", _make_record())
        pool.release("dmm")

        assert "dmm" not in pool.active
        disconnected = [e for e in log.events if isinstance(e, InstrumentDisconnected)]
        assert len(disconnected) == 1
        assert disconnected[0].role == "dmm"

    def test_release_all_reverse_order(self):
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(), event_log=log, channel_store=None, mock_all=True,
        )
        pool.acquire("dmm", _make_record("dmm"))
        pool.acquire("psu", _make_record("psu"))
        pool.release_all()

        assert len(pool.active) == 0
        disconnected = [e for e in log.events if isinstance(e, InstrumentDisconnected)]
        assert len(disconnected) == 2
        # Released in reverse: psu first, then dmm
        assert disconnected[0].role == "psu"
        assert disconnected[1].role == "dmm"

    def test_release_nonexistent_noop(self):
        pool = InstrumentPool(
            session_id=uuid4(), event_log=None, channel_store=None,
        )
        pool.release("nonexistent")  # should not raise


class TestMockAll:
    def test_mock_all_overrides_record(self):
        pool = InstrumentPool(
            session_id=uuid4(), event_log=None, channel_store=None, mock_all=True,
        )
        record = _make_record()
        record.mocked = False
        pool.acquire("dmm", record)
        assert record.mocked is True


class TestNoEventLog:
    def test_acquire_without_event_log(self):
        pool = InstrumentPool(
            session_id=uuid4(), event_log=None, channel_store=None, mock_all=True,
        )
        inst = pool.acquire("dmm", _make_record())
        assert inst is not None
        pool.release_all()
