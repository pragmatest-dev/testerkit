# Step 3: The @litmus_test Decorator

**Goal:** Use the @litmus_test decorator to log measurements automatically.

## What You'll Build

A test that automatically logs measurements to Litmus results storage.

## The Basic Pattern

```python
# tests/test_voltage.py
from litmus.execution import litmus_test
from litmus.instruments import MockDMM

@litmus_test
def test_output_voltage(vector, dmm):
    """Measure and return voltage - automatically logged."""
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
def test_output_voltage(vector, dmm):
    # vector contains test parameters (we'll use it later)
    print(f"Running with: {vector.params()}")
    return dmm.measure_voltage()
```

For now, `vector` is empty. In Step 5, we'll configure it with test conditions.

## Setting Up Fixtures

You need pytest fixtures for your instruments. Create a `conftest.py`:

```python
# tests/conftest.py
import pytest
from litmus.instruments import MockDMM, MockPSU

@pytest.fixture
def dmm():
    """Simulated DMM for testing."""
    with MockDMM(voltage=3.31) as d:
        yield d

@pytest.fixture
def psu():
    """Simulated PSU for testing."""
    with MockPSU() as p:
        yield p
```

Now your test can request `dmm` as a parameter:

```python
@litmus_test
def test_output_voltage(vector, dmm):
    return dmm.measure_voltage()
```

## Return Value Patterns

### Single Value

Return a single measurement:

```python
@litmus_test
def test_voltage(vector, dmm):
    return dmm.measure_voltage()  # Logged as "test_voltage"
```

The measurement name defaults to the function name.

### Multiple Measurements (Dict)

Return a dict for multiple measurements:

```python
@litmus_test
def test_power_analysis(vector, psu, dmm):
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
def test_stability(vector, dmm):
    import time
    for i in range(10):
        yield {"voltage": dmm.measure_voltage()}
        time.sleep(1)
```

Each yield adds a measurement. Useful for time-series data.

## Running the Test

```bash
pytest tests/test_voltage.py -v --dut-serial=TEST001
```

The `--dut-serial` flag identifies the device under test.

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
def test_voltage(vector, dmm):
    return dmm.measure_voltage()

# With parentheses - can customize behavior
@litmus_test()
def test_voltage(vector, dmm):
    return dmm.measure_voltage()
```

We'll use the parentheses form in later steps when we add configuration.

## Complete Example

**tests/conftest.py:**
```python
import pytest
from litmus.instruments import MockDMM, MockPSU

@pytest.fixture
def dmm():
    with MockDMM(voltage=3.31) as d:
        yield d

@pytest.fixture
def psu():
    with MockPSU() as p:
        yield p
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(vector, psu):
    """Measure input voltage."""
    psu.set_voltage(5.0)
    psu.enable_output()
    return psu.measure_voltage()

@litmus_test
def test_output_voltage(vector, dmm):
    """Measure output voltage."""
    return dmm.measure_voltage()
```

**Run:**
```bash
pytest tests/test_power.py -v --dut-serial=TEST001
```

## What You Learned

- The @litmus_test decorator for automatic measurement logging
- The `vector` parameter (used for conditions in later steps)
- Return value patterns: single, dict, yield
- Setting up instrument fixtures in conftest.py

## Next Step

Right now, measurements are just logged with no pass/fail criteria. Let's add limits.

[Step 4: Add Limits →](04-limits.md)
