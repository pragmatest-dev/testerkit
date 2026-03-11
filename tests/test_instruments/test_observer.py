"""Tests for EventEmitter and DriverObserver base contract."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import InstrumentConfigure, InstrumentRead, InstrumentSet
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
    def test_emits_instrument_read(self):
        emitter, log = _make_emitter()
        emitter.read("dmm.voltage", 3.3, method="measure_voltage")

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentRead)
        assert event.channel_id == "dmm.voltage"
        assert event.value == 3.3
        assert event.method == "measure_voltage"
        assert event.resource == "GPIB::16"


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
        log = CollectingLog()
        written: list = []

        class FakeStore:
            def write(self, channel_id: str, value, source: str = "") -> str:  # noqa: ANN001
                written.append((channel_id, value))
                return f"channel://{channel_id}"

        emitter = EventEmitter(
            event_log=log,  # type: ignore[arg-type]
            session_id=uuid4(),
            role="dmm",
            channel_store=FakeStore(),
        )
        emitter.read("dmm.voltage", 3.3, method="v")
        assert len(written) == 1
        assert written[0] == ("dmm.voltage", 3.3)
        # Event value should be the URI
        assert log.events[0].value == "channel://dmm.voltage"


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
