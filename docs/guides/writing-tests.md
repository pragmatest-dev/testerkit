# Writing Tests

This guide covers patterns and best practices for writing Litmus tests.

## Basic Test Structure

```python
from litmus.execution import litmus_test

@litmus_test
def test_voltage(context, instruments):
    """Measure and return voltage."""
    dmm = instruments["dmm"]
    return dmm.measure_voltage()
```

## The @litmus_test Decorator

The decorator transforms your function into a hardware test:

1. **Loads configuration** from `config.yaml`
2. **Expands vectors** (runs test multiple times if configured)
3. **Captures return values** as measurements
4. **Checks limits** against configured limits
5. **Records results** to Parquet

### Decorator Options

```python
@litmus_test(
    raise_on_fail=True,       # Raise if limit fails (default: True)
    config_file="custom.yaml", # Custom config file
)
def test_example(context):
    ...
```

## Return Values

### Single Measurement

```python
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_voltage()  # Stored as "test_voltage"
```

### Multiple Measurements

```python
@litmus_test
def test_power(context, dmm):
    return {
        "input_voltage": dmm.measure_voltage(),
        "input_current": dmm.measure_current(),
    }
```

### Streaming Measurements

```python
@litmus_test
def test_stability(context, dmm):
    for i in range(10):
        yield {"voltage": dmm.measure_voltage()}
        time.sleep(1)
```

## The context Fixture

Every `@litmus_test` function receives a `context` parameter:

```python
@litmus_test
def test_sweep(context, psu, dmm):
    # Access parameters
    voltage = context["voltage"]
    load = context["load"]

    psu.set_voltage(voltage)
    return dmm.measure_voltage()
```

### Context Methods

```python
context["voltage"]          # Get parameter
context.get("temp", 25)     # Get with default
context.params()            # All parameters as dict (method)
context["_index"]           # 0-based index in expansion

# Change detection (for nested loops)
if context.changed("temperature"):
    set_chamber_temp(context["temperature"])
```

## Test Configuration

### Limits

```yaml
# tests/config.yaml
test_voltage:
  limits:
    test_voltage:
      low: 3.0
      high: 3.6
      nominal: 3.3
      units: V
      spec_ref: "Section 7.2"
```

### Vectors

```yaml
test_sweep:
  vectors:
    expand: product
    voltage: [3.3, 5.0, 12.0]
    load: [0, 50, 100]
```

### Retry

```yaml
test_flaky:
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

## Instrument Fixtures

### Station Instruments

```python
@litmus_test
def test_voltage(context, instruments):
    dmm = instruments["dmm"]
    psu = instruments["psu"]
    ...
```

### Pin-Based Access

```python
@litmus_test
def test_output(context, pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    return pins["VOUT"].measure_voltage()
```

## Common Patterns

### Setup and Teardown

```python
@litmus_test
def test_with_setup(context, psu, dmm):
    # Setup
    psu.set_voltage(5.0)
    psu.enable_output()
    time.sleep(0.1)  # Settle

    try:
        # Test
        return dmm.measure_voltage()
    finally:
        # Teardown
        psu.disable_output()
```

### Conditional Logic

```python
@litmus_test
def test_conditional(context, instruments):
    if context.get("high_voltage", False):
        instruments["hvps"].set_voltage(100)
    else:
        instruments["psu"].set_voltage(5)

    return instruments["dmm"].measure_voltage()
```

### Multiple Conditions

```python
@litmus_test
def test_sweep(context, psu, dmm):
    results = {}

    for voltage in [3.3, 5.0, 12.0]:
        psu.set_voltage(voltage)
        time.sleep(0.1)
        results[f"output_at_{voltage}V"] = dmm.measure_voltage()

    return results
```

### Error Handling

```python
@litmus_test
def test_with_retry(context, dmm):
    for attempt in range(3):
        try:
            return dmm.measure_voltage()
        except InstrumentError:
            time.sleep(0.5)
    raise TestError("Measurement failed after 3 attempts")
```

## Characterization Mode

When no limits are configured, measurements pass (for data collection):

```python
@litmus_test(raise_on_fail=False)
def test_characterize(context, dmm):
    """Collect data without pass/fail."""
    return dmm.measure_voltage()
```

## CLI Options

```bash
pytest tests/ \
  --dut-serial=SN12345 \     # Required: DUT serial
  --station=bench_1 \         # Station ID
  --operator="Jane Doe" \     # Operator name
  --test-phase=production \   # Test phase
  --mock-instruments \        # Mock instruments mode
  -v
```

## Best Practices

1. **One measurement focus per test** — Don't combine unrelated tests
2. **Use descriptive names** — `test_output_voltage_at_max_load`
3. **Document test purpose** — Docstrings explain intent
4. **Handle cleanup** — Use try/finally or context managers
5. **Keep tests independent** — Don't rely on test order
6. **Use vectors for sweeps** — Don't hardcode in loops

## Anti-Patterns

### Don't: Combine Unrelated Tests

```python
# Bad
@litmus_test
def test_everything(context, instruments):
    return {
        "voltage": measure_voltage(),
        "temperature": measure_temp(),
        "communication": test_i2c(),
    }
```

### Do: Separate Tests

```python
# Good
@litmus_test
def test_voltage(context, instruments):
    return instruments["dmm"].measure_voltage()

@litmus_test
def test_temperature(context, instruments):
    return instruments["temp_logger"].measure_temperature()
```

### Don't: Hardcode Limits

```python
# Bad
@litmus_test
def test_voltage(context, dmm):
    v = dmm.measure_voltage()
    assert 3.0 < float(v) < 3.6  # Hardcoded!
    return v
```

### Do: Use Configuration

```yaml
# config.yaml
test_voltage:
  limits:
    test_voltage:
      low: 3.0
      high: 3.6
```

## Next Steps

- [pytest Plugin Reference](../reference/pytest-plugin.md) — Full plugin docs
- [Configuration Reference](../reference/configuration.md) — All config options
- [Simulation Mode](simulation-mode.md) — Testing without hardware
