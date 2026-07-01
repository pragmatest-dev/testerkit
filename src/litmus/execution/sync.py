"""Cross-process sync points for multi-UUT parallel testing.

SyncPoint (child side): emits SyncArrived, subscribes to SyncRelease, blocks.
SyncCoordinator (parent side): watches SyncArrived, emits SyncRelease when
all active sites arrive.

Both use the existing EventStore for cross-process communication — no new
IPC mechanism required.

Usage (child/test code):
    sync = get_sync(event_store)
    if sync:
        sync.wait("thermal_soak", timeout=300)

Usage (orchestrator):
    coordinator = SyncCoordinator(site_count, session_id, event_store)
    coordinator.start()
    # ... children run ...
    coordinator.mark_site_dead(1)  # if a child dies
    coordinator.stop()
"""

import logging
import os
import threading
from collections.abc import Callable
from uuid import UUID, uuid4

from litmus.data.event_store import EventStore
from litmus.data.events import SyncArrived, SyncRelease

logger = logging.getLogger(__name__)


class SyncError(Exception):
    """Raised when a sync point fails."""


class SyncPoint:
    """Child-side sync helper. Blocks until all sites arrive at a named point.

    Each call to ``wait(name)`` emits a ``SyncArrived`` event and blocks until
    a ``SyncRelease`` event with the same name is received via EventStore.
    """

    def __init__(
        self,
        site_index: int,
        site_count: int,
        session_id: UUID,
        event_store: EventStore,
    ) -> None:
        self._site_index = site_index
        self._site_count = site_count
        self._session_id = session_id
        self._event_store = event_store

    @property
    def site_index(self) -> int:
        return self._site_index

    @property
    def site_count(self) -> int:
        return self._site_count

    def wait(self, name: str, timeout: float | None = None) -> None:
        """Block until all sites reach this sync point.

        Args:
            name: Sync point name (e.g., "thermal_soak").
            timeout: Max seconds to wait. None = wait forever.

        Raises:
            SyncError: If timeout expires before release.
        """
        if self._site_count <= 1:
            return  # Single-site, no sync needed

        release_event = threading.Event()

        def on_event(evt: dict) -> None:
            if evt.get("event_type") == "sync.release" and evt.get("name") == name:
                release_event.set()

        # Subscribe BEFORE emitting arrival. on_event() replays existing
        # SyncRelease events, so if the orchestrator already released we
        # catch it on replay and release_event.set() fires immediately.
        unsub = self._event_store.on_event(
            on_event,
            event_type="sync.release",
            session_id=self._session_id,
        )

        try:
            # Emit arrival and flush immediately so the orchestrator's
            # cross-process watcher sees it without waiting for threshold.
            self._event_store.emit(
                SyncArrived(
                    session_id=self._session_id,
                    site_index=self._site_index,
                    name=name,
                )
            )
            self._event_store.flush()

            logger.debug(
                "Site %d waiting at sync point '%s'",
                self._site_index,
                name,
            )

            # Wait for release
            if not release_event.wait(timeout=timeout):
                # Emit a site.completed event so the orchestrator's
                # SyncCoordinator can mark this site dead and unblock
                # other sites waiting at this or future sync points.
                from litmus.data.events import SiteCompleted

                self._event_store.emit(
                    SiteCompleted(
                        session_id=self._session_id,
                        site_index=self._site_index,
                        site_name=None,
                        outcome="errored",
                        error_message=f"sync timeout at '{name}' after {timeout}s",
                    )
                )
                self._event_store.flush()
                raise SyncError(
                    f"Sync point '{name}' timed out for site {self._site_index} after {timeout}s"
                )

            logger.debug(
                "Site %d released from sync point '%s'",
                self._site_index,
                name,
            )
        finally:
            unsub()


