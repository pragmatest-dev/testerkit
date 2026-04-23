# Step 5: Test Configuration

**Goal:** Configure limits, vectors, and mocks for your tests.

## Where Test Config Lives

Test configuration (vectors, limits, mocks) can come from several places,
resolved in priority order:

1. **Sequence steps** — when running with `--sequence`, step config wins
2. **Pytest markers** — `@pytest.mark.parametrize(...)`, `@pytest.mark.litmus_limits`
3. **Sidecar YAML** — a `test_<module>.yaml` next to the test file

Sequence step config **replaces** (not merges) any lower-priority source for
the keys it sets.

## Sidecar YAML

For ad-hoc pytest runs, the simplest option is a sidecar `test_<module>.yaml`
next to the test file:

```yaml
# test_power.yaml
vectors:
  vin: [4.5, 5.0, 5.5]
  load_current: [0.1, 0.4, 0.8]

limits:
  output_voltage: {low: 3.135, high: 3.465, nominal: 3.3, units: "V"}

mocks:
  dmm.measure_dc_voltage: 3.31
```

The test is then:

```python
# tests/test_power.py
def test_output_voltage(context, psu, dmm, spec):
    psu.set_voltage(context.get_param("vin"))
    psu.enable_output()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

Run directly with pytest:

```bash
pytest tests/test_power.py::test_output_voltage -v --dut-serial=TEST001
```

## Inline Markers

For quick tweaks, markers work inline:

```python
import pytest


@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "units": "V"})
def test_output_voltage(vin, context, psu, dmm, logger):
    psu.set_voltage(vin)
    psu.enable_output()
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

## Sequence Step Config

For orchestrated runs (production, validation), config lives in the sequence:

```yaml
# sequences/power_board_smoke.yaml
id: power_board_smoke
name: "Power Board - Smoke Test"
test_phase: development

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
```

Run with sequence:

```bash
pytest tests/ --sequence=power_board_smoke --station=bench_1 -v
```

The test code is the same either way — only the config source changes.

## Vector Expansion

Vectors define the test conditions. They work identically in any of the
sources above.

```yaml
vectors:
  expand: product
  input_voltage: [4.5, 5.0, 5.5]
  load_percent: [0, 50, 100]
```

This runs the test 9 times (3 voltages × 3 loads):

```python
def test_voltage_sweep(context, dmm, logger):
    vin = context.get_param("input_voltage")
    load = context.get_param("load_percent")
    print(f"Testing at {vin}V, {load}% load")
    logger.measure("output_voltage", dmm.measure_voltage())
```

## Accessing Vector Parameters via Context

```python
def test_sweep(context, psu, dmm, logger):
    # Get required parameter (raises if missing)
    vin = context.get_param("input_voltage")

    # Get optional parameter with default
    load = context.get_param("load_percent", 0)

    # Get all parameters
    print(context.params)  # {"input_voltage": 5.0, "load_percent": 50}

    psu.set_voltage(vin)
    logger.measure("output_voltage", dmm.measure_voltage())
```

The context provides:
- `context.get_param("key")` - Required parameter (raises if missing)
- `context.get_param("key", default)` - Optional parameter with default
- `context.params` - All parameters as a dict

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

### Range strings (Numeric sweeps)

Use a compact `"start:stop:step"` string anywhere a list is expected:

```yaml
vectors:
  expand: product
  voltage: "3.0:5.0:0.5"
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

### Product with Change Detection

Put slow-changing parameters first. Use `context.changed()` to detect outer loop changes:

```yaml
vectors:
  expand: product
  temperature: [25, 85]      # Outer (changes slowly)
  load: [0.1, 0.5]           # Inner (changes fast)
```

```python
def test_temp_sweep(context, chamber, dmm, logger):
    if context.changed("temperature"):
        # Only reconfigure when temperature changes
        chamber.set_temp(context.get_param("temperature"))
        time.sleep(60)  # Wait for stabilization

    logger.measure("output_voltage", dmm.measure_voltage())
```

## Retries

For flaky tests, use the pytest ecosystem:

```python
import pytest


@pytest.mark.flaky(reruns=3, reruns_delay=0.5)
def test_flaky(dmm, logger):
    logger.measure("voltage", dmm.measure_voltage())
```

This uses `pytest-rerunfailures` (already a Litmus dependency). Sequence
steps can also specify `retry:` — see the sequence reference.

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
        input_voltage:
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
        output_voltage:
          low: 3.135
          high: 3.465
          units: V
      retry:
        max_attempts: 2
```

**Test code (same for both modes):**
```python
def test_input_voltage(psu, logger):
    """Measures input voltage; limit comes from sidecar or sequence."""
    psu.set_voltage(5.0)
    psu.enable_output()
    logger.measure("input_voltage", psu.measure_voltage())


def test_load_sweep(context, psu, dmm, eload, logger):
    """Multiple vectors with limits."""
    psu.set_voltage(5.0)
    psu.enable_output()
    eload.set_current(context.get_param("load_percent") / 100.0)
    eload.enable()
    voltage = dmm.measure_voltage()
    eload.disable()
    logger.measure("output_voltage", voltage)
```

**Run:**
```bash
# Ad-hoc (uses sidecar test_power.yaml)
pytest tests/test_power.py -v --dut-serial=TEST001

# Production (sequence takes precedence)
pytest tests/ --sequence=power_board_smoke --station=bench_1 -v
```

## What You Learned

- Config lives in sequence steps (primary), markers, sidecar YAML, or parametrize
- Sequence step config replaces lower-priority sources for its keys
- Vector expansion modes (product, zip, range strings, recursive sub-blocks)
- Accessing vector parameters via `context.get_param()` and `context.params`
- Using `context.changed()` for product sweeps
- Retries via `@pytest.mark.flaky` or sequence step `retry:`

## Next Step

Where do these limit values come from? Let's link them to product specifications.

[Step 6: Product Specifications →](06-specifications.md)
