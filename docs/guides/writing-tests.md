# Writing Tests

This guide covers patterns and best practices for writing Litmus tests.

## Basic Test Structure

```python
from litmus.execution import litmus_test

@litmus_test
def test_voltage(context, dmm):
    """Measure and return voltage."""
    return dmm.measure_voltage()
```

## The @litmus_test Decorator

The decorator transforms your function into a hardware test:

1. **Resolves config** from sequence step (if active) or inline decorator
2. **Expands vectors** (runs test multiple times if configured)
3. **Captures return values** as measurements
4. **Checks limits** against configured limits
5. **Records results** to Parquet

### Decorator Options

```python
@litmus_test(
    config={"vectors": [{"vin": 5.0}]},  # Inline vectors
    limits={"test_voltage": {"low": 3.0, "high": 3.6}},  # Inline limits
    raise_on_fail=True,       # Raise if limit fails (default: True)
)
def test_example(context, dmm):
    return dmm.measure_dc_voltage()
```

When running with `--sequence`, the sequence step config overrides inline config.

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
    voltage = context.get_param("voltage")
    load = context.get_param("load")

    psu.set_voltage(voltage)
    return dmm.measure_voltage()
```

### Context Methods

```python
context.get_param("voltage")          # Get parameter (raises if missing)
context.get_param("temp", 25)         # Get with default
context.params                     # All input parameters as dict

# Change detection (for nested loops)
if context.changed("temperature"):
    set_chamber_temp(context.get_param("temperature"))
```

## Test Configuration

Config comes from **sequence steps** (primary) or **inline decorator** (fallback).

### Inline Decorator (Dev/Ad-Hoc)

```python
@litmus_test(
    config={"vectors": {"expand": "product", "voltage": [3.3, 5.0, 12.0], "load": [0, 50, 100]}},
    limits={"test_sweep": {"low": 3.0, "high": 3.6, "nominal": 3.3, "units": "V"}},
    retry=RetryConfig(max_attempts=3, delay_seconds=0.5),
)
def test_sweep(context, dmm):
    return dmm.measure_dc_voltage()
```

### Sequence Step (Production)

```yaml
# sequences/my_sequence.yaml
steps:
  - id: sweep
    test: tests/test_power.py::test_sweep
    vectors:
      expand: product
      voltage: [3.3, 5.0, 12.0]
      load: [0, 50, 100]
    limits:
      test_sweep:
        low: 3.0
        high: 3.6
        nominal: 3.3
        units: V
    retry:
      max_attempts: 3
      delay_seconds: 0.5
```

## Instrument Fixtures

### Auto-Registered Role Fixtures (Recommended)

Instrument roles from your station config are auto-registered as pytest fixtures. Use them directly — no conftest boilerplate:

```python
@litmus_test
def test_voltage(context, dmm, psu):
    """dmm and psu are auto-registered from station config."""
    psu.set_voltage(context.get_param("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

To override with custom setup/teardown, define a fixture with the same name in `conftest.py`:

```python
# conftest.py
@pytest.fixture(scope="session")
def psu(instruments):
    """Custom PSU with default voltage."""
    inst = instruments.get("psu")
    inst.set_voltage(5.0)
    return inst
```

### Instrument Accessor

For programmatic or dynamic access:

```python
@litmus_test
def test_voltage(context, instrument):
    dmm = instrument("dmm")        # Get by role name
    roles = instrument.roles()      # List all roles
    ...
```

### Station Instruments Dict

The underlying dict of all instances, keyed by role:

```python
@litmus_test
def test_voltage(context, instruments):
    dmm = instruments["dmm"]
    psu = instruments["psu"]
    ...
```

### Pin-Based Access

For production tests with DUT traceability:

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
def test_conditional(context, hvps, psu, dmm):
    if context.get_param("high_voltage", False):
        hvps.set_voltage(100)
    else:
        psu.set_voltage(5)

    return dmm.measure_voltage()
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
def test_everything(context, dmm, temp_logger):
    return {
        "voltage": dmm.measure_voltage(),
        "temperature": temp_logger.measure_temperature(),
        "communication": test_i2c(),
    }
```

### Do: Separate Tests

```python
# Good
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_voltage()

@litmus_test
def test_temperature(context, temp_logger):
    return temp_logger.measure_temperature()
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

```python
# Good — limits in decorator (dev) or sequence step (production)
@litmus_test(limits={"test_voltage": {"low": 3.0, "high": 3.6}})
def test_voltage(context, dmm):
    return dmm.measure_voltage()
```

## Next Steps

- [pytest Plugin Reference](../reference/pytest-plugin.md) — Full plugin docs
- [Configuration Reference](../reference/configuration.md) — All config options
- [Simulation Mode](simulation-mode.md) — Testing without hardware
