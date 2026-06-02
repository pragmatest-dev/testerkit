"""Tests for EventEmitter and DriverObserver base contract."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.instruments.observer import DriverObserver, EventEmitter


class CollectingLog:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _make_emitter() -> tuple[EventEmitter, CollectingLog]:
    log = CollectingLog()
    emitter = EventEmitter(
        event_log=log,  # type: ignore[arg-type]
        session_id=uuid4(),
        role="dmm",
        run_id=uuid4(),
        resource="GPIB::16",
    )
    return emitter, log


class TestEventEmitterRead:
    def test_first_read_emits_channel_started(self):
        """Position 2 / v0.2.0: per-sample InstrumentRead is retired.

        First write per (channel, session) emits ChannelStarted —
        carries the instrument identity. Subsequent reads for the
        same channel don't re-emit (sample data lives in ChannelStore).
        """
        emitter, log = _make_emitter()
        emitter.read("dmm.voltage", 3.3, method="measure_voltage")

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, ChannelStarted)
        assert event.channel_id == "dmm.voltage"
        assert event.instrument_role == "dmm"
        assert event.method == "measure_voltage"
        assert event.resource == "GPIB::16"

    def test_subsequent_reads_do_not_emit_per_sample(self):
        """Only the first write per (channel, session) emits ChannelStarted."""
        emitter, log = _make_emitter()
        emitter.read("dmm.voltage", 3.3, method="measure_voltage")
        emitter.read("dmm.voltage", 3.4, method="measure_voltage")
        emitter.read("dmm.voltage", 3.5, method="measure_voltage")

        # Only one ChannelStarted, despite three reads
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)

    def test_different_channels_each_get_their_own_channel_started(self):
        emitter, log = _make_emitter()
        emitter.read("dmm.voltage", 3.3, method="measure_voltage")
        emitter.read("dmm.current", 0.1, method="measure_current")

        assert len(log.events) == 2
        assert all(isinstance(e, ChannelStarted) for e in log.events)
        assert {e.channel_id for e in log.events} == {"dmm.voltage", "dmm.current"}


class TestEventEmitterSet:
    def test_emits_instrument_set(self):
        emitter, log = _make_emitter()
        emitter.set("dmm.voltage", 5.0, attr="voltage")

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentSet)
        assert event.channel_id == "dmm.voltage"
        assert event.value == 5.0
        assert event.attribute == "voltage"


class TestEventEmitterConfigure:
    def test_emits_instrument_configure(self):
        emitter, log = _make_emitter()
        emitter.configure("configure_range", {"auto": True})

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentConfigure)
        assert event.method == "configure_range"
        assert event.parameters == {"auto": True}


class TestEventEmitterChannelStore:
    def test_writes_to_channel_store(self):
        """Channel store still receives every sample; only the EVENT is lifecycle-only."""
        from litmus.data.events import ChannelStarted

        log = CollectingLog()
        written: list = []
        session_id = uuid4()

        class FakeStore:
            # Mirrors the real ChannelStore item-4b contract: emits
            # ChannelStarted exactly once on first write per channel.
            _started: set = set()

            def write(  # noqa: ANN001, ARG002, PLR0913
                self,
                channel_id: str,
                value,
                source: str = "",
                instrument_role: str = "",
                resource: str = "",
                run_id=None,  # noqa: ANN001
                **_kwargs,
            ) -> str:
                written.append((channel_id, value))
                if channel_id not in self._started:
                    self._started.add(channel_id)
                    log.emit(
                        ChannelStarted(
                            session_id=session_id,
                            run_id=run_id,
                            channel_id=channel_id,
                            instrument_role=instrument_role or None,
                            method=source or None,
                            resource=resource or None,
                        )
                    )
                return f"channel://{channel_id}"

        emitter = EventEmitter(
            event_log=log,  # type: ignore[arg-type]
            session_id=session_id,
            role="dmm",
            channel_store=FakeStore(),
        )
        # Three reads — three channel writes; one event (ChannelStarted on first)
        emitter.read("dmm.voltage", 3.3, method="v")
        emitter.read("dmm.voltage", 3.4, method="v")
        emitter.read("dmm.voltage", 3.5, method="v")

        assert len(written) == 3
        assert [w[1] for w in written] == [3.3, 3.4, 3.5]
        # One ChannelStarted event (not three per-sample InstrumentRead)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)


class TestDriverObserverBase:
    def test_on_getattr_passthrough(self):
        emitter, _ = _make_emitter()
        obs = DriverObserver(object, "dmm", emitter)
        assert obs.on_getattr("voltage", 3.3) == 3.3

    def test_on_setattr_noop(self):
        emitter, log = _make_emitter()
        obs = DriverObserver(object, "dmm", emitter)
        obs.on_setattr("voltage", 5.0)
        assert len(log.events) == 0

    def test_on_call_noop(self):
        emitter, log = _make_emitter()
        obs = DriverObserver(object, "dmm", emitter)
        obs.on_call("measure", (), {}, 3.3)
        assert len(log.events) == 0
