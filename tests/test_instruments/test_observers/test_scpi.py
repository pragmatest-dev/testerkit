"""Tests for ScpiObserver (RsInstrument / easy-scpi)."""

from __future__ import annotations

from litmus.data.events import ChannelStarted, InstrumentSet
from litmus.instruments.observers.scpi import ScpiObserver

from .conftest import make_observer


class TestScpiObserverQuery:
    def test_query_float(self):
        obs, log = make_observer(ScpiObserver, role="dmm")
        obs.on_call("query_float", ("FREQ?",), {}, 1000.0)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, ChannelStarted)
        assert e.channel_id == "dmm.freq"

    def test_query_str_with_opc(self):
        obs, log = make_observer(ScpiObserver, role="dmm")
        obs.on_call("query_str_with_opc", ("SYST:ERR?",), {}, "0,No error")
        assert len(log.events) == 1
        assert log.events[0].channel_id == "dmm.syst_err"


class TestScpiObserverWrite:
    def test_write_int(self):
        obs, log = make_observer(ScpiObserver, role="gen")
        obs.on_call("write_int", ("COUNT", 10), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "gen.count"
        assert e.value == 10

    def test_write_bin_block(self):
        obs, log = make_observer(ScpiObserver, role="gen")
        obs.on_call("write_bin_block", ("DATA",), {}, None)
        assert len(log.events) == 1
        assert log.events[0].value == "<binary>"


class TestScpiObserverFallback:
    def test_unknown_uses_generic(self):
        obs, log = make_observer(ScpiObserver, role="gen")
        obs.on_call("measure_power", (), {}, 42.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
