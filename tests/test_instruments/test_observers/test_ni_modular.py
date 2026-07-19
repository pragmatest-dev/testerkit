"""Tests for NiModularObserver."""

from __future__ import annotations

from testerkit.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from testerkit.instruments.observers.ni_modular import NiModularObserver

from .conftest import make_observer


class TestNiModularRead:
    def test_measure(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("measure", (), {}, 3.3)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "scope.measure"

    def test_fetch_waveform(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("fetch_waveform", (), {}, [1.0, 2.0])
        assert len(log.events) == 1
        assert log.events[0].channel_id == "scope.fetch_waveform"


class TestNiModularWrite:
    def test_write_waveform(self):
        obs, log = make_observer(NiModularObserver, role="fgen")
        obs.on_call("write_waveform", ([1.0, 2.0],), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
        assert log.events[0].channel_id == "fgen.write_waveform"


class TestNiModularConfigure:
    def test_configure_method(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("configure_vertical", (), {"range": 5.0}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)

    def test_commit(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("commit", (), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)


class TestNiModularSilent:
    def test_initiate_silent(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("initiate", (), {}, None)
        assert len(log.events) == 0

    def test_close_silent(self):
        obs, log = make_observer(NiModularObserver, role="scope")
        obs.on_call("close", (), {}, None)
        assert len(log.events) == 0
