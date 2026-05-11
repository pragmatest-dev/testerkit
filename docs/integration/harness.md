# Test Harness Integration

> **For new pytest projects, use the pytest-native three-fixture split (`context`, `verify`,
> `logger`) documented in [pytest-native Reference](../reference/pytest-native.md).** The
> `TestHarness` API documented here is for integrating Litmus into existing tests, non-pytest
> runners, or custom harnesses where you need explicit lifecycle control.

Add Litmus measurement tracking to existing tests without restructuring your test code.

## Overview

The TestHarness provides:
- Measurement recording with limits
- Automatic pass/fail determination
- Result storage to Parquet
- Optional YAML configuration

## Quick Start

```python
from litmus.execution.harness import TestHarness

def test_voltage():
    """Existing test with Litmus tracking."""
    harness = TestHarness("test_voltage")

    # Your existing measurement code
    voltage = measure_voltage()

    # Add Litmus tracking
    harness.measure("output_voltage", voltage, units="V", low=3.0, high=3.6)

    harness.finish()
```

## TestHarness API

### Creating a Harness

```python
from litmus.execution.harness import TestHarness

# Basic usage
harness = TestHarness(step_name="my_test")

# With DUT info
harness = TestHarness(
    step_name="my_test",
    dut_serial="SN12345",
    station_id="bench_1",
)

# With spec context for automatic limits
from litmus.products import ProductContext
spec = ProductContext.from_file("products/my_product.yaml")
harness = TestHarness(
    step_name="my_test",
    product_context=spec,
)
```

### Recording Measurements

```python
# With inline limits
harness.measure(
    name="output_voltage",
    value=3.31,
    units="V",
    low=3.0,
    high=3.6,
)

# With spec-derived limits (if product_context provided)
harness.measure(
    name="output_voltage",
    value=3.31,
)

# Multiple measurements
harness.measure("voltage_1", v1, units="V", low=3.0, high=3.6)
harness.measure("voltage_2", v2, units="V", low=3.0, high=3.6)
harness.measure("current", i, units="A", low=0, high=1.0)
```

### Test Vectors

For parametrized tests:

```python
harness = TestHarness("sweep_test")

for voltage in [3.3, 5.0, 12.0]:
    with harness.vector(input_voltage=voltage) as vec:
        output = measure_at_voltage(voltage)
        vec.measure("output", output, units="V")

harness.finish()
```

### Finishing

```python
# Normal completion
result = harness.finish()
print(result.outcome)  # PASS or FAIL

# With context manager (auto-finish)
with TestHarness("my_test") as harness:
    harness.measure("voltage", 3.31, low=3.0, high=3.6)
# Automatically calls finish()
```

## Integration Patterns

### Wrapping Existing Tests

```python
# Before: Simple assert
def test_voltage():
    v = measure_voltage()
    assert 3.0 < v < 3.6

# After: With Litmus tracking
def test_voltage():
    harness = TestHarness("test_voltage")
    v = measure_voltage()
    harness.measure("voltage", v, units="V", low=3.0, high=3.6)
    harness.finish()
    assert harness.outcome == "PASS"  # Optional: fail test if measurement fails
```

### With pytest Fixtures

```python
import pytest
from litmus.execution.harness import TestHarness

@pytest.fixture
def harness(request):
    """Create harness for each test."""
    h = TestHarness(request.node.name)
    yield h
    h.finish()

def test_voltage(harness):
    v = measure_voltage()
    harness.measure("voltage", v, units="V", low=3.0, high=3.6)

def test_current(harness):
    i = measure_current()
    harness.measure("current", i, units="A", low=0, high=1.0)
```

### With Configuration

Pass limits directly when creating a harness:

```python
from litmus.execution.harness import TestHarness

harness = TestHarness(
    step_name="test_voltage",
    limits={"voltage": {"low": 3.0, "high": 3.6, "units": "V"}},
)

v = measure_voltage()
harness.measure("voltage", v)  # Limits from config
harness.finish()
```

In pytest-native mode, limits come from sequence step config, `@pytest.mark.litmus_limits`, sidecar YAML, or the active product spec (in that order). See [Test Configuration](../tutorial/05-configuration.md).

### With Spec-Driven Limits

```python
from litmus.execution.harness import TestHarness
from litmus.products import ProductContext

spec = ProductContext.from_file("products/power_board.yaml", guardband_pct=10)

harness = TestHarness(
    step_name="test_output",
    product_context=spec,
)

v = measure_voltage()
harness.measure("output_voltage", v)  # Limits from spec with guardband
harness.finish()
```

## Non-measurement steps

Every pytest-native test already opens a logger step around its body, so
setup helpers and dialog functions don't need a decorator to be tracked.
Write them as plain Python and call them from the test:

