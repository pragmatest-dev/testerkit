# Adopting Litmus

Litmus is designed for incremental adoption. You don't have to migrate everything at once — start with what provides the most value and expand from there.

## Adoption Paths

| Path | What You Get | Effort |
|------|-------------|--------|
| [Results API](results-api.md) | Unified results storage | Low |
| [Test Harness](harness.md) | Measurement tracking, limits | Medium |
| [Instrument Drivers](instruments.md) | Simulation, capability matching | Medium |
| [Full Framework](../tutorial/index.md) | Complete pytest integration | High |

## Start Where You Are

### "We use LabVIEW/TestStand"

Start with the **Results API**. Keep your existing tests, just send results to Litmus:

```python
# From any language/tool that can call Python
from litmus import LitmusClient

client = LitmusClient()
run = client.start_run(
    dut_serial="SN12345",
    station_id="teststand_1",
    test_sequence_id="imported_test",
)

with run.step("voltage_test") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)

run.finish()
```

Benefits:
- Unified results across tools
- Parquet storage for analytics
- Same UI for all test data

→ [Results API Guide](results-api.md)

### "We have pytest tests already"

Add the **Test Harness** to existing tests:

```python
# Existing test
def test_voltage():
    voltage = measure_voltage()
    assert 3.0 < voltage < 3.6

# With Litmus harness
from litmus.execution.harness import TestHarness

def test_voltage():
    harness = TestHarness("test_voltage")
    voltage = measure_voltage()
    harness.measure("voltage", voltage, low=3.0, high=3.6)
    harness.finish()
```

Benefits:
- Keep existing test structure
- Add measurement tracking
- Optional limits from YAML

→ [Test Harness Guide](harness.md)

### "We want Litmus instruments"

Use Litmus **instrument drivers** standalone:

```python
from litmus.instruments import DMM

# Real or simulated based on flag
dmm = DMM("TCPIP::192.168.1.100::INSTR", mock=True)
dmm.connect()
voltage = dmm.measure_voltage()
dmm.disconnect()
```

Benefits:
- Unified simulation mode
- Capability-based matching
- Works with any test framework

→ [Instrument Drivers Guide](instruments.md)

### "We're starting fresh"

Follow the **full tutorial** for complete integration:

```python
def test_voltage(dmm, logger):
    logger.measure("voltage", dmm.measure_voltage())
```

Benefits:
- Full pytest integration
- YAML configuration
- Automatic result storage
- Capability matching

→ [Tutorial](../tutorial/index.md)

## What You Give Up

Each integration level has trade-offs:

| Level | What You Keep | What You Miss |
|-------|--------------|---------------|
| Results API only | All existing code | Capability matching, instrument simulation |
| Harness only | Existing test structure | pytest-native fixtures, vector expansion |
| Instruments only | Existing framework | Automatic result capture |
| Full framework | — | Existing tests need migration |

## Migration Strategy

### Phase 1: Results Collection

1. Install Litmus
2. Add `LitmusClient` calls to existing tests
3. View all results in unified UI

### Phase 2: Instrument Drivers

1. Create station configs for existing benches
2. Use Litmus drivers with `mock=True` for CI
3. Gradually adopt capability matching

### Phase 3: Test Harness

1. Add `TestHarness` to tests needing traceability
2. Move limits to YAML configuration
3. Link limits to product specs

### Phase 4: Full Integration

1. New tests use the pytest-native `context`/`spec`/`logger` fixtures
2. Migrate high-value tests
3. Keep legacy tests with Results API

## Coexistence

Litmus components work independently. You can:

- Use Results API from LabVIEW while writing new tests in pytest
- Use Litmus instruments with Robot Framework
- Mix Litmus-aware pytest tests (using `context`/`spec`/`logger`) with plain pytest tests

## Getting Help

- [Results API](results-api.md) — Submit results from any source
- [Test Harness](harness.md) — Add tracking to existing tests
- [Instrument Drivers](instruments.md) — Use drivers standalone
- [OpenHTF Adapter](openhtf-adapter.md) — Migrate OpenHTF tests
- [Existing pytest](pytest-existing.md) — Add Litmus to pytest projects
