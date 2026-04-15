"""Instrument base classes, discovery, and Mock factory.

Litmus does NOT provide instrument drivers. Use:
- PyMeasure (100+ drivers): https://pymeasure.readthedocs.io/
- PyVISA for raw SCPI: https://pyvisa.readthedocs.io/
- Vendor-specific libraries

Litmus provides utilities for:
- Discovery: Scan for available instruments at setup time
- Identification: Query instrument identity (manufacturer, model, serial)
- Validation: Verify expected instruments are present at runtime
- Traceability: Log instrument identity and calibration with measurements

Discovery example:
    from litmus.instruments import discover_visa, get_info_visa

    # Setup time: scan for instruments
    resources = discover_visa()  # ["GPIB::16::INSTR", "USB::0x1234::INSTR"]

    # Runtime: query specific instrument
    info = get_info_visa("GPIB::16::INSTR")
    # InstrumentInfo(manufacturer="Keithley", model="2000", serial="ABC123")

Mock factory for testing:
    from pymeasure.instruments.keithley import Keithley2400
    from litmus.instruments import Mock

    # Create a mock that passes isinstance checks
    smu = Mock(Keithley2400, voltage=5.0, current=1.5e-6)
    assert isinstance(smu, Keithley2400)
    assert smu.voltage == 5.0

Mock supports:
- Simple values: measure_voltage=3.3
- Dict lookup: query={"MEAS:VOLT?": "3.3", "MEAS:CURR?": "0.1"}
- Callables: query=lambda cmd: "3.3" if "VOLT" in cmd else "0.0"
"""

from litmus.instruments.base import Instrument
from litmus.instruments.discovery import (
    DiscoveryProtocol,
    discover,
    discover_and_identify,
    discover_visa,
    get_info,
    get_info_visa,
    parse_idn,
)
from litmus.instruments.mocks import Mock
from litmus.instruments.visa import VisaInstrument
from litmus.models.instrument import (
    CalibrationInfo,
    InstrumentInfo,
    InstrumentRecord,
)

__all__ = [
    # Base classes
    "Instrument",
    "VisaInstrument",
    # Mock factory
    "Mock",
    # Models
    "InstrumentInfo",
    "CalibrationInfo",
    "InstrumentRecord",
    # Discovery
    "DiscoveryProtocol",
    "discover",
    "discover_visa",
    "discover_and_identify",
    "get_info",
    "get_info_visa",
    "parse_idn",
]
