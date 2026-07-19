"""Tests for MotionObserver."""

from __future__ import annotations

from testerkit.data.events import ChannelStarted, InstrumentSet
from testerkit.instruments.observers.motion import MotionObserver

from .conftest import make_observer


class PositionDriver:
    @property
    def position(self) -> float:
        return 0.0

    @position.setter
    def position(self, v: float) -> None:
        pass


class TestMotionMove:
    def test_move_to(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("move_to", (10.0,), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "stage.position"
        assert e.value == 10.0

    def test_move_home(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("home", (), {}, None)
        assert len(log.events) == 1
        assert log.events[0].value is None

    def test_get_position(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("get_position", (), {}, 5.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "stage.position"

    def test_set_velocity(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("set_velocity", (100.0,), {}, None)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
        assert log.events[0].channel_id == "stage.velocity"


class TestMotionSilent:
    def test_wait_move(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("wait_move", (), {}, None)
        assert len(log.events) == 0

    def test_stop(self):
        obs, log = make_observer(MotionObserver, role="stage")
        obs.on_call("stop", (), {}, None)
        assert len(log.events) == 0


class TestMotionDescriptors:
    def test_position_getattr(self):
        obs, log = make_observer(MotionObserver, driver_class=PositionDriver, role="stage")
        obs.on_getattr("position", 5.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "stage.position"

    def test_position_setattr(self):
        obs, log = make_observer(MotionObserver, driver_class=PositionDriver, role="stage")
        obs.on_setattr("position", 10.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
