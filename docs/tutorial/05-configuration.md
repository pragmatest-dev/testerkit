# Step 5: Test Configuration

**Goal:** Configure limits, vectors, and mocks for your tests.

## Where Test Config Lives

Test configuration (vectors, limits, mocks, retry) can come from two places:

1. **Sequence steps** (primary) — When running with `--sequence`, step config is the source of truth
2. **Inline decorator** (fallback) — For ad-hoc `pytest` runs without a sequence

### Config Resolution

When both exist, sequence step config **replaces** (not merges) inline decorator config:

```
sequence step > inline decorator
```

## Inline Decorator Config

For development and ad-hoc runs, pass config directly to `@litmus_test`:

```python
from litmus.execution import litmus_test

@litmus_test(
    config={"vectors": {"expand": "product", "vin": [4.5, 5.0, 5.5]}},
    limits={"output_voltage": {"low": 3.135, "high": 3.465, "nominal": 3.3, "units": "V"}},
)
def test_output_voltage(context, psu, dmm):
    psu.set_voltage(context.get_in("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

Run directly with pytest:

```bash
pytest tests/test_power.py::test_output_voltage -v --dut-serial=TEST001
```

## Sequence Step Config

For orchestrated runs (production, validation), config lives in the sequence:

```yaml
# sequences/power_board_smoke.yaml
id: power_board_smoke
name: "Power Board - Smoke Test"
test_phase: dev

steps:
    - id: output_voltage
      test: tests/test_power.py::test_output_voltage
      vectors:
        expand: product
        vin: [4.5, 5.0, 5.5]
        load_percent: [0, 50, 100]
      limits:
        output_voltage:
          low: 3.135
          high: 3.465
          nominal: 3.3
          units: V
          spec_ref: "Section 7.2"
      mocks:
        dmm.measure_dc_voltage: 3.31
      retry:
        max_attempts: 2
        delay_seconds: 0.5
```

Run with sequence:

```bash
pytest tests/ --sequence=power_board_smoke --station=bench_1 -v
```

The test code is the same either way — only the config source changes.

## Vector Expansion

Vectors define the test conditions. They work identically whether in a sequence step or inline decorator.

```yaml
vectors:
  expand: product
  input_voltage: [4.5, 5.0, 5.5]
  load_percent: [0, 50, 100]
```

This runs the test 9 times (3 voltages × 3 loads):

```python
@litmus_test
def test_voltage_sweep(context, dmm):
    vin = context.inputs["input_voltage"]
    load = context.inputs["load_percent"]
    print(f"Testing at {vin}V, {load}% load")
    return dmm.measure_voltage()
```

## Accessing Vector Parameters via Context

```python
@litmus_test
def test_sweep(context, psu, dmm):
    # Get required parameter
    vin = context.inputs["input_voltage"]

    # Get optional parameter with default
    load = context.get_in("load_percent", 0)

    # Get all parameters
    print(context.inputs)  # {"input_voltage": 5.0, "load_percent": 50}

    psu.set_voltage(vin)
    return dmm.measure_voltage()
```

The context provides:
- `context.inputs["key"]` - Required parameter (raises KeyError if missing)
- `context.get_in("key", default)` - Optional parameter with default
- `context.inputs` - All parameters as a dict

## Expansion Modes

### product (Cartesian)

All combinations:

```yaml
vectors:
  expand: product
  voltage: [3.3, 5.0, 12.0]
  current: [0.1, 0.5, 1.0]
# Creates 9 vectors: (3.3, 0.1), (3.3, 0.5), ..., (12.0, 1.0)
```

### zip (Parallel)

Pair elements by position:

```yaml
vectors:
  expand: zip
  voltage: [3.3, 5.0, 12.0]
  current: [0.1, 0.5, 1.0]
# Creates 3 vectors: (3.3, 0.1), (5.0, 0.5), (12.0, 1.0)
```

### range (Numeric)

Generate a numeric sequence:

```yaml
vectors:
  expand: range
  name: voltage
  start: 3.0
  stop: 5.0
  step: 0.5
# Creates: 3.0, 3.5, 4.0, 4.5, 5.0
```

### Explicit List

Define each vector explicitly:

```yaml
vectors:
  - input_voltage: 5.0
    load: 0.1
  - input_voltage: 5.0
    load: 0.8
  - input_voltage: 12.0
    load: 0.5
```

### nested (With Change Detection)

Nested loops with slow outer parameters:

```yaml
vectors:
  expand: nested
  loops:
    - name: temperature
      values: [25, 85]      # Outer (changes slowly)
    - name: load
      values: [0.1, 0.5]    # Inner (changes fast)
```

Use `context.changed()` to detect outer loop changes:

```python
@litmus_test
def test_temp_sweep(context, chamber, dmm):
    if context.changed("temperature"):
        # Only reconfigure when temperature changes
        chamber.set_temp(context.inputs["temperature"])
        time.sleep(60)  # Wait for stabilization

    return dmm.measure_voltage()
```

## Retry Configuration

Handle flaky measurements:

```yaml
# In sequence step or inline config
retry:
  max_attempts: 3
  delay_seconds: 0.5
```

If the test fails, it retries up to 3 times with 0.5s delay between attempts.

## Complete Example

**Sequence (production):**
```yaml
# sequences/power_board_smoke.yaml
id: power_board_smoke
test_phase: production

steps:
    - id: input_voltage
      test: tests/test_power.py::test_input_voltage
      limits:
        test_input_voltage:
          low: 4.5
          high: 5.5
          nominal: 5.0
          units: V

    - id: load_sweep
      test: tests/test_power.py::test_load_sweep
      vectors:
        expand: product
        load_percent: [0, 50, 100]
      limits:
        test_load_sweep:
          low: 3.135
          high: 3.465
          units: V
      retry:
        max_attempts: 2
```

**Test code (same for both modes):**
```python
from litmus.execution import litmus_test

@litmus_test(
    limits={"test_input_voltage": {"low": 4.5, "high": 5.5, "nominal": 5.0, "units": "V"}},
)
def test_input_voltage(context, psu):
    """Inline limits used for ad-hoc runs; sequence overrides in production."""
    psu.set_voltage(5.0)
    psu.enable_output()
    return psu.measure_voltage()

@litmus_test(
    config={"vectors": {"expand": "product", "load_percent": [0, 50, 100]}},
    limits={"test_load_sweep": {"low": 3.135, "high": 3.465, "units": "V"}},
)
def test_load_sweep(context, psu, dmm, eload):
    """Multiple vectors with limits."""
    psu.set_voltage(5.0)
    psu.enable_output()
    eload.set_current(context.inputs["load_percent"] / 100.0)
    eload.enable()
    voltage = dmm.measure_voltage()
    eload.disable()
    return voltage
```

**Run:**
```bash
# Ad-hoc (uses inline decorator config)
pytest tests/test_power.py -v --dut-serial=TEST001

# Production (sequence overrides decorator config)
pytest tests/ --sequence=power_board_smoke --station=bench_1 -v
```

## What You Learned

- Config lives in sequence steps (primary) or inline decorators (fallback)
- Sequence step config replaces decorator config entirely
- Vector expansion modes (product, zip, range, nested)
- Accessing vector parameters via context.inputs and context.get_in()
- Using context.changed() for nested loops
- Retry configuration

## Next Step

Where do these limit values come from? Let's link them to product specifications.

[Step 6: Product Specifications →](06-specifications.md)
