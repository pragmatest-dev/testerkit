"""Tests for InstrumentPool lifecycle management."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest

from litmus.data.event_log import EventLog
from litmus.data.events import (
    InstrumentConnected,
    InstrumentDisconnected,
    InstrumentReleased,
    InstrumentReserved,
)
from litmus.instruments.locks import ResourceInUse
from litmus.instruments.pool import InstrumentPool
from litmus.models.instrument import InstrumentRecord


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


def _make_real_record(role: str = "dmm", resource: str = "GPIB::16::INSTR") -> InstrumentRecord:
    """Non-mocked record with a resource string — for lock behaviour tests."""
    return InstrumentRecord(
        role=role,
        instrument_id=f"{role}-001",
        resource=resource,
        mocked=False,
    )


@pytest.fixture(autouse=True)
def _redirect_lock_dir(tmp_path, monkeypatch):
    """Redirect LITMUS_HOME so lock files go to a temp dir, not the global store."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


class TestAcquireRelease:
    def test_acquire_returns_proxy(self):
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
            mock_all=True,
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
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
            mock_all=True,
        )
        pool.acquire("dmm", _make_record())
        pool.disconnect("dmm")

        assert "dmm" not in pool.active
        disconnected = [e for e in log.events if isinstance(e, InstrumentDisconnected)]
        assert len(disconnected) == 1
        assert disconnected[0].role == "dmm"

    def test_release_all_reverse_order(self):
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
            mock_all=True,
        )
        pool.acquire("dmm", _make_record("dmm"))
        pool.acquire("psu", _make_record("psu"))
        pool.disconnect_all()

        assert len(pool.active) == 0
        disconnected = [e for e in log.events if isinstance(e, InstrumentDisconnected)]
        assert len(disconnected) == 2
        # Disconnected in reverse: psu first, then dmm
        assert disconnected[0].role == "psu"
        assert disconnected[1].role == "dmm"

    def test_release_nonexistent_noop(self):
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=None,
            channel_store=None,
        )
        pool.disconnect("nonexistent")  # should not raise


class TestMockAll:
    def test_mock_all_overrides_record(self):
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=None,
            channel_store=None,
            mock_all=True,
        )
        record = _make_record()
        record.mocked = False
        pool.acquire("dmm", record)
        assert record.mocked is True


class TestNoEventLog:
    def test_acquire_without_event_log(self):
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=None,
            channel_store=None,
            mock_all=True,
        )
        inst = pool.acquire("dmm", _make_record())
        assert inst is not None
        pool.disconnect_all()


