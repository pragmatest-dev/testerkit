"""Tests for GenericObserver prefix-based classification."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.instruments.observer import InstrumentEventBuilder
from litmus.instruments.observers.generic import GenericObserver, classify_by_prefix, strip_prefix
from litmus.models.instrument import ChannelKind


class CollectingLog:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _make_observer() -> tuple[GenericObserver, CollectingLog]:
    log = CollectingLog()
    emitter = InstrumentEventBuilder(event_log=log, session_id=uuid4(), role="dmm")  # type: ignore[arg-type]
    obs = GenericObserver(object, "dmm", emitter)
    return obs, log


class TestClassifyByPrefix:
    def test_measure(self):
        assert classify_by_prefix("measure_voltage") == ChannelKind.read

    def test_read(self):
        assert classify_by_prefix("read_data") == ChannelKind.read

    def test_set(self):
        assert classify_by_prefix("set_voltage") == ChannelKind.set

    def test_configure(self):
        assert classify_by_prefix("configure_range") == ChannelKind.configure

    def test_unknown_returns_none(self):
        assert classify_by_prefix("enable_output") is None


class TestStripPrefix:
    def test_measure(self):
        assert strip_prefix("measure_voltage", ChannelKind.read) == "voltage"

    def test_set(self):
        assert strip_prefix("set_voltage", ChannelKind.set) == "voltage"

    def test_no_match(self):
        assert strip_prefix("enable", ChannelKind.configure) == "enable"


class TestGenericObserverOnCall:
    def test_read_method(self):
        obs, log = _make_observer()
        obs.on_call("measure_dc_voltage", (), {}, 3.3)

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, ChannelStarted)
        assert event.channel_id == "dmm.dc_voltage"

    def test_set_method(self):
        obs, log = _make_observer()
        obs.on_call("set_voltage", (5.0,), {}, None)

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentSet)
        assert event.channel_id == "dmm.voltage"
        assert event.value == 5.0

    def test_set_method_kwarg(self):
        obs, log = _make_observer()
        obs.on_call("set_voltage", (), {"value": 5.0}, None)

        assert len(log.events) == 1
        assert log.events[0].value == 5.0

    def test_configure_method(self):
        obs, log = _make_observer()
        obs.on_call("configure_range", (), {"auto": True}, None)

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentConfigure)
        assert event.method == "configure_range"
        assert event.parameters == {"auto": True}

    def test_unknown_method_is_silent(self):
        obs, log = _make_observer()
        obs.on_call("enable_output", (), {}, None)

        assert len(log.events) == 0
