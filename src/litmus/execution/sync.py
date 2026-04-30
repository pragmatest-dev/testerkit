"""Cross-process sync points for multi-DUT parallel testing.

SyncPoint (child side): emits SyncArrived, subscribes to SyncRelease, blocks.
SyncCoordinator (parent side): watches SyncArrived, emits SyncRelease when
all active slots arrive.

Both use the existing EventStore for cross-process communication — no new
IPC mechanism required.

Usage (child/test code):
    sync = get_sync(event_store)
    if sync:
        sync.wait("thermal_soak", timeout=300)

Usage (orchestrator):
    coordinator = SyncCoordinator(slot_count, session_id, event_store)
    coordinator.start()
    # ... children run ...
    coordinator.mark_slot_dead("slot_2")  # if a child dies
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
    """Child-side sync helper. Blocks until all slots arrive at a named point.

    Each call to ``wait(name)`` emits a ``SyncArrived`` event and blocks until
    a ``SyncRelease`` event with the same name is received via EventStore.
    """

    def __init__(
        self,
        slot_id: str,
        slot_count: int,
        session_id: UUID,
        event_store: EventStore,
    ) -> None:
        self._slot_id = slot_id
        self._slot_count = slot_count
        self._session_id = session_id
        self._event_store = event_store

    @property
    def slot_id(self) -> str:
        return self._slot_id

    @property
    def slot_count(self) -> int:
        return self._slot_count

    def wait(self, name: str, timeout: float | None = None) -> None:
        """Block until all slots reach this sync point.

        Args:
            name: Sync point name (e.g., "thermal_soak").
            timeout: Max seconds to wait. None = wait forever.

        Raises:
            SyncError: If timeout expires before release.
        """
        if self._slot_count <= 1:
            return  # Single-slot, no sync needed

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
                    slot_id=self._slot_id,
                    name=name,
                )
            )
            self._event_store.flush()

            logger.debug(
                "Slot '%s' waiting at sync point '%s'",
                self._slot_id,
                name,
            )

            # Wait for release
            if not release_event.wait(timeout=timeout):
                # Emit a slot.completed event so the orchestrator's
                # SyncCoordinator can mark this slot dead and unblock
                # other slots waiting at this or future sync points.
                from litmus.data.events import SlotCompleted

                self._event_store.emit(
                    SlotCompleted(
                        session_id=self._session_id,
                        slot_id=self._slot_id,
                        outcome="errored",
                        error_message=f"sync timeout at '{name}' after {timeout}s",
                    )
                )
                self._event_store.flush()
                raise SyncError(
                    f"Sync point '{name}' timed out for slot '{self._slot_id}' after {timeout}s"
                )

            logger.debug(
                "Slot '%s' released from sync point '%s'",
                self._slot_id,
                name,
            )
        finally:
            unsub()


class SyncCoordinator:
    """Parent-side sync coordinator. Watches for SyncArrived events and
    emits SyncRelease when all active slots arrive at a named point.

    Handles slot death by reducing the expected count and re-checking
    pending sync points.
    """

    def __init__(
        self,
        slot_count: int,
        session_id: UUID,
        event_store: EventStore,
    ) -> None:
        self._slot_count = slot_count
        self._session_id = session_id
        self._event_store = event_store
        self._arrived: dict[str, set[str]] = {}  # name -> {slot_ids}
        self._released: set[str] = set()  # names already released
        self._dead_slots: set[str] = set()  # slots already marked dead
        self._active_slots = slot_count
        self._lock = threading.Lock()
        self._unsub: Callable[[], None] | None = None
        self._unsub_completed: Callable[[], None] | None = None

    def start(self) -> None:
        """Begin watching for SyncArrived and SlotCompleted events."""
        self._unsub = self._event_store.on_event(
            self._on_event,
            event_type="sync.arrived",
            session_id=self._session_id,
        )
        # Watch for slot.completed to detect dead slots (sync timeouts, crashes)
        self._unsub_completed = self._event_store.on_event(
            self._on_slot_completed,
            event_type="slot.completed",
            session_id=self._session_id,
        )

    def _on_event(self, evt: dict) -> None:
        """Handle a SyncArrived event."""
        name = evt.get("name")
        slot_id = evt.get("slot_id")
        if not name or not slot_id:
            return

        with self._lock:
            if name in self._released:
                return  # Already released

            self._arrived.setdefault(name, set()).add(slot_id)

            logger.debug(
                "Sync coordinator: slot '%s' arrived at '%s' (%d/%d)",
                slot_id,
                name,
                len(self._arrived[name]),
                self._active_slots,
            )

            if len(self._arrived[name]) >= self._active_slots:
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

    def _on_slot_completed(self, evt: dict) -> None:
        """Handle a SlotCompleted event — mark the slot dead if it errored."""
        slot_id = evt.get("slot_id")
        outcome = evt.get("outcome")
        if slot_id and outcome in ("errored", "failed", "aborted"):
            self.mark_slot_dead(slot_id)

    def mark_slot_dead(self, slot_id: str) -> None:
        """Reduce expected count when a child process dies.

        Re-checks all pending sync points in case the dead slot was the
        last one needed. Safe to call multiple times for the same slot.
        """
        with self._lock:
            if slot_id in self._dead_slots:
                return
            self._dead_slots.add(slot_id)
            self._active_slots -= 1

            logger.info(
                "Sync coordinator: slot '%s' dead. Active: %d",
                slot_id,
                self._active_slots,
            )

            # Remove dead slot from any arrivals
            for arrived in self._arrived.values():
                arrived.discard(slot_id)

            # Check if any pending sync points can now be released
            for name, arrived in self._arrived.items():
                if name not in self._released and len(arrived) >= self._active_slots:
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

    Returns None if not in a multi-slot worker process (i.e., _LITMUS_SLOT_ID
    is not set or slot count is 1).

    Args:
        event_store: EventStore for cross-process communication.
            Required if in multi-slot mode.
    """
    slot_id = os.environ.get("_LITMUS_SLOT_ID")
    if not slot_id:
        return None

    slot_count = int(os.environ.get("_LITMUS_SLOT_COUNT", "1"))
    if slot_count <= 1:
        return None

    session_id_str = os.environ.get("_LITMUS_SESSION_ID")
    session_id = UUID(session_id_str) if session_id_str else uuid4()

    if event_store is None:
        raise ValueError(
            "EventStore required for multi-slot sync. "
            "_LITMUS_SLOT_ID is set but no EventStore was provided."
        )

    return SyncPoint(
        slot_id=slot_id,
        slot_count=slot_count,
        session_id=session_id,
        event_store=event_store,
    )
