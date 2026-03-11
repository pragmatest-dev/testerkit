"""Tests for observer registry and auto-detection."""

from __future__ import annotations

import pytest

from litmus.instruments.observer import DriverObserver
from litmus.instruments.observers.daqmx import DaqmxObserver
from litmus.instruments.observers.generic import GenericObserver
from litmus.instruments.observers.lantz import LantzObserver
from litmus.instruments.observers.modbus import ModbusObserver
from litmus.instruments.observers.motion import MotionObserver
from litmus.instruments.observers.ni_modular import NiModularObserver
from litmus.instruments.observers.ophyd import OphydObserver
from litmus.instruments.observers.pymeasure import PyMeasureObserver
from litmus.instruments.observers.qcodes import QCodesObserver
from litmus.instruments.observers.registry import (
    detect_protocol,
    get_observer_class,
    register_observer,
)
from litmus.instruments.observers.scpi import ScpiObserver
from litmus.instruments.observers.tektronix import TektronixObserver
from litmus.instruments.observers.visa import VisaObserver


def _make_class(module: str) -> type:
    cls = type("FakeDriver", (), {})
    cls.__module__ = module
    return cls


class TestDetectProtocol:
    @pytest.mark.parametrize("module,expected", [
        # Cross-vendor frameworks
        ("pymeasure.instruments.keithley", "pymeasure"),
        ("instruments.keithley", "instrumentkit"),
        ("pyvisa.resources.serial", "visa"),
        ("vxi11.device", "visa"),
        ("qcodes.instrument_drivers.keysight", "qcodes"),
        ("ophyd.device", "ophyd"),
        ("instrumental.drivers.cameras", "instrumental"),
        ("lantz.drivers.laser", "lantz"),
        ("ivi.agilent", "ivi"),
        ("slave.transport", "slave"),
        ("easy_scpi.instrument", "easy_scpi"),
        ("yaqc.client", "yaqc"),
        # Vendor SDKs
        ("RsInstrument.driver", "rsinstrument"),
        ("tm_devices.drivers.scopes", "tektronix"),
        ("nidaqmx.task", "nidaqmx"),
        ("niscope.session", "ni_modular"),
        ("nidcpower.session", "ni_modular"),
        ("nidmm.session", "ni_modular"),
        ("nifgen.session", "ni_modular"),
        ("niswitch.session", "ni_modular"),
        ("nidigital.session", "ni_modular"),
        # Motion control
        ("thorlabs_apt.core", "motion"),
        ("thorlabs_apt_device.device", "motion"),
        ("zaber_motion.ascii", "motion"),
        ("pipython.gcs2", "motion"),
        # Protocol libraries
        ("pymodbus.client", "modbus"),
        ("minimalmodbus.instrument", "modbus"),
        # Domain-specific
        ("epics.pv", "epics"),
        ("lakeshore.model336", "lakeshore"),
        ("seabreeze.spectrometers", "seabreeze"),
        ("zhinst.toolkit", "zhinst"),
        ("dwfpy.device", "dwfpy"),
        ("picosdk.ps2000a", "picosdk"),
        ("pylablib.devices.Thorlabs", "pylablib"),
    ])
    def test_protocol_detection(self, module: str, expected: str):
        assert detect_protocol(_make_class(module)) == expected

    def test_unknown_is_generic(self):
        assert detect_protocol(_make_class("my_custom_lib.driver")) == "generic"


class TestGetObserverClass:
    @pytest.mark.parametrize("protocol,expected_cls", [
        ("generic", GenericObserver),
        ("pymeasure", PyMeasureObserver),
        ("instrumentkit", PyMeasureObserver),
        ("visa", VisaObserver),
        ("rsinstrument", ScpiObserver),
        ("easy_scpi", ScpiObserver),
        ("qcodes", QCodesObserver),
        ("nidaqmx", DaqmxObserver),
        ("ni_modular", NiModularObserver),
        ("ophyd", OphydObserver),
        ("tektronix", TektronixObserver),
        ("motion", MotionObserver),
        ("modbus", ModbusObserver),
        ("lantz", LantzObserver),
        # Descriptor-based → PyMeasureObserver
        ("instrumental", PyMeasureObserver),
        ("ivi", PyMeasureObserver),
        ("slave", PyMeasureObserver),
        # Method-based → GenericObserver
        ("yaqc", GenericObserver),
        ("lakeshore", GenericObserver),
        ("epics", GenericObserver),
        ("seabreeze", GenericObserver),
        ("zhinst", GenericObserver),
        ("dwfpy", GenericObserver),
        ("picosdk", GenericObserver),
        ("pylablib", GenericObserver),
    ])
    def test_observer_class(self, protocol: str, expected_cls: type):
        assert get_observer_class(protocol) is expected_cls

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