class SyncCoordinator:
    """Parent-side sync coordinator. Watches for SyncArrived events and
    emits SyncRelease when all active sites arrive at a named point.

    Handles site death by reducing the expected count and re-checking
    pending sync points.
    """

    def __init__(
        self,
        site_count: int,
        session_id: UUID,
        event_store: EventStore,
    ) -> None:
        self._site_count = site_count
        self._session_id = session_id
        self._event_store = event_store
        self._arrived: dict[str, set[int]] = {}  # name -> {site_indices}
        self._released: set[str] = set()  # names already released
        self._dead_sites: set[int] = set()  # sites already marked dead
        self._active_sites = site_count
        self._lock = threading.Lock()
        self._unsub: Callable[[], None] | None = None
        self._unsub_completed: Callable[[], None] | None = None

    def start(self) -> None:
        """Begin watching for SyncArrived and SiteCompleted events."""
        self._unsub = self._event_store.on_event(
            self._on_event,
            event_type="sync.arrived",
            session_id=self._session_id,
        )
        # Watch for site.completed to detect dead sites (sync timeouts, crashes)
        self._unsub_completed = self._event_store.on_event(
            self._on_site_completed,
            event_type="site.completed",
            session_id=self._session_id,
        )

    def _on_event(self, evt: dict) -> None:
        """Handle a SyncArrived event."""
        name = evt.get("name")
        site_index = evt.get("site_index")
        if not name or site_index is None:
            return

        with self._lock:
            if name in self._released:
                return  # Already released

            self._arrived.setdefault(name, set()).add(site_index)

            logger.debug(
                "Sync coordinator: site %d arrived at '%s' (%d/%d)",
                site_index,
                name,
                len(self._arrived[name]),
                self._active_sites,
            )

            if len(self._arrived[name]) >= self._active_sites:
                self._release(name)

    def _release(self, name: str) -> None:
        """Emit SyncRelease for a named sync point. Caller holds lock."""
        self._released.add(name)
        logger.info("Sync coordinator: releasing '%s'", name)
        self._event_store.emit(
            SyncRelease(
                session_id=self._session_id,
                name=name,
            )
        )
        self._event_store.flush()

    def _on_site_completed(self, evt: dict) -> None:
        """Handle a SiteCompleted event — mark the site dead if it errored."""
        site_index = evt.get("site_index")
        outcome = evt.get("outcome")
        if site_index is not None and outcome in ("errored", "failed", "aborted"):
            self.mark_site_dead(site_index)

    def mark_site_dead(self, site_index: int) -> None:
        """Reduce expected count when a child process dies.

        Re-checks all pending sync points in case the dead site was the
        last one needed. Safe to call multiple times for the same site.
        """
        with self._lock:
            if site_index in self._dead_sites:
                return
            self._dead_sites.add(site_index)
            self._active_sites -= 1

            logger.info(
                "Sync coordinator: site %d dead. Active: %d",
                site_index,
                self._active_sites,
            )

            # Remove dead site from any arrivals
            for arrived in self._arrived.values():
                arrived.discard(site_index)

            # Check if any pending sync points can now be released
            for name, arrived in self._arrived.items():
                if name not in self._released and len(arrived) >= self._active_sites:
                    self._release(name)

    def stop(self) -> None:
        """Stop watching for events."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_completed:
            self._unsub_completed()
            self._unsub_completed = None


def get_sync(event_store: EventStore | None = None) -> SyncPoint | None:
    """Factory: create a SyncPoint from environment variables.

    Returns None if not in a multi-site worker process (i.e., _LITMUS_SITE_INDEX
    is not set or site count is 1).

    Args:
        event_store: EventStore for cross-process communication.
            Required if in multi-site mode.
    """
    site_index_str = os.environ.get("_LITMUS_SITE_INDEX")
    if site_index_str is None:
        return None

    site_count = int(os.environ.get("_LITMUS_SITE_COUNT", "1"))
    if site_count <= 1:
        return None

    session_id_str = os.environ.get("_LITMUS_SESSION_ID")
    session_id = UUID(session_id_str) if session_id_str else uuid4()

    if event_store is None:
        raise ValueError(
            "EventStore required for multi-site sync. "
            "_LITMUS_SITE_INDEX is set but no EventStore was provided."
        )

    return SyncPoint(
        site_index=int(site_index_str),
        site_count=site_count,
        session_id=session_id,
        event_store=event_store,
    )
