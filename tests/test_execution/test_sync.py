"""Tests for cross-process sync points."""

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from litmus.data.events import SyncRelease
from litmus.execution.sync import (
    SyncCoordinator,
    SyncPoint,
    get_sync,
)


class TestSyncPointSingleSite:
    """SyncPoint is a no-op for single-site (site_count=1)."""

    def test_wait_returns_immediately(self):
        store = MagicMock()
        sp = SyncPoint(
            site_index=0,
            site_count=1,
            session_id=uuid4(),
            event_store=store,
        )
        sp.wait("sync_name")  # Should not block or emit
        store.emit.assert_not_called()
        store.on_event.assert_not_called()


class TestSyncCoordinatorBasics:
    """SyncCoordinator emits SyncRelease when all sites arrive."""

    def test_two_sites_arrive_releases(self):
        session_id = uuid4()
        store = MagicMock()
        released: list[str] = []

        # Capture emit calls
        def capture_emit(event, **kwargs):
            if isinstance(event, SyncRelease):
                released.append(event.name)

        store.emit.side_effect = capture_emit

        # on_event returns an unsubscribe callable and captures the callback
        callbacks = []

        def mock_on_event(callback, **kwargs):
            callbacks.append(callback)
            return lambda: None

        store.on_event.side_effect = mock_on_event

        coord = SyncCoordinator(
            site_count=2,
            session_id=session_id,
            event_store=store,
        )
        coord.start()
        assert len(callbacks) == 2  # sync.arrived + site.completed

        # Simulate site 0 arriving (first callback is sync.arrived handler)
        callbacks[0](
            {
                "event_type": "sync.arrived",
                "name": "thermal_soak",
                "site_index": 0,
            }
        )
        assert len(released) == 0  # Not yet

        # Simulate site 1 arriving
        callbacks[0](
            {
                "event_type": "sync.arrived",
                "name": "thermal_soak",
                "site_index": 1,
            }
        )
        assert released == ["thermal_soak"]

        coord.stop()

    def test_mark_site_dead_reduces_count(self):
        session_id = uuid4()
        store = MagicMock()
        released: list[str] = []

        def capture_emit(event, **kwargs):
            if isinstance(event, SyncRelease):
                released.append(event.name)

        store.emit.side_effect = capture_emit

        callbacks = []
        store.on_event.side_effect = lambda cb, **kw: callbacks.append(cb) or (lambda: None)

        coord = SyncCoordinator(
            site_count=3,
            session_id=session_id,
            event_store=store,
        )
        coord.start()

        # site 0 arrives
        callbacks[0](
            {
                "event_type": "sync.arrived",
                "name": "sync_point",
                "site_index": 0,
            }
        )

        # site 1 dies
        coord.mark_site_dead(1)

        # Now only 2 active sites needed, but only 1 arrived
        assert len(released) == 0

        # site 2 arrives → 2/2, should release
        callbacks[0](
            {
                "event_type": "sync.arrived",
                "name": "sync_point",
                "site_index": 2,
            }
        )
        assert released == ["sync_point"]

        coord.stop()

    def test_mark_dead_releases_pending(self):
        """If a site dies and the remaining sites already arrived, release."""
        session_id = uuid4()
        store = MagicMock()
        released: list[str] = []

        def capture_emit(event, **kwargs):
            if isinstance(event, SyncRelease):
                released.append(event.name)

        store.emit.side_effect = capture_emit

        callbacks = []
        store.on_event.side_effect = lambda cb, **kw: callbacks.append(cb) or (lambda: None)

        coord = SyncCoordinator(
            site_count=2,
            session_id=session_id,
            event_store=store,
        )
        coord.start()

        # site 0 arrives at sync
        callbacks[0](
            {
                "event_type": "sync.arrived",
                "name": "sync_point",
                "site_index": 0,
            }
        )
        assert len(released) == 0

        # site 1 dies → only 1 active needed, site 0 already arrived
        coord.mark_site_dead(1)
        assert released == ["sync_point"]

        coord.stop()

    def test_multiple_sync_points(self):
        session_id = uuid4()
        store = MagicMock()
        released: list[str] = []

        def capture_emit(event, **kwargs):
            if isinstance(event, SyncRelease):
                released.append(event.name)

        store.emit.side_effect = capture_emit

        callbacks = []
        store.on_event.side_effect = lambda cb, **kw: callbacks.append(cb) or (lambda: None)

        coord = SyncCoordinator(
            site_count=2,
            session_id=session_id,
            event_store=store,
        )
        coord.start()

        # Both arrive at "first"
        callbacks[0]({"event_type": "sync.arrived", "name": "first", "site_index": 0})
        callbacks[0]({"event_type": "sync.arrived", "name": "first", "site_index": 1})
        assert "first" in released

        # Both arrive at "second"
        callbacks[0]({"event_type": "sync.arrived", "name": "second", "site_index": 0})
        callbacks[0]({"event_type": "sync.arrived", "name": "second", "site_index": 1})
        assert "second" in released

        coord.stop()


class TestGetSyncFactory:
    """get_sync() reads env vars and creates appropriate SyncPoint."""

    def test_returns_none_without_site_index(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_sync() is None

    def test_returns_none_for_single_site(self):
        env = {"_LITMUS_SITE_INDEX": "0", "_LITMUS_SITE_COUNT": "1"}
        with patch.dict(os.environ, env, clear=True):
            assert get_sync() is None

    def test_raises_without_event_store(self):
        env = {"_LITMUS_SITE_INDEX": "0", "_LITMUS_SITE_COUNT": "2"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="EventStore required"):
                get_sync()

    def test_returns_sync_point_for_multi_site(self):
        session_id = uuid4()
        env = {
            "_LITMUS_SITE_INDEX": "0",
            "_LITMUS_SITE_COUNT": "2",
            "_LITMUS_SESSION_ID": str(session_id),
        }
        store = MagicMock()
        with patch.dict(os.environ, env, clear=True):
            sp = get_sync(store)
            assert sp is not None
            assert sp.site_index == 0
            assert sp.site_count == 2
