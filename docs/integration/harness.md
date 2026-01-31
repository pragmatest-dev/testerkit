# Test Harness Integration

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
from litmus.products import SpecContext
spec = SpecContext.from_file("specs/my_product.yaml")
harness = TestHarness(
    step_name="my_test",
    spec_context=spec,
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

# With spec-derived limits (if spec_context provided)
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

Load limits from YAML:

```yaml
# tests/config.yaml
test_voltage:
  limits:
    voltage:
      low: 3.0
      high: 3.6
      units: V
```

```python
from litmus.execution.harness import TestHarness

harness = TestHarness(
    step_name="test_voltage",
    config_file="tests/config.yaml",
)

v = measure_voltage()
harness.measure("voltage", v)  # Limits loaded from config
harness.finish()
```

### With Spec-Driven Limits

```python
from litmus.execution.harness import TestHarness
from litmus.products import SpecContext

spec = SpecContext.from_file("specs/power_board.yaml", guardband_pct=10)

harness = TestHarness(
    step_name="test_output",
    spec_context=spec,
)

v = measure_voltage()
harness.measure("output_voltage", v)  # Limits from spec with guardband
harness.finish()
```

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

### Custom Metadata

```python
harness = TestHarness("my_test")

harness.add_metadata(
    operator="Jane Doe",
    firmware_version="1.2.3",
    calibration_date="2026-01-15",
)

harness.measure("voltage", 3.31, low=3.0, high=3.6)
harness.finish()
```

## Comparison with @litmus_test

| Feature | TestHarness | @litmus_test |
|---------|-------------|--------------|
| Explicit control | ✓ | |
| Works with any test framework | ✓ | pytest only |
| Automatic vector expansion | | ✓ |
| Automatic result capture | | ✓ |
| YAML configuration | ✓ | ✓ |
| Spec-driven limits | ✓ | ✓ |
| Incremental adoption | Easy | Requires decorator |

## When to Use TestHarness

- Adding Litmus to existing test code
- Non-pytest frameworks (Robot, unittest, etc.)
- Need explicit control over test flow
- Gradual migration to Litmus

## When to Use @litmus_test

- New tests written for Litmus
- Want automatic vector expansion
- Want minimal boilerplate
- Full pytest integration

## Next Steps

- [Results API](results-api.md) — Store results from any source
- [Instrument Drivers](instruments.md) — Use Litmus drivers
- [pytest Plugin](../reference/pytest-plugin.md) — Full pytest integration
