# Step 3: The @litmus_test Decorator

**Goal:** Use the @litmus_test decorator to log measurements automatically.

## What You'll Build

A test that automatically logs measurements to Litmus results storage.

## The Basic Pattern

```python
# tests/test_voltage.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Measure and return voltage - automatically logged."""
    dmm = instruments["dmm"]
    return dmm.measure_voltage()
```

The decorator does several things:

1. **Captures the return value** as a measurement
2. **Logs it** to Litmus results storage
3. **Provides the `vector` parameter** for test conditions

## The vector Parameter

Every `@litmus_test` function receives a `vector` parameter as its first argument:

```python
@litmus_test
def test_output_voltage(vector, instruments):
    # vector contains test parameters (we'll use it later)
    print(f"Running with: {vector.params()}")
    return instruments["dmm"].measure_voltage()
```

For now, `vector` is empty. In Step 5, we'll configure it with test conditions.

## The instruments Fixture

When you run with `--station-config`, Litmus provides an `instruments` fixture:

```python
@litmus_test
def test_output_voltage(vector, instruments):
    dmm = instruments["dmm"]   # From station config
    psu = instruments["psu"]

    psu.set_voltage(5.0)
    psu.enable_output()
    return dmm.measure_voltage()
```

## Return Value Patterns

### Single Value

Return a single measurement:

```python
@litmus_test
def test_voltage(vector, instruments):
    return instruments["dmm"].measure_voltage()  # Logged as "test_voltage"
```

The measurement name defaults to the function name.

### Multiple Measurements (Dict)

Return a dict for multiple measurements:

```python
@litmus_test
def test_power_analysis(vector, instruments):
    psu = instruments["psu"]
    dmm = instruments["dmm"]
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
def test_stability(vector, instruments):
    import time
    dmm = instruments["dmm"]
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
def test_voltage(vector, instruments):
    return instruments["dmm"].measure_voltage()

# With parentheses - can customize behavior
@litmus_test()
def test_voltage(vector, instruments):
    return instruments["dmm"].measure_voltage()
```

We'll use the parentheses form in later steps when we add configuration.

## Complete Example

**stations/my_station.yaml:**
```yaml
station:
  id: my_station
  name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(vector, instruments):
    """Measure input voltage."""
    psu = instruments["psu"]
    psu.set_voltage(5.0)
    psu.enable_output()
    return psu.measure_voltage()

@litmus_test
def test_output_voltage(vector, instruments):
    """Measure output voltage."""
    return instruments["dmm"].measure_voltage()
```

**Run:**
```bash
pytest tests/test_power.py --station-config=stations/my_station.yaml --mock-instruments -v
```

## What You Learned

- The @litmus_test decorator for automatic measurement logging
- The `vector` parameter (used for conditions in later steps)
- The `instruments` fixture from station config
- Return value patterns: single, dict, yield

## Next Step

Right now, measurements are just logged with no pass/fail criteria. Let's add limits.

[Step 4: Add Limits →](04-limits.md)
