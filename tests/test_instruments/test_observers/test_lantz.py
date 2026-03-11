"""Tests for LantzObserver."""

from __future__ import annotations

from litmus.data.events import InstrumentRead, InstrumentSet
from litmus.instruments.observers.lantz import LantzObserver

from .conftest import make_observer


class FeatDriver:
    """Simulates Lantz Feat descriptors using properties."""

    @property
    def wavelength(self) -> float:
        return 532.0

    @wavelength.setter
    def wavelength(self, v: float) -> None:
        pass

    @property
    def power(self) -> float:
        return 1.0


class TestLantzGetattr:
    def test_control_emits_read(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_getattr("wavelength", 532.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)
        assert log.events[0].channel_id == "laser.wavelength"

    def test_read_only_emits_read(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_getattr("power", 1.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)

    def test_unmapped_no_emit(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_getattr("unknown", 42)
        assert len(log.events) == 0


class TestLantzSetattr:
    def test_control_emits_set(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_setattr("wavelength", 633.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
        assert log.events[0].value == 633.0

    def test_read_only_no_set(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_setattr("power", 2.0)
        assert len(log.events) == 0


class TestLantzCall:
    def test_method_fallback(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_call("measure_power", (), {}, 1.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)

    def test_mapped_method_skipped(self):
        obs, log = make_observer(LantzObserver, driver_class=FeatDriver, role="laser")
        obs.on_call("wavelength", (), {}, 532.0)
        assert len(log.events) == 0
