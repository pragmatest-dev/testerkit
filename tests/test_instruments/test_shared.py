"""Tests for SharedInstrumentProvider and SharedInstrumentHandle."""

import threading

import pytest

from litmus.instruments.models import InstrumentRecord
from litmus.instruments.shared import SharedInstrumentHandle, SharedInstrumentProvider
from litmus.schemas import StationInstrumentConfig


@pytest.fixture(autouse=True)
def _use_tmp_lock_dir(tmp_path, monkeypatch):
    """Redirect lock dir to tmp_path for test isolation."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


class FakeDriver:
    """Minimal instrument driver for testing."""

    def __init__(self):
        self.connected = True
        self.disconnected = False

    def measure_voltage(self) -> float:
        return 3.3

    def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False


def _make_record(role: str = "dmm", mocked: bool = True) -> InstrumentRecord:
    return InstrumentRecord(
        role=role,
        instrument_id=role,
        resource="GPIB::16::INSTR",
        driver="test.FakeDriver",
        protocol="visa",
        mocked=mocked,
    )


class TestSharedInstrumentProvider:
    """SharedInstrumentProvider: per-measurement lock → connect → disconnect."""

    def test_mock_connection_yields_instrument(self):
        record = _make_record(mocked=True)
        provider = SharedInstrumentProvider(
            role="dmm",
            record=record,
            mock_all=True,
        )
        with provider.connection() as inst:
            assert inst is not None
            assert hasattr(inst, "measure_voltage")

    def test_role_property(self):
        provider = SharedInstrumentProvider(
            role="dmm", record=_make_record(),
        )
        assert provider.role == "dmm"

    def test_mock_config_passed_through(self):
        inst_config = StationInstrumentConfig(
            type="dmm", mock=True,
            mock_config={"measure_voltage": 5.5},
        )
        provider = SharedInstrumentProvider(
            role="dmm",
            record=_make_record(mocked=True),
            inst_config=inst_config,
            mock_all=True,
        )
        with provider.connection() as inst:
            assert inst.measure_voltage() == 5.5


class TestSharedInstrumentHandle:
    """SharedInstrumentHandle: mutex-protected persistent driver access."""

    def test_acquire_yields_driver(self):
        driver = FakeDriver()
        handle = SharedInstrumentHandle("dmm", driver)
        with handle.acquire() as d:
            assert d is driver
            assert d.measure_voltage() == 3.3

    def test_role_property(self):
        handle = SharedInstrumentHandle("dmm", FakeDriver())
        assert handle.role == "dmm"

    def test_driver_property(self):
        driver = FakeDriver()
        handle = SharedInstrumentHandle("dmm", driver)
        assert handle.driver is driver

    def test_acquire_serializes_access(self):
        """Two threads acquire sequentially, not simultaneously."""
        driver = FakeDriver()
        handle = SharedInstrumentHandle("dmm", driver)

        access_order: list[str] = []
        barrier = threading.Barrier(2)

        def thread_fn(name: str) -> None:
            barrier.wait()  # Start both threads simultaneously
            with handle.acquire(timeout=5):
                access_order.append(f"{name}_start")
                # Small sleep to simulate work
                threading.Event().wait(0.05)
                access_order.append(f"{name}_end")

        t1 = threading.Thread(target=thread_fn, args=("A",))
        t2 = threading.Thread(target=thread_fn, args=("B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Verify no interleaving: either A then B, or B then A
        assert len(access_order) == 4
        # First thread completes before second starts
        if access_order[0] == "A_start":
            assert access_order == ["A_start", "A_end", "B_start", "B_end"]
        else:
            assert access_order == ["B_start", "B_end", "A_start", "A_end"]

    def test_acquire_timeout(self):
        """Timeout when lock held by another thread."""
        driver = FakeDriver()
        lock = threading.Lock()
        handle = SharedInstrumentHandle("dmm", driver, lock)

        # Hold the lock from main thread
        lock.acquire()
        try:
            with pytest.raises(TimeoutError, match="Could not acquire"):
                with handle.acquire(timeout=0.1):
                    pass
        finally:
            lock.release()

    def test_shared_lock_between_handles(self):
        """Multiple handles sharing a lock serialize correctly."""
        driver = FakeDriver()
        lock = threading.Lock()
        handle1 = SharedInstrumentHandle("dmm", driver, lock)
        handle2 = SharedInstrumentHandle("matrix", driver, lock)

        with handle1.acquire() as d1:
            assert d1 is driver
            # handle2 should block (not tested here to avoid deadlock)

        # After release, handle2 works
        with handle2.acquire() as d2:
            assert d2 is driver


class TestSharedInstrumentHandleConcurrent:
    """SharedInstrumentHandle with concurrent=True (switches)."""

    def test_concurrent_skips_mutex(self):
        """Concurrent handle yields driver without locking."""
        driver = FakeDriver()
        handle = SharedInstrumentHandle("matrix", driver, concurrent=True)

        assert handle.concurrent is True
        with handle.acquire() as d:
            assert d is driver

    def test_concurrent_allows_simultaneous_access(self):
        """Two threads access a concurrent handle simultaneously."""
        driver = FakeDriver()
        handle = SharedInstrumentHandle("matrix", driver, concurrent=True)

        barrier = threading.Barrier(2, timeout=5)
        both_inside: list[bool] = []

        def thread_fn() -> None:
            with handle.acquire():
                barrier.wait()  # Both must be inside acquire() at the same time
                both_inside.append(True)

        t1 = threading.Thread(target=thread_fn)
        t2 = threading.Thread(target=thread_fn)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both threads were inside acquire() simultaneously
        assert len(both_inside) == 2

    def test_concurrent_ignores_held_lock(self):
        """Concurrent handle yields driver even when the lock is held externally."""
        driver = FakeDriver()
        lock = threading.Lock()
        handle = SharedInstrumentHandle("matrix", driver, lock, concurrent=True)

        # Hold the lock from the main thread — concurrent mode should ignore it
        lock.acquire()
        try:
            with handle.acquire(timeout=0.1) as d:
                assert d is driver
                # Lock is still held by main thread, proving concurrent skips it
                assert not lock.acquire(blocking=False)
        finally:
            lock.release()
