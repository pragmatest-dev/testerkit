"""Tests for DaqmxObserver."""

from __future__ import annotations

from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.instruments.observers.daqmx import DaqmxObserver

from .conftest import make_observer


class TestDaqmxRead:
    def test_read_emits(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("read", (), {}, [3.3, 3.4])
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, ChannelStarted)
        assert e.channel_id == "daq.data"


class TestDaqmxWrite:
    def test_write_emits(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("write", ([1.0, 2.0],), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "daq.data"
        assert e.value == [1.0, 2.0]


class TestDaqmxConfigure:
    def test_add_channel(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("add_ai_voltage_chan", (), {"min_val": -10, "max_val": 10}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)
        assert log.events[0].method == "add_ai_voltage_chan"

    def test_cfg_timing(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("cfg_samp_clk_timing", (), {"rate": 1000}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)


class TestDaqmxSilent:
    def test_start_silent(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("start", (), {}, None)
        assert len(log.events) == 0

    def test_stop_silent(self):
        obs, log = make_observer(DaqmxObserver, role="daq")
        obs.on_call("stop", (), {}, None)
        assert len(log.events) == 0
