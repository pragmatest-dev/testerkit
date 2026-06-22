"""Tests for OphydObserver."""

from __future__ import annotations

from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.instruments.observers.ophyd import OphydObserver

from .conftest import make_observer


class TestOphydRead:
    def test_dict_result_per_key(self):
        obs, log = make_observer(OphydObserver, role="det")
        result = {
            "det_voltage": {"value": 3.3, "timestamp": 1000.0},
            "det_current": {"value": 0.01, "timestamp": 1000.0},
        }
        obs.on_call("read", (), {}, result)
        assert len(log.events) == 2
        assert all(isinstance(e, ChannelStarted) for e in log.events)
        channels = {e.channel_id for e in log.events}
        assert channels == {"det.det_voltage", "det.det_current"}

    def test_non_dict_result(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("read", (), {}, 42)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "det.reading"

    def test_get(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("get", (), {}, 5.0)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "det.value"


class TestOphydSet:
    def test_set(self):
        obs, log = make_observer(OphydObserver, role="motor")
        obs.on_call("set", (10.0,), {}, "status")
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "motor.setpoint"
        assert e.value == 10.0

    def test_put(self):
        obs, log = make_observer(OphydObserver, role="motor")
        obs.on_call("put", (5.0,), {}, None)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "motor.setpoint"


class TestOphydConfigure:
    def test_trigger(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("trigger", (), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)
        assert log.events[0].method == "trigger"

    def test_stage(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("stage", (), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)


class TestOphydSilent:
    def test_describe_silent(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("describe", (), {}, {})
        assert len(log.events) == 0

    def test_summary_silent(self):
        obs, log = make_observer(OphydObserver, role="det")
        obs.on_call("summary", (), {}, "text")
        assert len(log.events) == 0
