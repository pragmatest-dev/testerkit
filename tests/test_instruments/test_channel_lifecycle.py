"""Position 2 channel-lifecycle behavior — items 4b + 5.

C1 in the v0.2.0 build-item cluster plan. Verifies:

- ``ChannelStarted`` fires once per (channel_id, session_id) on first
  write through ``observer.read``; subsequent writes don't re-emit.
- ``InstrumentRead`` per-sample event is retired (no longer importable).
- ``observer.read`` stamps the active harness ``Context``'s
  ``_observations`` with the channel URI on first write per (vector,
  channel) — item 5. Idempotent via ``setdefault``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from litmus.data.events import ChannelStarted
from litmus.execution._state import push_current_context, reset_current_context
from litmus.execution.harness import Context, TestHarness
from litmus.instruments.observer import EventEmitter

# --------------------------------------------------------------------- #
# helpers                                                               #
# --------------------------------------------------------------------- #


class CollectingLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeChannelStore:
    def __init__(self) -> None:
        self.written: list[tuple[str, Any]] = []

    def write(self, channel_id: str, value: Any, source: str = "") -> str:  # noqa: ARG002
        self.written.append((channel_id, value))
        return f"channel://{channel_id}?session=test"


def _make_emitter(channel_store: Any | None = None) -> tuple[EventEmitter, CollectingLog]:
    log = CollectingLog()
    emitter = EventEmitter(
        event_log=log,  # type: ignore[arg-type]
        session_id=uuid4(),
        role="dmm",
        run_id=uuid4(),
        resource="GPIB::16",
        channel_store=channel_store,
    )
    return emitter, log


# --------------------------------------------------------------------- #
# item 4b — ChannelStarted lifecycle event                              #
# --------------------------------------------------------------------- #


def test_first_read_emits_channel_started() -> None:
    emitter, log = _make_emitter()
    emitter.read("dmm.voltage", 3.3, method="measure_voltage")

    assert len(log.events) == 1
    ev = log.events[0]
    assert isinstance(ev, ChannelStarted)
    assert ev.channel_id == "dmm.voltage"
    assert ev.instrument_role == "dmm"
    assert ev.method == "measure_voltage"
    assert ev.resource == "GPIB::16"


def test_subsequent_reads_same_channel_emit_no_more_events() -> None:
    """The killer Position-2 test: 1000 reads → 1 event."""
    emitter, log = _make_emitter()
    for v in range(1000):
        emitter.read("dmm.voltage", float(v), method="measure_voltage")

    assert len(log.events) == 1  # not 1000
    assert isinstance(log.events[0], ChannelStarted)


def test_different_channels_each_get_own_channel_started() -> None:
    emitter, log = _make_emitter()
    emitter.read("dmm.voltage", 3.3, method="v")
    emitter.read("dmm.current", 0.1, method="i")
    emitter.read("dmm.voltage", 3.4, method="v")  # repeat — no new event
    emitter.read("dmm.current", 0.2, method="i")  # repeat — no new event

    assert len(log.events) == 2
    assert {e.channel_id for e in log.events} == {"dmm.voltage", "dmm.current"}


def test_different_emitter_instances_each_emit_channel_started() -> None:
    """Two emitters (different sessions) write same channel → two events."""
    emitter_a, log_a = _make_emitter()
    emitter_b, log_b = _make_emitter()

    emitter_a.read("dmm.voltage", 3.3, method="v")
    emitter_b.read("dmm.voltage", 3.3, method="v")

    assert len(log_a.events) == 1
    assert len(log_b.events) == 1


# --------------------------------------------------------------------- #
# item 4b — InstrumentRead retired                                      #
# --------------------------------------------------------------------- #


def test_instrument_read_event_no_longer_importable() -> None:
    """Per the no-backcompat principle: InstrumentRead is deleted, not aliased."""
    import litmus.data.events as events_module

    assert not hasattr(events_module, "InstrumentRead")


# --------------------------------------------------------------------- #
# item 5 — observer.read stamps active Context's out_<channel>          #
# --------------------------------------------------------------------- #


def test_observer_read_stamps_active_context_observations() -> None:
    """First read per (vector, channel) stamps Context._observations."""
    store = FakeChannelStore()
    emitter, _ = _make_emitter(channel_store=store)

    ctx = Context(harness=TestHarness(session_id=uuid4()))
    token = push_current_context(ctx)
    try:
        emitter.read("dmm.voltage", 3.3, method="v")
    finally:
        reset_current_context(token)

    assert "dmm.voltage" in ctx._observations
    # The stamped value is the URI returned by ChannelStore.write
    assert ctx._observations["dmm.voltage"].startswith("channel://dmm.voltage")


def test_observer_read_setdefault_does_not_overwrite_existing() -> None:
    """Subsequent reads to same channel don't overwrite vector observations."""
    store = FakeChannelStore()
    emitter, _ = _make_emitter(channel_store=store)

    ctx = Context(harness=TestHarness(session_id=uuid4()))
    token = push_current_context(ctx)
    try:
        emitter.read("dmm.voltage", 3.3, method="v")
        first_uri = ctx._observations["dmm.voltage"]
        emitter.read("dmm.voltage", 3.4, method="v")
        assert ctx._observations["dmm.voltage"] == first_uri  # unchanged
    finally:
        reset_current_context(token)


def test_observer_read_with_no_active_context_does_not_error() -> None:
    """Outside a Context (no push_current_context) — observer.read still works."""
    store = FakeChannelStore()
    emitter, _ = _make_emitter(channel_store=store)

    # No push_current_context — get_current_context returns None
    emitter.read("dmm.voltage", 3.3, method="v")

    # Channel got written; ChannelStarted fired; no error from missing Context
    assert len(store.written) == 1


def test_observer_read_without_channel_store_does_not_stamp_observations() -> None:
    """No channel_store → no URI → no stamping (nothing to stamp with)."""
    emitter, _ = _make_emitter(channel_store=None)

    ctx = Context(harness=TestHarness(session_id=uuid4()))
    token = push_current_context(ctx)
    try:
        emitter.read("dmm.voltage", 3.3, method="v")
    finally:
        reset_current_context(token)

    # No URI to stamp; observations stays empty
    assert ctx._observations == {}
