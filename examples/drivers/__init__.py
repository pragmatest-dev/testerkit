"""Example driver classes.

These classes define the interface for instruments used in the examples.
Use with Mock for testing:

    from examples.drivers import DMM, PSU, ELoad, Scope
    from litmus.instruments import Mock

    dmm = Mock(DMM, measure_dc_voltage=3.3)
    psu = Mock(PSU, measure_voltage=5.0, measure_current=0.1)
"""

from examples.drivers.dmm import DMM
from examples.drivers.eload import ELoad
from examples.drivers.psu import PSU
from examples.drivers.scope import Scope

__all__ = ["DMM", "PSU", "ELoad", "Scope"]
