"""Observer registry and auto-detection from driver module namespace.

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.observers"]
    mydriver = "my_package.observers:MyDriverObserver"
"""

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


def detect_protocol(driver_class: type) -> str:
    """Detect driver protocol from module namespace."""
    module = driver_class.__module__
    for prefix, protocol in _PREFIX_MAP.items():
        if module.startswith(prefix):
            return protocol
    return "generic"


def get_observer_class(protocol: str) -> type[DriverObserver]:
    """Get the observer class for a protocol. Falls back to generic."""
    return DriverObserver._registry.get(protocol, DriverObserver._registry["generic"])
