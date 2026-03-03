# OpenHTF Migration

Migrate existing OpenHTF test suites to Litmus while preserving your test logic.

## Overview

OpenHTF and Litmus share similar concepts:
- Test phases ↔ Test steps
- Measurements ↔ Measurements
- Plugs ↔ User's driver classes
- Station configs ↔ Station configs

Litmus does not provide instrument drivers — you bring your own (PyMeasure, PyVISA, vendor SDKs, or even your existing OpenHTF plugs refactored as plain classes). Litmus provides the infrastructure around them: discovery, identity verification, calibration tracking, and a Mock factory for simulation.

This guide shows how to migrate incrementally.

## Concept Mapping

| OpenHTF | Litmus | Notes |
|---------|--------|-------|
| `@measures` decorator | `@litmus_test` | Return values become measurements |
| `Measurement` | `Measurement` | Similar API |
| `Plug` | User's driver class | Any Python class — PyMeasure, custom, or refactored plug |
| `PhaseResult` | `Outcome` | PASS, FAIL, SKIP, ERROR |
| `test_record` | `TestRun` | Results storage |
| `Test` class | pytest test file | Litmus uses pytest natively |

## Migration Strategies

### Strategy 1: Results Bridge

Keep OpenHTF tests, send results to Litmus via HTTP API:

```python
# openhtf_bridge.py
import requests

LITMUS_API = "http://localhost:8000/api"

def on_test_complete(test_record):
    """OpenHTF output callback to send results to Litmus."""
    run_data = {
        "dut_serial": test_record.dut_id,
        "station_id": test_record.station_id,
        "test_sequence_id": "openhtf_import",
        "steps": [],
    }

    for phase in test_record.phases:
        step = {"name": phase.name, "measurements": []}
        for m in phase.measurements.values():
            step["measurements"].append({
                "name": m.name,
                "value": m.measured_value,
                "units": m.units,
                "low": m.validators[0].minimum if m.validators else None,
                "high": m.validators[0].maximum if m.validators else None,
            })
        run_data["steps"].append(step)

    requests.post(f"{LITMUS_API}/runs", json=run_data)
```

### Strategy 2: Parallel Tests

Run both OpenHTF and Litmus tests during migration:

```python
# tests/test_voltage_litmus.py
from litmus.execution import litmus_test

@litmus_test
def test_voltage(context, dmm):
    """Litmus version of voltage test."""
    return dmm.measure_dc_voltage()
```

```python
# openhtf_tests/test_voltage.py
import openhtf as htf

@htf.measures(htf.Measurement('voltage').in_range(3.0, 3.6))
def test_voltage(test):
    """OpenHTF version of voltage test."""
    return test.plugs['dmm'].measure_voltage()
```

### Strategy 3: Full Migration

Convert OpenHTF tests to Litmus:

**Before (OpenHTF):**
```python
import openhtf as htf
from openhtf.util import validators

@htf.measures(
    htf.Measurement('input_voltage').in_range(4.5, 5.5).with_units('V'),
    htf.Measurement('output_voltage').in_range(3.0, 3.6).with_units('V'),
)
def power_test(test):
    test.plugs['psu'].set_voltage(5.0)
    test.plugs['psu'].output_on()

    test.measurements.input_voltage = test.plugs['dmm'].measure_voltage()

    # Switch to output
    test.measurements.output_voltage = test.plugs['dmm'].measure_voltage()

    test.plugs['psu'].output_off()
```

**After (Litmus):**
```python
from litmus.execution import litmus_test

@litmus_test
def test_power(context, psu, dmm):
    """Power test migrated from OpenHTF."""
    psu.set_voltage(5.0)
    psu.enable_output()

    input_voltage = dmm.measure_voltage()
    output_voltage = dmm.measure_voltage()  # After switching

    psu.disable_output()

    return {
        "input_voltage": input_voltage,
        "output_voltage": output_voltage,
    }
```

**Config (tests/config.yaml):**
```yaml
test_power:
  limits:
    input_voltage:
      low: 4.5
      high: 5.5
      units: V
    output_voltage:
      low: 3.0
      high: 3.6
      units: V
```

## Plug to Driver Class Migration

OpenHTF plugs can be migrated in two ways: reuse an existing driver library (e.g., PyMeasure) or refactor your plug into a standalone class.

### OpenHTF Plug

```python
import openhtf as htf
from openhtf import plugs

class DmmPlug(plugs.BasePlug):
    def __init__(self, resource):
        self.resource = resource
        self._inst = None

    def setup(self):
        import visa
        rm = visa.ResourceManager()
        self._inst = rm.open_resource(self.resource)

    def teardown(self):
        self._inst.close()

    def measure_voltage(self):
        return float(self._inst.query("MEAS:VOLT:DC?"))
```

### Option A: Use an existing driver library

If a library like PyMeasure already supports your instrument, use it directly:

```python
# drivers/dmm.py — no code needed, just reference in station config
# PyMeasure already provides Keithley2000, Agilent34401A, etc.
```

```yaml
# stations/bench_1.yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keithley.Keithley2000
    resource: "TCPIP::192.168.1.100::INSTR"
```

