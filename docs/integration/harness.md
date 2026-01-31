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
spec = SpecContext.from_file("products/my_product/spec.yaml")
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

spec = SpecContext.from_file("products/power_board/spec.yaml", guardband_pct=10)

harness = TestHarness(
    step_name="test_output",
    spec_context=spec,
)

v = measure_voltage()
harness.measure("output_voltage", v)  # Limits from spec with guardband
harness.finish()
```

## Decorators for Test Architects

### @measure Decorator

Create reusable measurement functions with embedded limits.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | Function name | Measurement name in results |
| `limit` | `Limit` | `None` | Limit object with low/high bounds |
| `units` | `str` | `None` | Measurement units (overrides `limit.units`) |
| `raise_on_fail` | `bool` | `True` | Raise AssertionError if limit check fails |

#### Basic Usage

```python
from litmus.execution.decorators import measure
from litmus.config.models import Limit

@measure(
    name="output_voltage",
    limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    raise_on_fail=False,  # Return Measurement object instead of raising
)
def measure_output_voltage(dmm):
    """Reusable measurement - can be called from multiple tests."""
    return dmm.measure_dc_voltage()

# Usage
def test_voltage(dmm, litmus_logger):
    result = measure_output_voltage(dmm)  # Returns Measurement object
    assert result.outcome == Outcome.PASS
```

#### Examples

**Minimal (uses function name, no limits):**
```python
@measure()
def measure_temperature(sensor):
    return sensor.read_temp()
```

**With limit that raises on failure:**
```python
@measure(
    name="supply_current",
    limit=Limit(low=0, high=1.5, units="A"),
    raise_on_fail=True,  # Default - raises AssertionError on FAIL
)
def measure_supply_current(psu):
    return psu.measure_current()
```

**Override units from limit:**
```python
@measure(
    limit=Limit(low=0, high=1500),  # Stored as mA
    units="mA",  # Override display units
)
def measure_current_ma(psu):
    return psu.measure_current() * 1000
```

### @litmus_step Decorator

Track non-measurement steps (setup, verification, dialogs).

#### Parameters

None. This decorator takes no parameters.

#### What It Does

1. Registers the function execution as a step in the test run
2. Tracks pass/fail based on whether the function raises an exception
3. Does NOT produce measurements (use `@measure` for that)

#### Basic Usage

```python
from litmus.execution.decorators import litmus_step

@litmus_step
def verify_dut_connection(psu):
    """Step tracked in test run without producing measurements."""
    psu.set_voltage(0.1)
    current = psu.measure_current()
    assert current < 0.001, "DUT appears shorted!"

@litmus_step
def configure_test_equipment(psu, eload):
    """Setup step - tracked but no measurement."""
    psu.set_voltage(5.0)
    psu.enable_output()
    eload.set_current(0.5)
    eload.enable()

# Usage
def test_with_steps(psu, dmm, eload, litmus_logger):
    verify_dut_connection(psu)      # Tracked as step
    configure_test_equipment(psu, eload)  # Tracked as step
    result = measure_output_voltage(dmm)  # Measurement logged
```

#### Use Cases

- **Setup steps:** Configure instruments before measurements
- **Verification steps:** Check DUT connection, continuity tests
- **Operator dialogs:** Confirm DUT placement, visual inspections
- **Cleanup steps:** Disable outputs, safe state transitions

#### Async Support

```python
@litmus_step
async def wait_for_temperature(chamber, target):
    """Async step - works with async functions."""
    while await chamber.read_temp() < target:
        await asyncio.sleep(1)
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
