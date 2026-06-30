"""Tests for per-resource file locking."""

import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from litmus.instruments.locks import (
    ResourceInUse,
    ResourceMeta,
    acquire_resource,
    lock_holder,
    release_resource,
)


@pytest.fixture(autouse=True)
def _use_tmp_lock_dir(tmp_path, monkeypatch):
    """Redirect lock dir to tmp_path for test isolation."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


def _meta(**overrides) -> ResourceMeta:
    defaults = {
        "pid": 12345,
        "session_id": uuid4(),
        "station_id": "test-station",
        "role": "dmm",
        "acquired_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ResourceMeta(**defaults)


class TestAcquireRelease:
    def test_acquire_and_release(self):
        resource = "GPIB::16::INSTR"
        meta = _meta()
        lock = acquire_resource(resource, meta)
        assert lock is not None

        # Metadata written
        holder = lock_holder(resource)
        assert holder is not None
        assert holder.station_id == "test-station"

        release_resource(resource, lock)

    def test_acquire_twice_raises(self):
        resource = "GPIB::17::INSTR"
        meta = _meta()
        lock = acquire_resource(resource, meta)

        with pytest.raises(ResourceInUse, match="GPIB::17::INSTR"):
            acquire_resource(resource, _meta(role="psu"))

        release_resource(resource, lock)

    def test_release_allows_reacquire(self):
        resource = "GPIB::18::INSTR"
        lock1 = acquire_resource(resource, _meta())
        release_resource(resource, lock1)

        lock2 = acquire_resource(resource, _meta(role="psu"))
        release_resource(resource, lock2)

    def test_different_resources_independent(self):
        lock1 = acquire_resource("GPIB::1::INSTR", _meta(role="dmm"))
        lock2 = acquire_resource("GPIB::2::INSTR", _meta(role="psu"))

        release_resource("GPIB::1::INSTR", lock1)
        release_resource("GPIB::2::INSTR", lock2)


class TestLockHolder:
    def test_no_lock_returns_none(self):
        assert lock_holder("NONEXISTENT::RESOURCE") is None

    def test_returns_meta_while_held(self):
        resource = "TCPIP::192.168.1.1::INSTR"
        sid = uuid4()
        meta = _meta(session_id=sid)
        lock = acquire_resource(resource, meta)

        holder = lock_holder(resource)
        assert holder is not None
        assert holder.session_id == sid

        release_resource(resource, lock)


class TestReentrant:
    def test_same_holder_does_not_deadlock(self):
        """Same (pid, session_id, role) re-acquiring returns immediately — no deadlock."""
        resource = "GPIB::10::INSTR"
        meta = _meta()
        lock1 = acquire_resource(resource, meta)
        lock2 = acquire_resource(resource, meta)
        assert lock1 is lock2
        release_resource(resource, lock1)
        release_resource(resource, lock2)

    def test_refcount_n_acquires_need_n_releases(self):
        """Underlying flock is freed only after the Nth release for N acquires."""
        resource = "GPIB::20::INSTR"
        meta = _meta()
        n = 3
        locks = [acquire_resource(resource, meta) for _ in range(n)]

        assert all(lk is locks[0] for lk in locks), "All re-entrant acquires return the same lock"

        other_meta = _meta(session_id=uuid4(), role="psu")
        for i in range(n - 1):
            release_resource(resource, locks[i])
            with pytest.raises(ResourceInUse):
                acquire_resource(resource, other_meta, timeout=0)

        release_resource(resource, locks[-1])
        lock_other = acquire_resource(resource, other_meta, timeout=0)
        release_resource(resource, lock_other)

    def test_different_holder_contention_preserved(self):
        """A different holder still gets ResourceInUse on timeout=0."""
        resource = "GPIB::21::INSTR"
        meta_a = _meta(session_id=uuid4(), role="dmm")
        meta_b = _meta(session_id=uuid4(), role="psu")

        lock_a = acquire_resource(resource, meta_a)
        with pytest.raises(ResourceInUse):
            acquire_resource(resource, meta_b, timeout=0)
        release_resource(resource, lock_a)

    def test_timeout_minus_one_blocks_then_succeeds(self):
        """timeout=-1 blocks until a live holder releases, then acquires."""
        resource = "GPIB::22::INSTR"
        meta_a = _meta(session_id=uuid4(), role="dmm")
        meta_b = _meta(session_id=uuid4(), role="psu")

        lock_a = acquire_resource(resource, meta_a)
        acquired = threading.Event()

        def acquire_blocking() -> None:
            lock = acquire_resource(resource, meta_b, timeout=-1)
            acquired.set()
            release_resource(resource, lock)

        t = threading.Thread(target=acquire_blocking, daemon=True)
        t.start()

        time.sleep(0.1)
        assert not acquired.is_set(), "Thread should still be blocked"

        release_resource(resource, lock_a)
        acquired.wait(timeout=5.0)
        assert acquired.is_set(), "Thread should have acquired after holder released"
        t.join(timeout=5.0)

    def test_timeout_zero_fail_fast_preserved(self):
        """timeout=0 raises ResourceInUse immediately (unchanged behaviour)."""
        resource = "GPIB::23::INSTR"
        meta_a = _meta(session_id=uuid4(), role="dmm")
        meta_b = _meta(session_id=uuid4(), role="psu")

        lock = acquire_resource(resource, meta_a)
        with pytest.raises(ResourceInUse):
            acquire_resource(resource, meta_b, timeout=0)
        release_resource(resource, lock)
