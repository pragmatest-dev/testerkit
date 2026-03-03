# Step 3: The @litmus_test Decorator

**Goal:** Use the @litmus_test decorator to log measurements automatically.

## What You'll Build

A test that automatically logs measurements to Litmus results storage.

## The Basic Pattern

```python
# tests/test_voltage.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, dmm):
    """Measure and return voltage - automatically logged."""
    return dmm.measure_voltage()
```

The decorator does several things:

1. **Captures the return value** as a measurement
2. **Logs it** to Litmus results storage
3. **Provides the `context` parameter** for test data and conditions

## The context Parameter

Every `@litmus_test` function receives a `context` parameter as its first argument:

```python
@litmus_test
def test_output_voltage(context, dmm):
    # context contains test parameters and provides observation logging
    # We'll use it extensively in later steps
    return dmm.measure_voltage()
```

The context provides:
- **Inputs**: Test vector parameters (configured in Step 5)
- **Observations**: Log environmental data
- **Configuration**: Record commanded values

For now, you can ignore it. In Step 5, we'll use it to access test conditions.

## Instrument Role Fixtures

When you run with `--station-config`, Litmus auto-registers each instrument role as a pytest fixture. Use them directly as function parameters:

```python
@litmus_test
def test_output_voltage(context, dmm, psu):
    psu.set_voltage(5.0)
    psu.enable_output()
    return dmm.measure_voltage()
```

## Return Value Patterns

### Single Value

Return a single measurement:

```python
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_voltage()  # Logged as "test_voltage"
```

The measurement name defaults to the function name.

### Multiple Measurements (Dict)

Return a dict for multiple measurements:

```python
@litmus_test
def test_power_analysis(context, psu, dmm):
    return {
        "input_voltage": psu.measure_voltage(),
        "input_current": psu.measure_current(),
        "output_voltage": dmm.measure_voltage(),
    }
```

Each key becomes a separate measurement.

### Streaming (Yield)

Yield measurements over time:

```python
@litmus_test
def test_stability(context, dmm):
    import time
    for i in range(10):
        yield {"voltage": dmm.measure_voltage()}
        time.sleep(1)
```

Each yield adds a measurement. Useful for time-series data.

## Running the Test

```bash
# With mock instruments (no hardware)
pytest tests/test_voltage.py --station-config=stations/my_station.yaml --mock-instruments -v

# With real hardware
pytest tests/test_voltage.py --station-config=stations/my_station.yaml --dut-serial=SN001 -v
```

## What Gets Stored

Each measurement includes:

| Field | Description |
|-------|-------------|
| `name` | Measurement name (function name or dict key) |
| `value` | The measured value |
| `units` | Unit of measure (from limits, when configured) |
| `outcome` | PASS, FAIL, or unchecked |
| `timestamp` | When it was recorded |
| `vector_index` | Which test vector (for parametrized tests) |

## The Decorator Without Parentheses

Both forms work:

```python
# Without parentheses - uses all defaults
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_voltage()

# With parentheses - can customize behavior
@litmus_test()
def test_voltage(context, dmm):
    return dmm.measure_voltage()
```

We'll use the parentheses form in later steps when we add configuration.

## Complete Example

**stations/my_station.yaml:**
```yaml
id: my_station
name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(context, psu):
    """Measure input voltage."""
    psu.set_voltage(5.0)
    psu.enable_output()
    return psu.measure_voltage()

@litmus_test
def test_output_voltage(context, dmm):
    """Measure output voltage."""
    return dmm.measure_voltage()
```

**Run:**
```bash
pytest tests/test_power.py --station-config=stations/my_station.yaml --mock-instruments -v
```

## What You Learned

- The @litmus_test decorator for automatic measurement logging
- The `context` parameter (used extensively in later steps)
- Instrument role fixtures from station config (e.g. `dmm`, `psu`)
- Return value patterns: single, dict, yield

## Next Step

Right now, measurements are just logged with no pass/fail criteria. Let's add limits.

[Step 4: Add Limits →](04-limits.md)
