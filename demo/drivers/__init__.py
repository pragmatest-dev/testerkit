"""Demo driver classes.

These classes define the interface for instruments used in demo tests.
Use with Mock for testing:

    from demo.drivers import DMM, PSU, ELoad, Scope
    from litmus.instruments import Mock

    dmm = Mock(DMM, measure_dc_voltage=3.3)
    psu = Mock(PSU, measure_voltage=5.0, measure_current=0.1)
"""

from demo.drivers.dmm import DMM
from demo.drivers.eload import ELoad
from demo.drivers.psu import PSU
from demo.drivers.scope import Scope

__all__ = ["DMM", "PSU", "ELoad", "Scope"]
