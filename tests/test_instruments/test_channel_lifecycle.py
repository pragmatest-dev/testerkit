"""Position 2 channel-lifecycle behavior — items 4b + 5.

C1 in the v0.2.0 build-item cluster plan, **plus** the item-4b
consolidation that moved ``ChannelStarted`` / ``ChannelClosed``
emission ownership from :class:`InstrumentEventEmitter` to
:class:`ChannelStore` so any writer path (observer.read /
Context.stream / channels.write / FileStore sink) gets the same
lifecycle events without coordinating its own tracker.

Verifies:

- ``ChannelStarted`` fires once per (channel_id, session_id) on first
  write through ``observer.read``; subsequent writes don't re-emit.
- ``ChannelClosed`` fires once per channel on ``ChannelStore.close()``,
  with reason ``"session_ended"``, paired with the original
  ChannelStarted's run_id.
- ``InstrumentRead`` per-sample event is retired (no longer importable).
- ``observer.read`` stamps the active harness ``Context``'s
  ``_observations`` with the channel URI on first write per (vector,
  channel) — item 5. Idempotent via ``setdefault``.
- ``InstrumentEventEmitter`` no longer emits ``ChannelStarted`` itself — the
  store does. Per-instance trackers in the observer were a
  pre-consolidation duplicate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from litmus.data.channels.store import ChannelStore
from litmus.data.events import ChannelClosed, ChannelStarted
from litmus.execution._state import push_current_context, reset_current_context
from litmus.execution.harness import Context, TestHarness
from litmus.instruments.observer import InstrumentEventEmitter


class CollectingLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
def session(tmp_path: Path) -> tuple[ChannelStore, CollectingLog, UUID, UUID]:
    """Real ChannelStore with the lifecycle event log wired."""
    log = CollectingLog()
    session_id = uuid4()
    run_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=1000, event_log=log)
    store.open()
    return store, log, session_id, run_id


def _emitter(
    store: ChannelStore,
    log: CollectingLog,
    session_id: UUID,
    run_id: UUID,
) -> InstrumentEventEmitter:
    return InstrumentEventEmitter(
        event_log=log,  # type: ignore[arg-type]
        session_id=session_id,
        role="dmm",
        run_id=run_id,
        resource="GPIB::16",
        channel_store=store,
    )


# --------------------------------------------------------------------- #
# item 4b — ChannelStarted lifecycle event                              #
# --------------------------------------------------------------------- #


def test_first_read_emits_channel_started(session) -> None:
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    emit.read("dmm.voltage", 3.3, method="measure_voltage")

    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1
    ev = started[0]
    assert ev.channel_id == "dmm.voltage"
    assert ev.instrument_role == "dmm"
    assert ev.method == "measure_voltage"
    assert ev.resource == "GPIB::16"
    assert ev.run_id == run_id
    assert ev.session_id == session_id

    store.close()


def test_subsequent_reads_same_channel_emit_no_more_events(session) -> None:
    """The killer Position-2 test: 1000 reads → 1 event."""
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    for v in range(1000):
        emit.read("dmm.voltage", float(v), method="measure_voltage")

    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1  # not 1000

    store.close()


def test_different_channels_each_get_own_channel_started(session) -> None:
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    emit.read("dmm.voltage", 3.3, method="v")
    emit.read("dmm.current", 0.1, method="i")
    emit.read("dmm.voltage", 3.4, method="v")  # repeat — no new event
    emit.read("dmm.current", 0.2, method="i")  # repeat — no new event

    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 2
    assert {e.channel_id for e in started} == {"dmm.voltage", "dmm.current"}

    store.close()


def test_two_writer_paths_to_same_channel_emit_one_started(session) -> None:
    """Item 4b consolidation: observer.read AND Context.stream hitting the
    same channel in the same session emit ChannelStarted exactly once.

    Pre-consolidation, observer.read tracked its own _started_channels
    set; Context.stream had none, so observer would emit but stream
    wouldn't — or worse, both could emit if the order was right.
    Moving the tracker onto the store eliminates the coordination
    problem.
    """
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    ctx = Context(
        harness=TestHarness(session_id=session_id, channel_store=store),
        channel_store=store,
    )

    emit.read("psu.voltage", 3.3, method="measure_voltage")  # first writer
    ctx.stream("psu.voltage", 3.4)  # second writer, same channel/session

    started = [
        e for e in log.events if isinstance(e, ChannelStarted) and e.channel_id == "psu.voltage"
    ]
    assert len(started) == 1, (
        "ChannelStore should emit ChannelStarted exactly once per "
        f"(channel, session) regardless of writer path; got {len(started)}"
    )
    store.close()


# --------------------------------------------------------------------- #
# item 4b — ChannelClosed lifecycle event (new in this cluster)         #
# --------------------------------------------------------------------- #


def test_close_emits_channel_closed_for_each_touched_channel(session) -> None:
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    emit.read("dmm.voltage", 3.3, method="v")
    emit.read("dmm.current", 0.1, method="i")

    store.close()

    closed = [e for e in log.events if isinstance(e, ChannelClosed)]
    assert len(closed) == 2
    assert {e.channel_id for e in closed} == {"dmm.voltage", "dmm.current"}
    for ev in closed:
        assert ev.reason == "session_ended"
        assert ev.session_id == session_id
        # run_id paired with the original ChannelStarted's run_id
        assert ev.run_id == run_id


def test_close_emits_no_channel_closed_for_untouched_channels(session) -> None:
    store, log, _session_id, _run_id = session
    # No writes at all
    store.close()

    closed = [e for e in log.events if isinstance(e, ChannelClosed)]
    assert closed == []


def test_close_is_idempotent_event_wise(session) -> None:
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    emit.read("dmm.voltage", 3.3, method="v")

    store.close()
    store.close()  # second close emits nothing

    closed = [e for e in log.events if isinstance(e, ChannelClosed)]
    assert len(closed) == 1


def test_close_without_event_log_does_not_raise(tmp_path: Path) -> None:
    """Bringup / tests without a wired event_log: close is silent."""
    store = ChannelStore(tmp_path, uuid4(), flush_threshold=100)
    store.open()
    store.write("dmm.voltage", 3.3)
    store.close()  # no event_log; must not raise


# --------------------------------------------------------------------- #
# Context.stream / channels.write also emit ChannelStarted              #
# (the gap surfaced during C5 PoC review — the design doc §8 contract)  #
# --------------------------------------------------------------------- #


def test_context_stream_emits_channel_started(session) -> None:
    store, log, session_id, _run_id = session
    ctx = Context(
        harness=TestHarness(session_id=session_id, channel_store=store),
        channel_store=store,
    )
    ctx.stream("psu.voltage", 3.3)
    ctx.stream("psu.voltage", 3.4)  # repeat — no new event

    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1
    assert started[0].channel_id == "psu.voltage"
    store.close()


def test_channels_write_emits_channel_started(tmp_path: Path) -> None:
    """``litmus.channels.write`` routes through the store; ChannelStarted
    fires once.
    """
    from litmus import channels
    from litmus.execution._state import set_channel_store

    log = CollectingLog()
    session_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=1000, event_log=log)
    store.open()
    set_channel_store(store)
    try:
        channels.write("eload.current", 1.0)
        channels.write("eload.current", 1.1)

        started = [e for e in log.events if isinstance(e, ChannelStarted)]
        assert len(started) == 1
        assert started[0].channel_id == "eload.current"
    finally:
        set_channel_store(None)
        store.close()


# --------------------------------------------------------------------- #
# Negative — InstrumentRead retired (kept from original test file)      #
# --------------------------------------------------------------------- #


def test_instrument_read_event_no_longer_importable() -> None:
    """Per the no-backcompat principle: InstrumentRead is deleted, not aliased."""
    import litmus.data.events as events_module

    assert not hasattr(events_module, "InstrumentRead")


# --------------------------------------------------------------------- #
# item 5 — observer.read stamps active Context's out_<channel>          #
# --------------------------------------------------------------------- #


def test_observer_read_stamps_active_context_observations(session) -> None:
    """First read per (vector, channel) stamps Context._observations."""
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    ctx = Context(harness=TestHarness(session_id=session_id))
    token = push_current_context(ctx)
    try:
        emit.read("dmm.voltage", 3.3, method="v")
    finally:
        reset_current_context(token)

    assert "dmm.voltage" in ctx._observations
    # The stamped value is the URI returned by ChannelStore.write
    assert ctx._observations["dmm.voltage"].startswith("channel://dmm.voltage")
    store.close()


def test_observer_read_setdefault_does_not_overwrite_existing(session) -> None:
    """Subsequent reads to same channel don't overwrite vector observations."""
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    ctx = Context(harness=TestHarness(session_id=session_id))
    token = push_current_context(ctx)
    try:
        emit.read("dmm.voltage", 3.3, method="v")
        first_uri = ctx._observations["dmm.voltage"]
        emit.read("dmm.voltage", 3.4, method="v")
        assert ctx._observations["dmm.voltage"] == first_uri  # unchanged
    finally:
        reset_current_context(token)
    store.close()


def test_observer_read_with_no_active_context_does_not_error(session) -> None:
    """Outside a Context (no push_current_context) — observer.read still works."""
    store, log, session_id, run_id = session
    emit = _emitter(store, log, session_id, run_id)
    emit.read("dmm.voltage", 3.3, method="v")
    # Channel got written; ChannelStarted fired; no error from missing Context
    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1
    store.close()


def test_observer_read_without_channel_store_does_not_stamp_observations() -> None:
    """No channel_store → no URI → no stamping (nothing to stamp with)."""
    log = CollectingLog()
    emit = InstrumentEventEmitter(
        event_log=log,  # type: ignore[arg-type]
        session_id=uuid4(),
        role="dmm",
        run_id=uuid4(),
        resource="GPIB::16",
        channel_store=None,
    )

    ctx = Context(harness=TestHarness(session_id=uuid4()))
    token = push_current_context(ctx)
    try:
        emit.read("dmm.voltage", 3.3, method="v")
    finally:
        reset_current_context(token)

    # No URI to stamp; observations stays empty
    assert ctx._observations == {}
