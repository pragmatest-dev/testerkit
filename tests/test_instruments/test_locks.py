"""Tests for per-resource file locking."""

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