class TestConnectReserveSplit:
    """Phase 2a: connect (no lock) is separate from reserve (lock)."""

    def test_connect_takes_no_lock(self, monkeypatch):
        """connect stores the driver in _records but holds no file lock."""
        monkeypatch.setattr("litmus.instruments.pool.load_and_connect", lambda *a, **kw: object())
        monkeypatch.setattr(
            "litmus.instruments.pool.verify_and_wrap", lambda driver, *a, **kw: driver
        )

        pool = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        record = _make_real_record()
        pool.connect("dmm", record)

        assert "dmm" in pool._records
        assert "dmm" not in pool._locks

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        pool2.reserve("dmm")
        pool2.release_reservation("dmm")

    def test_connect_is_idempotent(self, monkeypatch):
        """A second connect call for the same role returns the cached driver."""
        call_count = 0

        def fake_connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            return object()

        monkeypatch.setattr("litmus.instruments.pool.load_and_connect", fake_connect)
        monkeypatch.setattr(
            "litmus.instruments.pool.verify_and_wrap", lambda driver, *a, **kw: driver
        )

        pool = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        record = _make_real_record()
        first = pool.connect("dmm", record)
        second = pool.connect("dmm", _make_real_record())

        assert first is second
        assert call_count == 1

    def test_reserve_acquires_lock(self):
        """reserve() takes the file lock; a second holder gets ResourceInUse."""
        pool1 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool1._records["dmm"] = _make_real_record()
        pool1.reserve("dmm")

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        with pytest.raises(ResourceInUse):
            pool2.reserve("dmm", timeout=0)

        pool1.release_reservation("dmm")

    def test_release_reservation_frees_lock(self):
        """release_reservation() drops the lock so another holder can acquire."""
        pool1 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool1._records["dmm"] = _make_real_record()
        pool1.reserve("dmm")
        pool1.release_reservation("dmm")

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        pool2.reserve("dmm")
        pool2.release_reservation("dmm")

    def test_release_reservation_noop_when_no_lock(self):
        """release_reservation() on an unreserved role is a silent no-op."""
        pool = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool._records["dmm"] = _make_real_record()
        pool.release_reservation("dmm")

    def test_acquire_is_back_compat_attach_plus_reserve(self, monkeypatch):
        """acquire() = attach + reserve; a second holder cannot reserve."""
        monkeypatch.setattr("litmus.instruments.pool.load_and_connect", lambda *a, **kw: object())
        monkeypatch.setattr(
            "litmus.instruments.pool.verify_and_wrap", lambda driver, *a, **kw: driver
        )

        pool1 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool1.acquire("dmm", _make_real_record())

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        with pytest.raises(ResourceInUse):
            pool2.reserve("dmm", timeout=0)

        pool1.disconnect("dmm")

    def test_reentrant_reserve_refcounted(self):
        """Same holder re-acquiring is refcounted; lock held until all releases."""
        sid = uuid4()
        pool = InstrumentPool(session_id=sid, event_log=None, channel_store=None)
        pool._records["dmm"] = _make_real_record()

        pool.reserve("dmm")
        pool.reserve("dmm")

        pool.release_reservation("dmm")

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        with pytest.raises(ResourceInUse):
            pool2.reserve("dmm", timeout=0)

        pool.release_reservation("dmm")

        pool2.reserve("dmm")
        pool2.release_reservation("dmm")

    def test_release_drains_all_reservations(self, monkeypatch):
        """release() frees all outstanding reservations so another holder can acquire."""
        monkeypatch.setattr("litmus.instruments.pool.load_and_connect", lambda *a, **kw: object())
        monkeypatch.setattr(
            "litmus.instruments.pool.verify_and_wrap", lambda driver, *a, **kw: driver
        )

        sid = uuid4()
        pool = InstrumentPool(session_id=sid, event_log=None, channel_store=None)
        pool.connect("dmm", _make_real_record())
        pool.reserve("dmm")
        pool.reserve("dmm")

        pool.disconnect("dmm")

        pool2 = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        pool2._records["dmm"] = _make_real_record()
        pool2.reserve("dmm")
        pool2.release_reservation("dmm")

    def test_reserve_noop_for_mocked_record(self):
        """reserve() is a no-op when the record is mocked (no real resource to lock)."""
        pool = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None, mock_all=True)
        pool.acquire("dmm", _make_record())
        pool.reserve("dmm")
        assert "dmm" not in pool._locks
        pool.disconnect("dmm")


class TestReservationEvents:
    """Phase 3: InstrumentReserved / InstrumentReleased emission."""

    def test_reserve_emits_instrument_reserved(self):
        """reserve() emits exactly one InstrumentReserved with waited_ms >= 0."""
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
        )
        pool._records["dmm"] = _make_real_record()
        pool.reserve("dmm")

        reserved = [e for e in log.events if isinstance(e, InstrumentReserved)]
        assert len(reserved) == 1
        ev = reserved[0]
        assert ev.role == "dmm"
        assert ev.resource == "GPIB::16::INSTR"
        assert ev.waited_ms >= 0.0

        pool.release_reservation("dmm")

    def test_release_reservation_emits_instrument_released(self):
        """release_reservation() emits exactly one InstrumentReleased."""
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
        )
        pool._records["dmm"] = _make_real_record()
        pool.reserve("dmm")
        pool.release_reservation("dmm")

        released = [e for e in log.events if isinstance(e, InstrumentReleased)]
        assert len(released) == 1
        ev = released[0]
        assert ev.role == "dmm"
        assert ev.resource == "GPIB::16::INSTR"

    def test_reserve_run_id_none_on_no_run_path(self):
        """run_id is None on events when no run is active (interactive/bench path)."""
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
            run_id=None,
        )
        pool._records["dmm"] = _make_real_record()
        pool.reserve("dmm")
        pool.release_reservation("dmm")

        reserved = [e for e in log.events if isinstance(e, InstrumentReserved)]
        released = [e for e in log.events if isinstance(e, InstrumentReleased)]
        assert reserved[0].run_id is None
        assert released[0].run_id is None

    def test_reserve_emits_nothing_for_mocked_record(self):
        """reserve() on a mocked/resource-less role emits no reservation events."""
        log = CollectingLog()
        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=cast(EventLog, log),
            channel_store=None,
            mock_all=True,
        )
        pool.acquire("dmm", _make_record())
        pool.reserve("dmm")
        pool.release_reservation("dmm")

        reserved = [e for e in log.events if isinstance(e, InstrumentReserved)]
        released = [e for e in log.events if isinstance(e, InstrumentReleased)]
        assert len(reserved) == 0
        assert len(released) == 0

        pool.disconnect("dmm")
