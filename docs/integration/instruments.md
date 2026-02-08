# Instrument Drivers Integration

Use Litmus instrument drivers with any test framework — pytest, Robot Framework, unittest, or custom scripts.

## Overview

Litmus instrument drivers provide:
- Unified interface across instrument types
- Built-in simulation mode
- Capability-based matching
- Float precision for measurements

## Quick Start

```python
from litmus.instruments import DMM

# Real hardware
dmm = DMM("TCPIP::192.168.1.100::INSTR")
dmm.connect()
voltage = dmm.measure_voltage()
dmm.disconnect()

# Simulated (same interface)
dmm = DMM("TCPIP::192.168.1.100::INSTR", mock=True, sim_config={"voltage": 3.3})
dmm.connect()
voltage = dmm.measure_voltage()  # Returns 3.3
dmm.disconnect()
```

## Available Drivers

| Type | Class | Capabilities |
|------|-------|--------------|
| Digital Multimeter | `DMM` | voltage, current, resistance |
| Power Supply | `PSU` | voltage/current output |
| Electronic Load | `ELoad` | current sink |
| Oscilloscope | `Scope` | voltage (AC), frequency |
| Function Generator | `FuncGen` | waveform output |

## Usage Patterns

### Context Managers

```python
from litmus.instruments import DMM, PSU

with DMM("TCPIP::192.168.1.100::INSTR") as dmm:
    voltage = dmm.measure_voltage()
# Automatically disconnects
```

### Multiple Instruments

```python
from litmus.instruments import DMM, PSU

with PSU("GPIB0::5::INSTR") as psu, DMM("TCPIP::192.168.1.100::INSTR") as dmm:
    psu.set_voltage(5.0)
    psu.enable_output()
    voltage = dmm.measure_voltage()
    psu.disable_output()
```

### Mock Mode Flag

```python
import os
from litmus.instruments import DMM, Mock

# Environment-based mock mode
mock_mode = os.environ.get("LITMUS_MOCK_INSTRUMENTS", "") == "1"

if mock_mode:
    dmm = Mock(DMM, voltage=3.31)
else:
    dmm = DMM("TCPIP::192.168.1.100::INSTR")

with dmm:
    voltage = dmm.measure_voltage()
```

## Mock Instruments

For unit tests and CI, use mock instruments (no I/O overhead):

```python
from litmus.instruments import DMM, PSU, ELoad, Mock

# Instant mock responses
dmm = Mock(DMM, voltage=3.31, current=0.1)
dmm.connect()

v = dmm.measure_voltage()  # Returns 3.31

# Update simulated values
dmm.set_value("voltage", 5.0)
v = dmm.measure_voltage()  # Returns 5.0
```

### Mock vs mock=True

| Feature | Mock(DMM) | DMM(mock=True) |
|---------|---------|-------------------|
| I/O overhead | None | pyvisa-sim |
| Realistic timing | No | Yes |
| Tests driver logic | No | Yes |
| Speed | Instant | ~5-50ms/call |
| Use case | Unit tests | Integration tests |

## Integration Examples

### With pytest

When using the Litmus plugin with a station config, instrument role fixtures are auto-registered. No conftest boilerplate needed:

```python
# Station config defines dmm with driver path → fixture is auto-available
def test_voltage(dmm):
    voltage = dmm.measure_voltage()
    assert float(voltage) > 3.0
```

For custom lifecycle management, override in conftest:

```python
import pytest

@pytest.fixture(scope="session")
def dmm(instruments):
    """Custom DMM with range configuration."""
    inst = instruments["dmm"]
    inst.configure_voltage_range("AUTO")
    return inst

def test_voltage(dmm):
    voltage = dmm.measure_voltage()
    assert float(voltage) > 3.0
```

### With Robot Framework

```python
# litmus_keywords.py
from litmus.instruments import DMM, PSU

class LitmusKeywords:
    def __init__(self):
        self.instruments = {}

    def connect_dmm(self, resource, simulate=False):
        self.instruments["dmm"] = DMM(resource, simulate=simulate)
        self.instruments["dmm"].connect()

    def measure_voltage(self):
        return float(self.instruments["dmm"].measure_voltage())

    def disconnect_all(self):
        for inst in self.instruments.values():
            inst.disconnect()
```

```robot
*** Settings ***
Library    litmus_keywords.LitmusKeywords

*** Test Cases ***
Test Voltage
    Connect DMM    TCPIP::192.168.1.100::INSTR    mock=True
    ${voltage}=    Measure Voltage
    Should Be True    ${voltage} > 3.0
    [Teardown]    Disconnect All
```

### With unittest

```python
import unittest
from litmus.instruments import DMM, Mock

class TestVoltage(unittest.TestCase):
    def setUp(self):
        self.dmm = Mock(DMM, voltage=3.31)
        self.dmm.connect()

    def tearDown(self):
        self.dmm.disconnect()

    def test_measure_voltage(self):
        voltage = self.dmm.measure_voltage()
        self.assertGreater(float(voltage), 3.0)
```

### Standalone Script

```python
#!/usr/bin/env python3
from litmus.instruments import DMM, PSU

def run_test(serial: str, simulate: bool = False):
    with PSU("GPIB0::5::INSTR", simulate=simulate) as psu, \
         DMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:

        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        voltage = dmm.measure_voltage()
        print(f"DUT {serial}: {voltage}V")

        psu.disable_output()

        return float(voltage) > 3.0

if __name__ == "__main__":
    import sys
    success = run_test(sys.argv[1], simulate="--mock-instruments" in sys.argv)
    sys.exit(0 if success else 1)
```

## Capability Interfaces

Instruments implement capability interfaces for type-safe code:

```python
from litmus.capabilities.interfaces import VoltageInput, VoltageOutput

def measure_and_source(
    meter: VoltageInput,
    supply: VoltageOutput,
):
    """Works with any instrument implementing these capabilities."""
    supply.set_voltage(5.0)
    supply.enable_output()
    return meter.measure_voltage()

# Works with any compatible instruments
from litmus.instruments import DMM, PSU

dmm = DMM("TCPIP::192.168.1.100::INSTR")
psu = PSU("GPIB0::5::INSTR")
voltage = measure_and_source(dmm, psu)
```

## Station Configuration

Load instruments from YAML:

```python
from litmus.config.loader import load_station

station = load_station("stations/bench_1.yaml")

dmm = station.get_instrument("dmm")
psu = station.get_instrument("psu")
```

Station YAML:
```yaml
# stations/bench_1.yaml
station:
  id: bench_1

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
```

## Custom Drivers

Create drivers for custom instruments:

```python
from litmus.instruments.base import Instrument
from litmus.capabilities.interfaces import VoltageInput

class MyCustomDMM(Instrument, VoltageInput):
    """Custom DMM driver."""

    def __init__(self, port: str, simulate: bool = False, sim_config: dict = None):
        super().__init__(simulate=simulate, sim_config=sim_config)
        self.port = port
        self._sim_voltage = float(sim_config.get("voltage", 0)) if sim_config else 0.0

    def connect(self):
        if self.simulate:
            self._connected = True
            return
        # Real connection logic
        self._connected = True

    def disconnect(self):
        self._connected = False

    def measure_voltage(self, signal_type=None) -> float:
        if self.simulate:
            return self._sim_voltage
        # Real measurement logic
        return 3.31
```

## Next Steps

- [Adding Instruments](../guides/adding-instruments.md) — Create custom drivers
- [Capabilities](../concepts/capabilities.md) — Understanding capability matching
- [Stations](../concepts/stations.md) — Station configuration
