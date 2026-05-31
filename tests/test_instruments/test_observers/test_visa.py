"""Tests for VisaObserver SCPI parsing and classification."""

from __future__ import annotations

from litmus.data.events import ChannelStarted, InstrumentSet
from litmus.instruments.observers.visa import VisaObserver, parse_scpi

from .conftest import make_observer


class TestParseScpi:
    def test_query(self):
        assert parse_scpi("MEAS:VOLT:DC?") == ("meas_volt_dc", True)

    def test_write_with_value(self):
        assert parse_scpi("VOLT 3.3") == ("volt", False)

    def test_common_command(self):
        assert parse_scpi("*RST") == ("rst", False)

    def test_idn_query(self):
        assert parse_scpi("*IDN?") == ("idn", True)

    def test_simple_mnemonic(self):
        assert parse_scpi("FREQ?") == ("freq", True)

    def test_whitespace(self):
        assert parse_scpi("  VOLT 5  ") == ("volt", False)


class TestVisaObserverQuery:
    def test_query_emits_read(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("query", ("MEAS:VOLT:DC?",), {}, "3.3")
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, ChannelStarted)
        assert e.channel_id == "dmm.meas_volt_dc"

    def test_ask_emits_read(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("ask", ("FREQ?",), {}, "1000")
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "dmm.freq"


class TestVisaObserverWrite:
    def test_write_emits_set(self):
        obs, log = make_observer(VisaObserver, role="psu")
        obs.on_call("write", ("VOLT 3.3",), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "psu.volt"
        assert e.value == "3.3"

    def test_write_query_emits_read(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("write", ("MEAS:VOLT?",), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)

    def test_write_rst(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("write", ("*RST",), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "dmm.rst"


class TestVisaObserverRawRead:
    def test_read_emits_raw(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("read", (), {}, b"data")
        assert len(log.events) == 1
        assert log.events[0].channel_id == "dmm.raw_read"

    def test_read_raw_emits(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("read_raw", (), {}, b"\x00\x01")
        assert len(log.events) == 1
        assert log.events[0].channel_id == "dmm.raw_read"


class TestVisaObserverFallback:
    def test_unknown_method_falls_through(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("measure_voltage", (), {}, 3.3)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)

    def test_lifecycle_silent(self):
        obs, log = make_observer(VisaObserver, role="dmm")
        obs.on_call("close", (), {}, None)
        assert len(log.events) == 0
