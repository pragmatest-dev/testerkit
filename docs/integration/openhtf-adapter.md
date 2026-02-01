# OpenHTF Migration

Migrate existing OpenHTF test suites to Litmus while preserving your test logic.

## Overview

OpenHTF and Litmus share similar concepts:
- Test phases ↔ Test steps
- Measurements ↔ Measurements
- Plugs ↔ Instruments
- Station configs ↔ Station configs

This guide shows how to migrate incrementally.

## Concept Mapping

| OpenHTF | Litmus | Notes |
|---------|--------|-------|
| `@measures` decorator | `@litmus_test` | Return values become measurements |
| `Measurement` | `Measurement` | Similar API |
| `Plug` | Instrument driver | Litmus uses capability interfaces |
| `PhaseResult` | `Outcome` | PASS, FAIL, SKIP, ERROR |
| `test_record` | `TestRun` | Results storage |
| `Test` class | pytest test file | Litmus uses pytest natively |

## Migration Strategies

### Strategy 1: Results Bridge

Keep OpenHTF tests, send results to Litmus:

```python
# openhtf_bridge.py
from litmus import LitmusClient

def on_test_complete(test_record):
    """OpenHTF callback to send results to Litmus."""
    client = LitmusClient()

    run = client.start_run(
        dut_serial=test_record.dut_id,
        station_id=test_record.station_id,
        test_sequence_id="openhtf_import",
    )

    for phase in test_record.phases:
        with run.step(phase.name) as step:
            for m in phase.measurements.values():
                step.measure(
                    name=m.name,
                    value=m.measured_value,
                    units=m.units,
                    low=m.validators[0].minimum if m.validators else None,
                    high=m.validators[0].maximum if m.validators else None,
                )

    run.finish()
```

### Strategy 2: Parallel Tests

Run both OpenHTF and Litmus tests during migration:

```python
# tests/test_voltage_litmus.py
from litmus.execution import litmus_test

@litmus_test
def test_voltage(context, instruments):
    """Litmus version of voltage test."""
    return instruments["dmm"].measure_voltage()
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
def test_power(context, instruments):
    """Power test migrated from OpenHTF."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

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

## Plug to Instrument Migration

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

### Litmus Instrument

```python
from litmus.instruments import DMM

# Direct use (no custom class needed for SCPI instruments)
dmm = DMM("TCPIP::192.168.1.100::INSTR")
dmm.connect()
voltage = dmm.measure_voltage()  # Returns float
dmm.disconnect()

# Or with context manager
with DMM("TCPIP::192.168.1.100::INSTR") as dmm:
    voltage = dmm.measure_voltage()
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
station:
  id: bench_1
  name: "Production Bench 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
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

### Week 1-2: Results Bridge

1. Install Litmus alongside OpenHTF
2. Add results bridge callback
3. Verify results appear in Litmus UI
4. Continue running OpenHTF tests

### Week 3-4: Instrument Drivers

1. Create station configs for existing benches
2. Test Litmus drivers with simulation
3. Replace OpenHTF plugs with Litmus instruments
4. Verify measurements match

### Week 5-8: Test Migration

1. Start with simple tests
2. Migrate one test file at a time
3. Run both versions in parallel
4. Validate results match
5. Deprecate OpenHTF versions

### Week 9+: Full Cutover

1. Run Litmus tests in production
2. Monitor for issues
3. Archive OpenHTF tests
4. Remove OpenHTF dependency

## Benefits of Migration

| Aspect | OpenHTF | Litmus |
|--------|---------|--------|
| Framework | Custom | pytest (familiar) |
| Simulation | Custom plugs | Built-in |
| Configuration | Python | YAML |
| AI Integration | None | MCP server |
| Capability matching | None | Automatic |
| Community | Small | Large (pytest) |

## Next Steps

- [Results API](results-api.md) — Bridge results during migration
- [Instrument Drivers](instruments.md) — Replace plugs
- [Tutorial](../tutorial/index.md) — Learn Litmus patterns
