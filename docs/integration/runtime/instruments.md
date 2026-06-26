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

For raw PyVISA (no driver class), supply only `resource:` in your station YAML. Litmus opens the
resource via `rm.open_resource(resource)`:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Test Bench 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    # no driver: — Litmus opens via rm.open_resource(resource)
```

The `driver:` field is for a custom driver class that takes a resource string as its only argument.
`pyvisa.resources.MessageBasedResource` is not user-instantiable — omit `driver:` for raw PyVISA
and let the loader open it directly.

The pytest plugin constructs the driver and exposes it as a fixture:

```python
def test_voltage(dmm, measure):
    # dmm is a pyvisa MessageBasedResource opened by rm.open_resource()
    voltage = float(dmm.query("MEAS:VOLT:DC?"))
    measure("voltage", voltage)
```

## Using PyMeasure Drivers

PyMeasure provides high-level drivers for 100+ instruments. PyMeasure drivers take a resource
string as their first argument, which matches Litmus's calling convention exactly:

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
def test_output_voltage(psu, dmm, measure):
    # PyMeasure provides high-level methods
    psu.voltage = 5.0
    psu.output_enabled = True

    measure("output_voltage", dmm.voltage_dc)

    psu.output_enabled = False
```

**Driver instantiation rule:** When `driver:` is set, Litmus calls `driver_class(resource)` with
the resource string as the only argument. Any class that accepts a resource string first arg works
(PyMeasure, custom `VisaInstrument` subclasses, any SCPI wrapper with that convention). Omit
`driver:` and supply `resource:` alone for raw PyVISA.

## Mock Instruments

For testing without hardware, Litmus provides a `Mock` factory that works with any class:

```python
from pymeasure.instruments.keithley import Keithley2400
from litmus import Mock

# Create mock that passes isinstance checks
smu = Mock(Keithley2400, voltage=5.0, current=1.5e-6)

assert isinstance(smu, Keithley2400)
assert smu.voltage == 5.0
```

Set `mock: true` on the instrument in station YAML and list method return values under
`mock_config:`; run with `--mock-instruments` to force mock mode for every instrument:

```yaml
instruments:
  dmm:
    type: dmm
    driver: my_pkg.MyDMM
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true
    mock_config:
      measure_dc_voltage: 3.31
      measure_dc_current: 0.1
```

For the full `Mock(...)` value types (scalar / dict / callable), `simulate=True`, and
conftest-tier mocking, see [Custom drivers — Running without hardware](../../how-to/configuration/custom-drivers.md).

## Discovery

Scan for available VISA instruments:

```python
from litmus.instruments.discovery import discover_visa, get_info_visa

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
def test_output_voltage(context, psu, dmm, measure):
    # context exposes the active run's DUT, station, and condition values;
    # get_param("vin", 5.0) reads a sweep/condition input (default 5.0).
    # See: reference/pytest/fixtures.md
    psu.voltage = context.get_param("vin", 5.0)
    psu.output_enabled = True
    measure("output_voltage", dmm.voltage_dc)
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
from litmus import Mock

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

Every measurement records the instrument that took it (name, serial, model, firmware, resource),
joined from the instrument asset YAML. For the per-step column layout, see
[query API reference](../../reference/query-api.md).

### Instrument Asset Files

Per-device identity and calibration live in `instruments/<id>.yaml` (the `InstrumentAssetFile`
schema). The `id:` field in the asset YAML must match the instrument's role key in the station
YAML — that is how the loader joins them at session start.

```yaml
# instruments/dmm.yaml     ← filename and id both match the station role "dmm"
id: dmm
protocol: visa
driver: pymeasure.instruments.keysight.Keysight34461A
resource: "TCPIP::192.168.1.100::INSTR"
catalog_ref: keysight/34461a
info:
  manufacturer: "Keysight"
  model: "34461A"
  serial: "MY12345678"
  firmware: "A.03.10"
calibration:
  due_date: 2025-06-15
  last_cal: 2024-06-15
  certificate: "CAL-2024-1234"
  lab: "Acme Calibration"
```

The `info:` block (`manufacturer`, `model`, `serial`, `firmware`) holds the identity the loader
verifies against the live `*IDN?` response at session start. The `calibration:` block is
configuration only — it is not queried from the device. Litmus emits a warning if
`calibration.due_date` is in the past or within 30 days.

The station YAML carries the instrument's role, driver, and resource. The asset file carries
identity and calibration for that physical unit. They join on the role key:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Test Bench 1"

instruments:
  dmm:                                              # role key
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    # instruments/dmm.yaml (id: dmm) is joined here automatically
```

`catalog_ref:` on either the station instrument entry or the asset file points at the catalog
entry the capability matcher reads — see [catalog schema](../../reference/catalog/schema.md).

## Next Steps

- [Custom drivers](../../how-to/configuration/custom-drivers.md) — Build a non-VISA driver
- [Mock Mode](../../how-to/configuration/mock-mode.md) — Testing without hardware
- [Stations](../../concepts/configuration/stations.md) — Station architecture