```python
def verify_dut_connection(psu):
    psu.set_voltage(0.1)
    assert psu.measure_current() < 0.001, "DUT appears shorted!"

def test_output_voltage(psu, dmm, eload, verify):
    verify_dut_connection(psu)
    psu.set_voltage(5.0); psu.enable_output()
    eload.set_current(0.5); eload.enable()
    verify("output_voltage", float(dmm.measure_dc_voltage()))
```

Any helper can be async — pytest-asyncio handles the test itself, and
the helper is just awaited normally.

## Advanced Features

### Change Detection

For tests with nested loops:

```python
harness = TestHarness("nested_test")

for temp in [25, 85]:
    for load in [0, 50, 100]:
        with harness.vector(temperature=temp, load=load) as vec:
            # Check if temperature changed
            if vec.changed("temperature"):
                set_chamber_temperature(temp)
                time.sleep(60)  # Wait for stabilization

            set_load(load)
            output = measure()
            vec.measure("output", output)

harness.finish()
```

### Error Handling

```python
harness = TestHarness("risky_test")

try:
    v = measure_voltage()
    harness.measure("voltage", v, low=3.0, high=3.6)
except InstrumentError as e:
    harness.error(f"Measurement failed: {e}")
finally:
    harness.finish()
```

### Hierarchical Context

The TestHarness provides a hierarchical `Context` with scoped inheritance:

- **Run level**: Data visible to all steps and vectors
- **Step level**: Data visible to all vectors in that step
- **Vector level**: Data visible only to that vector

```python
from litmus.execution.harness import TestHarness

harness = TestHarness(step_name="my_test")

# Run-level context - persists across all steps
harness.run_context.configure("operator", "jane")

with harness.step():
    # Step-level context - visible to all vectors in this step
    harness.context.configure("fixture.id", "FIX-01")

    for vector in harness.vectors:
        with harness.run_vector(vector) as tv:
            # Vector-level context - inherits from step and run
            harness.context.observe("temp_probe.temp", 24.8)

            # All levels merged in tv.params
            # → {"operator": "jane", "fixture.id": "FIX-01", "temp": 25}
            harness.measure("voltage", dmm.measure())
```

### Custom Metadata with run_context

The `run_context` fixture lets you add custom columns to the Parquet output:

```python
def test_with_custom_metadata(run_context, psu, dmm):
    # These become columns in the Parquet file
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("operator_shift", "day")
    run_context.set("fixture_serial", "FIX-001")
    run_context.set("ambient_temp", 23.5)
    run_context.set("calibration_due", "2026-06-15")

    # Normal test code...
    psu.set_voltage(5.0)
    return dmm.measure_dc_voltage()
```

**Result in Parquet:**
```
operator_badge | operator_shift | fixture_serial | ambient_temp | value
EMP-12345      | day            | FIX-001        | 23.5         | 5.01
```

Use meaningful prefixes for organization:
- `operator_*` — Operator-related fields
- `fixture_*` — Fixture-related fields
- `custom_*` — General custom fields

The `run_context` is session-scoped, so values set in one test persist across all tests in the session.

### Context API Methods

The `harness.context` property returns the current active context (vector > step > run):

```python
# Configuration (→ in_* columns)
harness.context.configure("psu.voltage", 5.0)
harness.context.set_in("psu.voltage", 5.0)  # Alias

# Observations (→ out_* columns)
harness.context.observe("temp_probe.temp", 24.8)
harness.context.set_out("temp_probe.temp", 24.8)  # Alias

# Bulk operations
harness.context.configure_all({"psu.voltage": 5.0, "eload.current": 0.8})
harness.context.observe_all({"temp_probe.temp": 24.8, "humidity": 45.2})

# Read values (checks parent chain)
voltage = harness.context.get_param("psu.voltage")
all_inputs = harness.context.params   # Merged with parent chain
all_outputs = harness.context.observations  # Merged with parent chain
```

## Comparison with pytest-native

| Feature | TestHarness | pytest-native |
|---------|-------------|---------------|
| Explicit control | ✓ | |
| Works with any test framework | ✓ | pytest only |
| Automatic vector expansion | | ✓ |
| Automatic result capture | | ✓ |
| YAML configuration | ✓ | ✓ |
| Spec-driven limits | ✓ | ✓ |
| Incremental adoption | Easy | Drop in fixtures per-test |

## When to Use TestHarness

- Adding Litmus to existing test code
- Non-pytest frameworks (Robot, unittest, etc.)
- Need explicit control over test flow
- Gradual migration to Litmus

## When to Use pytest-native

- New tests written for Litmus
- Want automatic vector expansion
- Want minimal boilerplate
- Full pytest integration

## Next Steps

- [Results API](results-api.md) — Store results from any source
- [Instrument Drivers](instruments.md) — Use Litmus drivers
- [pytest-native Reference](../reference/pytest-native.md) — Fixtures, markers, sidecar YAML
