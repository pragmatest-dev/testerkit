"""Tests for observer registry and auto-detection."""

from __future__ import annotations

from litmus.instruments.observer import DriverObserver
from litmus.instruments.observers.generic import GenericObserver
from litmus.instruments.observers.pymeasure import PyMeasureObserver
from litmus.instruments.observers.registry import (
    detect_protocol,
    get_observer_class,
    register_observer,
)


class _FakeModule:
    """Helper to create classes with specific __module__ values."""


def _make_class(module: str) -> type:
    cls = type("FakeDriver", (), {})
    cls.__module__ = module
    return cls


class TestDetectProtocol:
    def test_pymeasure(self):
        assert detect_protocol(_make_class("pymeasure.instruments.keithley")) == "pymeasure"

    def test_unregistered_protocols_fall_back_to_generic(self):
        # These will get their own observers in future PRs
        assert detect_protocol(_make_class("qcodes.instrument_drivers.keysight")) == "generic"
        assert detect_protocol(_make_class("nidaqmx.task")) == "generic"
        assert detect_protocol(_make_class("pyvisa.resources")) == "generic"

    def test_unknown_is_generic(self):
        assert detect_protocol(_make_class("my_custom_lib.driver")) == "generic"


class TestGetObserverClass:
    def test_generic(self):
        assert get_observer_class("generic") is GenericObserver

    def test_pymeasure(self):
        assert get_observer_class("pymeasure") is PyMeasureObserver

    def test_unknown_falls_back_to_generic(self):
        assert get_observer_class("nonexistent") is GenericObserver


class TestRegisterObserver:
    def test_register_custom(self):
        class CustomObserver(DriverObserver):
            pass

        register_observer("custom_proto", CustomObserver)
        assert get_observer_class("custom_proto") is CustomObserver
        # Cleanup
        from litmus.instruments.observers.registry import _observers
        _observers.pop("custom_proto", None)
