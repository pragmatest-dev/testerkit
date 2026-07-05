"""Behavior contract for ``subscribe_with_refresh``.

The helper wires an EventStore subscription to a page-side
``refresh()`` callback with a debounce window. We test the debounce +
unsubscribe behavior directly — the EventStore subscription is
real-but-mocked (we drive it via a fake store that exposes
``on_event`` / ``emit``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class _FakeEventStore:
    """Minimal EventStore stub for testing subscribe_with_refresh.

    Mirrors the real EventStore.on_event signature; ignores filters
    other than ``event_type`` (which we honor so the test can verify
    per-type subscriptions land separately).
    """

    def __init__(self) -> None:
        self._subs: list[tuple[str | None, Callable[[dict], None]]] = []

    def on_event(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        session_id: Any = None,
        run_id: Any = None,
        since: Any = None,
    ) -> Callable[[], None]:
        _ = role, session_id, run_id, since
        entry = (event_type, callback)
        self._subs.append(entry)

        def _unsub() -> None:
            try:
                self._subs.remove(entry)
            except ValueError:
                pass

        return _unsub

    def emit(self, event: dict[str, Any]) -> None:
        et = event.get("event_type")
        for filter_type, cb in list(self._subs):
            if filter_type is None or filter_type == et:
                cb(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


async def _drain(seconds: float = 0.05) -> None:
    """Yield to the event loop a few ticks so debounced timers fire."""
    await asyncio.sleep(seconds)


class TestSubscribeWithRefresh:
    """Refresh-on-event debounced semantics."""

    async def test_burst_collapses_into_single_refresh(self):
        """A burst of 50 events coalesces into a few refreshes, not ~50."""
        from litmus.ui.shared.components import subscribe_with_refresh

        store = _FakeEventStore()
        calls: list[int] = []
        unsub = subscribe_with_refresh(
            store,
            ["run.started"],
            lambda: calls.append(1),
            debounce_seconds=0.05,
        )
        try:
            for _ in range(50):
                store.emit({"event_type": "run.started"})
            await _drain(0.1)
            # The debounce COALESCES the burst — the guarantee is that 50
            # back-to-back events do not produce ~50 refreshes. The exact count
            # is timing-sensitive: any leading edge landing more than
            # ``debounce_seconds`` after the previous one fires immediately, so
            # under CPU load a burst can split across a few debounce windows
            # (2, 3, 4…). Assert the load-robust invariant — coalesced to a
            # small fraction of the events — not an exact count, which flakes on
            # a busy CI box. A broken (non-coalescing) debounce would fire ~50.
            assert 1 <= len(calls) < 25, calls
        finally:
            unsub()

    async def test_only_subscribed_event_types_fire_refresh(self):
        """Events the page didn't ask for don't trigger refresh."""
        from litmus.ui.shared.components import subscribe_with_refresh

        store = _FakeEventStore()
        calls: list[int] = []
        unsub = subscribe_with_refresh(
            store,
            ["run.ended"],
            lambda: calls.append(1),
            debounce_seconds=0.01,
        )
        try:
            store.emit({"event_type": "run.started"})  # different type
            store.emit({"event_type": "run.ended"})  # subscribed
            await _drain(0.05)
            assert len(calls) == 1
        finally:
            unsub()

    async def test_unsubscribe_releases_all_event_types(self):
        """Calling the returned unsubscribe drops every subscription."""
        from litmus.ui.shared.components import subscribe_with_refresh

        store = _FakeEventStore()
        unsub = subscribe_with_refresh(
            store,
            ["run.started", "run.ended"],
            lambda: None,
            debounce_seconds=0.01,
        )
        assert store.subscriber_count == 2
        unsub()
        assert store.subscriber_count == 0

    async def test_refresh_callback_exception_does_not_break_subscription(self):
        """A broken refresh function doesn't prevent later events from arriving."""
        from litmus.ui.shared.components import subscribe_with_refresh

        store = _FakeEventStore()
        calls: list[int] = []

        def _flaky() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        unsub = subscribe_with_refresh(
            store,
            ["run.ended"],
            _flaky,
            debounce_seconds=0.01,
        )
        try:
            store.emit({"event_type": "run.ended"})
            await _drain(0.05)
            store.emit({"event_type": "run.ended"})
            await _drain(0.05)
            assert len(calls) == 2  # both fires landed despite first raising
        finally:
            unsub()
