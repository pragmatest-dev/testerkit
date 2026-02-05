"""Instrument base classes and Mock factory.

Litmus does NOT provide instrument drivers. Use:
- PyMeasure (100+ drivers): https://pymeasure.readthedocs.io/
- PyVISA for raw SCPI: https://pyvisa.readthedocs.io/
- Vendor-specific libraries

Litmus provides:
- Base classes for driver patterns (Instrument, VisaInstrument)
- Mock factory for testing without hardware
- Discovery utilities (see litmus.instruments.discovery)

Mock factory for testing:
    from pymeasure.instruments.keithley import Keithley2400
    from litmus.instruments import Mock

    # Create a mock that passes isinstance checks
    smu = Mock(Keithley2400, voltage=5.0, current=1.5e-6)
    assert isinstance(smu, Keithley2400)
    assert smu.voltage == 5.0
"""

from litmus.instruments.base import Instrument
from litmus.instruments.mocks import Mock
from litmus.instruments.visa import VisaInstrument

__all__ = [
    "Instrument",
    "Mock",
    "VisaInstrument",
]