### Option B: Refactor your plug into a plain class

Strip the OpenHTF base class and keep the instrument logic:

```python
# drivers/dmm.py
import pyvisa

class MyDMM:
    """Refactored from DmmPlug — no framework dependency."""

    def __init__(self, resource: str):
        self.resource = resource
        self._inst = None

    def connect(self):
        rm = pyvisa.ResourceManager()
        self._inst = rm.open_resource(self.resource)

    def disconnect(self):
        if self._inst:
            self._inst.close()

    def measure_voltage(self) -> float:
        return float(self._inst.query("MEAS:VOLT:DC?"))

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
```

```yaml
# stations/bench_1.yaml
instruments:
  dmm:
    type: dmm
    driver: drivers.dmm.MyDMM
    resource: "TCPIP::192.168.1.100::INSTR"
```

### Simulation with Mock factory

Litmus provides a generic Mock factory that works with any driver class — no simulation code required in your driver:

```python
from litmus.instruments import Mock
from drivers.dmm import MyDMM

# Mock wraps any class — all methods become no-ops unless configured
dmm = Mock(MyDMM, measure_voltage=3.3)
dmm.measure_voltage()  # → 3.3
dmm.connect()           # → None (no-op)

# Dict values for command-response patterns
dmm = Mock(MyDMM, query={"MEAS:VOLT:DC?": "3.300", "*IDN?": "Keithley,2000,..."})

# Callable values for dynamic behavior
import random
dmm = Mock(MyDMM, measure_voltage=lambda: 3.3 + random.gauss(0, 0.01))
```

## Station Config Migration

### OpenHTF

```python
# station_config.py
STATION_CONFIG = {
    'dmm': {
        'resource': 'TCPIP::192.168.1.100::INSTR',
    },
    'psu': {
        'resource': 'GPIB0::5::INSTR',
    },
}
```

### Litmus

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Production Bench 1"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keithley.Keithley2000
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: psu
    driver: drivers.psu.MyPSU
    resource: "GPIB0::5::INSTR"
```

Litmus also supports instrument asset files for identity verification and calibration tracking:

```yaml
# instruments/keithley_dmm_001.yaml
id: keithley_dmm_001
protocol: visa
driver: pymeasure.instruments.keithley.Keithley2000

info:
  manufacturer: Keithley
  model: "2000"
  serial: "1234567"
  firmware: "A02"

calibration:
  due_date: 2026-06-15
  last_cal: 2025-06-15
  certificate: CAL-2025-042
  lab: Acme Calibration
```

## Measurement Migration

### OpenHTF Measurement

```python
htf.Measurement('voltage')
    .in_range(3.0, 3.6)
    .with_units('V')
    .doc('Output voltage measurement')
```

### Litmus Measurement (inline)

```python
from litmus.data import Measurement

m = Measurement(
    name="voltage",
    value=dmm.measure_voltage(),
    units="V",
    low_limit=3.0,
    high_limit=3.6,
)
m.check_limit()
```

### Litmus Measurement (config)

```yaml
# tests/config.yaml
test_voltage:
  limits:
    voltage:
      low: 3.0
      high: 3.6
      units: V
```

## Test Execution Migration

### OpenHTF

```python
import openhtf as htf

test = htf.Test(power_test)
test.configure(
    plugs=[('dmm', DmmPlug, {'resource': 'TCPIP::...'})]
)
test.execute()
```

### Litmus

```bash
pytest tests/test_power.py --station=bench_1 --dut-serial=SN12345
```

## Gradual Migration Plan

### Phase 1: Results Bridge

1. Install Litmus alongside OpenHTF
2. Add results bridge callback (Strategy 1 above)
3. Verify results appear in Litmus UI
4. Continue running OpenHTF tests

### Phase 2: Station Setup

1. Run `litmus discover` to scan connected instruments
2. Create station configs with `litmus station init`
3. Create instrument asset files for calibration tracking
4. Test Mock factory with your driver classes

### Phase 3: Test Migration

1. Start with simple tests
2. Migrate one test file at a time
3. Run both versions in parallel (Strategy 2)
4. Validate results match
5. Deprecate OpenHTF versions

### Phase 4: Full Cutover

1. Run Litmus tests in production
2. Monitor for issues
3. Archive OpenHTF tests
4. Remove OpenHTF dependency

## Benefits of Migration

| Aspect | OpenHTF | Litmus |
|--------|---------|--------|
| Framework | Custom executor | pytest (familiar) |
| Simulation | DIY per plug | Generic Mock factory (any class) |
| Configuration | Python dicts | YAML with Pydantic validation |
| AI Integration | None | MCP server + HTTP API |
| Instrument discovery | Manual | Multi-protocol (VISA/NI/serial) |
| Calibration tracking | Not supported | Built-in with expiration warnings |
| Identity verification | Not supported | Auto-verify *IDN? at runtime |

## Next Steps

- [Results API](results-api.md) — Bridge results during migration
- [Tutorial](../tutorial/index.md) — Learn Litmus patterns
