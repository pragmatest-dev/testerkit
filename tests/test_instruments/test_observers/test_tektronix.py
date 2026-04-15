"""Tests for TektronixObserver."""

from __future__ import annotations

from litmus.data.events import InstrumentRead, InstrumentSet
from litmus.instruments.observers.tektronix import TektronixObserver

from .conftest import make_observer


class DescriptorDriver:
    @property
    def bandwidth(self) -> float:
        return 100e6

    @bandwidth.setter
    def bandwidth(self, v: float) -> None:
        pass


class TestTektronixDescriptors:
    def test_getattr_emits_read(self):
        obs, log = make_observer(TektronixObserver, driver_class=DescriptorDriver, role="scope")
        obs.on_getattr("bandwidth", 100e6)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)
        assert log.events[0].channel_id == "scope.bandwidth"

    def test_setattr_emits_set(self):
        obs, log = make_observer(TektronixObserver, driver_class=DescriptorDriver, role="scope")
        obs.on_setattr("bandwidth", 200e6)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)


class TestTektronixScpi:
    def test_query_parses_scpi(self):
        obs, log = make_observer(TektronixObserver, role="scope")
        obs.on_call("query", ("MEAS:FREQ?",), {}, "1000")
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)
        assert log.events[0].channel_id == "scope.meas_freq"

    def test_write_parses_scpi(self):
        obs, log = make_observer(TektronixObserver, role="scope")
        obs.on_call("write", ("CH1:SCALE 0.5",), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
        assert log.events[0].channel_id == "scope.ch1_scale"
        assert log.events[0].value == "0.5"


class TestTektronixFallback:
    def test_prefix_fallback(self):
        obs, log = make_observer(TektronixObserver, role="scope")
        obs.on_call("measure_frequency", (), {}, 1000)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)
