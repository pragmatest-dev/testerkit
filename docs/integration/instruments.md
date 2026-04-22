# Instrument Integration

Litmus does NOT provide instrument drivers. You bring your own:
- **PyMeasure** (100+ drivers): https://pymeasure.readthedocs.io/
- **PyVISA** for raw SCPI: https://pyvisa.readthedocs.io/
- **Vendor libraries** (NI-DAQmx, etc.)

Litmus provides utilities for discovery, identification, mocking, and traceability.

## Quick Start with PyVISA

Install PyVISA with the pure-Python backend (no NI-VISA or Keysight IO Libraries required):

```bash
pip install pyvisa pyvisa-py
```

### Direct PyVISA Usage

```python
import pyvisa

# Connect to instrument
rm = pyvisa.ResourceManager('@py')  # Use pyvisa-py backend
dmm = rm.open_resource("TCPIP::192.168.1.100::INSTR")

# Query identity
print(dmm.query("*IDN?"))

# Measure voltage
voltage = float(dmm.query("MEAS:VOLT:DC?"))
print(f"Voltage: {voltage} V")

dmm.close()
```

### With Station Config

In your station YAML, reference the driver class:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Test Bench 1"

instruments:
  dmm:
    type: dmm
    driver: pyvisa.resources.MessageBasedResource
    resource: "TCPIP::192.168.1.100::INSTR"
```

The pytest plugin will instantiate the driver and make it available as a fixture:

```python
def test_voltage(dmm):
    # dmm is a pyvisa MessageBasedResource
    voltage = float(dmm.query("MEAS:VOLT:DC?"))
    assert voltage > 3.0
```

## Using PyMeasure Drivers

PyMeasure provides high-level drivers for 100+ instruments:

```bash
pip install pymeasure
```

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Test Bench 1"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "TCPIP::192.168.1.101::INSTR"
```

```python
def test_output_voltage(psu, dmm):
    # PyMeasure provides high-level methods
    psu.voltage = 5.0
    psu.output_enabled = True

    voltage = dmm.voltage_dc
    assert 4.9 < voltage < 5.1

    psu.output_enabled = False
```

## Mock Instruments

For testing without hardware, Litmus provides a `Mock` factory that works with any class:

```python
from pymeasure.instruments.keithley import Keithley2400
from litmus.instruments import Mock

# Create mock that passes isinstance checks
smu = Mock(Keithley2400, voltage=5.0, current=1.5e-6)

assert isinstance(smu, Keithley2400)
assert smu.voltage == 5.0
```

### Mock Configuration

Mock supports three value types:

```python
from litmus.instruments import Mock

# Simple values - always returned
dmm = Mock(object, measure_voltage=3.31)
dmm.measure_voltage()  # Returns 3.31

# Dict lookup - first argument is key
inst = Mock(object, query={
    "MEAS:VOLT:DC?": "3.31",
    "MEAS:CURR:DC?": "0.1",
    "*IDN?": "Keysight,34461A,SN123,1.0",
})
inst.query("MEAS:VOLT:DC?")  # Returns "3.31"

# Callable - full control
inst = Mock(object, query=lambda cmd: "3.31" if "VOLT" in cmd else "0.0")
```

### Station Mock Config

Configure mocks in station YAML:

```yaml
# stations/dev_station.yaml
id: dev_station
name: "Development Station"

instruments:
  dmm:
    type: dmm
    catalog_ref: generic_dmm
    mock: true  # Use mock mode
    mock_config:
      measure_dc_voltage: 3.31
      measure_dc_current: 0.1

  psu:
    type: psu
    catalog_ref: generic_psu
    mock: true
    mock_config:
      measure_voltage: 5.0
      measure_current: 0.25
```

Run with mocks:
```bash
pytest tests/ --station=dev_station --mock-instruments --dut-serial=TEST001
```

## Discovery

Scan for available VISA instruments:

```python
from litmus.instruments import discover_visa, get_info_visa

# Find all instruments
resources = discover_visa()
# ["TCPIP::192.168.1.100::INSTR", "USB0::0x1234::0x5678::SN123::INSTR"]

# Get identity info
info = get_info_visa("TCPIP::192.168.1.100::INSTR")
# InstrumentInfo(manufacturer="Keysight", model="34461A", serial="SN123")
```

Or use the CLI:
```bash
litmus discover
```

## Integration Patterns

### pytest (Recommended)

Station roles become fixtures automatically:

```python
# Station config has dmm and psu → fixtures auto-registered
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    psu.voltage = context.get_param("vin", 5.0)
    psu.output_enabled = True
    return dmm.voltage_dc
```

### Custom Fixture Override

Override auto-registered fixtures for custom setup/teardown:

```python
# tests/conftest.py
import pytest

@pytest.fixture(scope="session")
def psu(instruments):
    """Custom PSU with safety defaults."""
    inst = instruments["psu"]
    inst.current_limit = 0.5  # Safety limit
    yield inst
    inst.output_enabled = False  # Always disable on teardown
```

### Standalone Script

```python
#!/usr/bin/env python3
import pyvisa
from litmus.instruments import Mock

def measure_voltage(resource: str, mock: bool = False) -> float:
    if mock:
        dmm = Mock(object, query={"MEAS:VOLT:DC?": "3.31"})
    else:
        rm = pyvisa.ResourceManager('@py')
        dmm = rm.open_resource(resource)

    try:
        voltage = float(dmm.query("MEAS:VOLT:DC?"))
        return voltage
    finally:
        if not mock:
            dmm.close()

if __name__ == "__main__":
    import sys
    mock = "--mock" in sys.argv
    v = measure_voltage("TCPIP::192.168.1.100::INSTR", mock=mock)
    print(f"Voltage: {v} V")
```

## Traceability

Every measurement records which instrument took it:

```python
# Result Parquet includes:
# - instrument_serial: "SN123456"
# - instrument_model: "34461A"
# - instrument_cal_due: "2024-06-15"
# - instrument_firmware: "1.0.2"
```

Configure calibration info in station:

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    calibration:
      due_date: 2024-06-15
      last_cal: 2023-06-15
      certificate: "CAL-2023-1234"
      lab: "Acme Calibration"
```

## Next Steps

- [Adding Instruments](../guides/adding-instruments.md) — Station configuration details
- [Mock Mode](../guides/mock-mode.md) — Testing without hardware
- [Stations](../concepts/stations.md) — Station architecture
