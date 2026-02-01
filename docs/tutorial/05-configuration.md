# Step 5: Test Configuration

**Goal:** Move limits and test parameters into YAML files.

## What You'll Build

A test suite where limits and vectors come from configuration, not code.

## The config.yaml File

Create a config file in your tests directory:

```yaml
# tests/config.yaml
test_output_voltage:
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
```

Now simplify your test:

```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, dmm):
    """Limits come from config.yaml automatically."""
    return dmm.measure_voltage()
```

The decorator auto-discovers `config.yaml` in the same directory as the test file.

## How Config Discovery Works

```
tests/
├── config.yaml         # ← Discovered automatically
└── test_power.py       # ← @litmus_test looks for config.yaml here
```

The decorator:
1. Finds the test file's directory
2. Looks for `config.yaml`
3. Loads configuration for the function name (`test_output_voltage`)

## Config Structure

```yaml
# tests/config.yaml

# Section name = function name
test_output_voltage:
  # Limits for measurements
  limits:
    test_output_voltage:      # measurement name
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
      spec_ref: "Section 7.2"  # Optional reference
```

## Vector Expansion

The real power of config is parametrized testing. Define test vectors:

```yaml
# tests/config.yaml
test_voltage_sweep:
  vectors:
    expand: product
    input_voltage: [4.5, 5.0, 5.5]
    load_percent: [0, 50, 100]
  limits:
    test_voltage_sweep:
      low: 3.135
      high: 3.465
      units: V
```

This runs the test 9 times (3 voltages x 3 loads):

```python
@litmus_test
def test_voltage_sweep(context, dmm):
    """Run at multiple conditions."""
    vin = context.inputs["input_voltage"]
    load = context.inputs["load_percent"]
    print(f"Testing at {vin}V, {load}% load")
    return dmm.measure_voltage()
```

## Accessing Vector Parameters via Context

Test vectors are accessed through the `context` parameter:

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
test_output_voltage:
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

If the test fails, it retries up to 3 times with 0.5s delay between attempts.

## Complete Example

**tests/config.yaml:**
```yaml
test_input_voltage:
  limits:
    test_input_voltage:
      low: 4.5
      high: 5.5
      nominal: 5.0
      units: V

test_load_sweep:
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

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(context, psu):
    """Single vector, limits from config."""
    psu.set_voltage(5.0)
    psu.enable_output()
    return psu.measure_voltage()

@litmus_test
def test_load_sweep(context, psu, dmm, eload):
    """Multiple vectors from config."""
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
pytest tests/test_power.py -v --dut-serial=TEST001
```

## Benefits of YAML Configuration

1. **Separation of concerns** — Engineers change limits, not code
2. **Non-developer access** — Technicians can adjust parameters
3. **Version control** — Track limit changes over time
4. **Environment-specific** — Different configs for debug vs production

## What You Learned

- How @litmus_test auto-discovers config.yaml
- Configuring limits in YAML
- Vector expansion modes (product, zip, range, nested)
- Accessing vector parameters via context.inputs and context.get_in()
- Using context.changed() for nested loops
- Retry configuration

## Next Step

Where do these limit values come from? Let's link them to product specifications.

[Step 6: Product Specifications →](06-specifications.md)
