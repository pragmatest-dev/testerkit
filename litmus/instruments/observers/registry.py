"""Observer registry and auto-detection from driver module namespace."""

from __future__ import annotations

from litmus.instruments.observer import DriverObserver

# Maps driver module prefix → observer protocol name.
_PREFIX_MAP: dict[str, str] = {
    # Cross-vendor frameworks
    "pymeasure.": "pymeasure",
    "instruments.": "instrumentkit",
    "pyvisa.": "visa",
    "vxi11.": "visa",
    "qcodes.": "qcodes",
    "ophyd.": "ophyd",
    "instrumental.": "instrumental",
    "lantz.": "lantz",
    "ivi.": "ivi",
    "slave.": "slave",
    "easy_scpi.": "easy_scpi",
    "yaqc.": "yaqc",
    # Vendor SDKs
    "RsInstrument.": "rsinstrument",
    "tm_devices.": "tektronix",
    "nidaqmx.": "nidaqmx",
    "niscope.": "ni_modular",
    "nidcpower.": "ni_modular",
    "nidmm.": "ni_modular",
    "nifgen.": "ni_modular",
    "niswitch.": "ni_modular",
    "nidigital.": "ni_modular",
    # Motion control
    "thorlabs_apt.": "motion",
    "thorlabs_apt_device.": "motion",
    "zaber_motion.": "motion",
    "pipython.": "motion",
    # Protocol libraries
    "pymodbus.": "modbus",
    "minimalmodbus.": "modbus",
    # Domain-specific (generic/prefix fallback)
    "epics.": "epics",
    "lakeshore.": "lakeshore",
    "seabreeze.": "seabreeze",
    "zhinst.": "zhinst",
    "dwfpy.": "dwfpy",
    "picosdk.": "picosdk",
    "pylablib.": "pylablib",
}

_observers: dict[str, type[DriverObserver]] = {}


def detect_protocol(driver_class: type) -> str:
    """Detect driver protocol from module namespace."""
    module = driver_class.__module__
    for prefix, protocol in _PREFIX_MAP.items():
        if module.startswith(prefix):
            return protocol
    return "generic"


def get_observer_class(protocol: str) -> type[DriverObserver]:
    """Get the observer class for a protocol. Falls back to generic."""
    return _observers.get(protocol, _observers["generic"])


def register_observer(protocol: str, cls: type[DriverObserver]) -> None:
    """Register an observer class for a protocol."""
    _observers[protocol] = cls


def _register_builtins() -> None:
    """Register built-in observers on first import."""
    from litmus.instruments.observers.daqmx import DaqmxObserver
    from litmus.instruments.observers.generic import GenericObserver
    from litmus.instruments.observers.lantz import LantzObserver
    from litmus.instruments.observers.modbus import ModbusObserver
    from litmus.instruments.observers.motion import MotionObserver
    from litmus.instruments.observers.ni_modular import NiModularObserver
    from litmus.instruments.observers.ophyd import OphydObserver
    from litmus.instruments.observers.pymeasure import PyMeasureObserver
    from litmus.instruments.observers.qcodes import QCodesObserver
    from litmus.instruments.observers.scpi import ScpiObserver
    from litmus.instruments.observers.tektronix import TektronixObserver
    from litmus.instruments.observers.visa import VisaObserver

    # Existing
    register_observer("generic", GenericObserver)
    register_observer("pymeasure", PyMeasureObserver)
    register_observer("instrumentkit", PyMeasureObserver)

    # New observer classes
    register_observer("visa", VisaObserver)
    register_observer("rsinstrument", ScpiObserver)
    register_observer("easy_scpi", ScpiObserver)
    register_observer("qcodes", QCodesObserver)
    register_observer("nidaqmx", DaqmxObserver)
    register_observer("ni_modular", NiModularObserver)
    register_observer("ophyd", OphydObserver)
    register_observer("tektronix", TektronixObserver)
    register_observer("motion", MotionObserver)
    register_observer("modbus", ModbusObserver)
    register_observer("lantz", LantzObserver)

    # Descriptor-based → PyMeasureObserver
    register_observer("instrumental", PyMeasureObserver)
    register_observer("ivi", PyMeasureObserver)
    register_observer("slave", PyMeasureObserver)

    # Method-based → GenericObserver
    register_observer("yaqc", GenericObserver)
    register_observer("lakeshore", GenericObserver)
    register_observer("epics", GenericObserver)
    register_observer("seabreeze", GenericObserver)
    register_observer("zhinst", GenericObserver)
    register_observer("dwfpy", GenericObserver)
    register_observer("picosdk", GenericObserver)
    register_observer("pylablib", GenericObserver)


_register_builtins()
