# Step 2: Add Measurement

**Goal:** Measure a voltage using a simulated instrument.

## What You'll Build

A test that measures voltage from a mock DMM and checks that it's positive.

## The Code

```python
# tests/test_voltage.py
from litmus.instruments import MockDMM

def test_measure_voltage():
    """Measure voltage from a simulated DMM."""
    # Create a mock DMM that returns 3.31V
    dmm = MockDMM(voltage=3.31)
    dmm.connect()

    # Read the voltage
    voltage = dmm.measure_voltage()

    # Check it's reasonable
    assert float(voltage) > 0
    assert float(voltage) < 10

    dmm.disconnect()
```

Run it:

```bash
pytest tests/test_voltage.py -v
```

Expected output:
```
tests/test_voltage.py::test_measure_voltage PASSED
```

## What's Happening

1. **MockDMM** is a simulated digital multimeter
2. `voltage=3.31` configures what value it returns
3. `measure_voltage()` returns a `Decimal` value
4. We assert the value is within a reasonable range

## Why MockDMM?

Real hardware testing requires real instruments. But during development:

- Instruments may not be available
- CI/CD runs on servers without hardware
- Iteration should be fast

MockDMM provides the same interface as real DMM drivers, letting you write tests that work with both.

## Using Context Managers

The connection/disconnection pattern is common. Use a context manager:

```python
# tests/test_voltage.py
from litmus.instruments import MockDMM

def test_measure_voltage():
    """Measure voltage using context manager."""
    with MockDMM(voltage=3.31) as dmm:
        voltage = dmm.measure_voltage()
        assert float(voltage) > 0
```

This automatically handles connect/disconnect and cleanup.

## The @litmus_test Decorator

For tests that should be logged to Litmus results, use the decorator:

```python
# tests/test_voltage.py
from litmus.execution import litmus_test
from litmus.instruments import MockDMM

@litmus_test
def test_measure_voltage(vector):
    """Measure voltage - result logged to Litmus."""
    with MockDMM(voltage=3.31) as dmm:
        return dmm.measure_voltage()
```

### What @litmus_test Does

1. **Provides `vector`** — Test parameters (we'll use this later)
2. **Captures return value** — The measurement is logged
3. **Checks limits** — If configured (next step)
4. **Records results** — To Parquet storage

### The `vector` Parameter

Every `@litmus_test` function receives a `vector` parameter. For now, it's empty. In later steps, we'll use it for parametrized testing.

## Changing the Simulated Value

MockDMM can return different values:

```python
def test_multiple_readings():
    dmm = MockDMM(voltage=5.0)
    dmm.connect()

    v1 = dmm.measure_voltage()
    assert float(v1) == 5.0

    # Change the simulated value
    dmm.set_value("voltage", 3.3)

    v2 = dmm.measure_voltage()
    assert float(v2) == 3.3

    dmm.disconnect()
```

## Other Mock Instruments

Litmus provides mocks for common instrument types:

```python
from litmus.instruments import MockDMM, MockPSU, MockELoad

# Digital multimeter
dmm = MockDMM(voltage=3.3, current=0.1, resistance=1000)

# Power supply
psu = MockPSU()
psu.set_voltage(5.0)
psu.enable_output()

# Electronic load
eload = MockELoad()
eload.set_current(0.5)
```

## Why Decimal?

Measurements return `Decimal` instead of `float`:

```python
from decimal import Decimal

voltage = dmm.measure_voltage()
print(type(voltage))  # <class 'decimal.Decimal'>
print(voltage)        # 3.31
```

`Decimal` avoids floating-point precision issues that can cause false test failures:

```python
# Float precision problem
0.1 + 0.2 == 0.3  # False!

# Decimal is exact
Decimal("0.1") + Decimal("0.2") == Decimal("0.3")  # True
```

## What You Learned

- How to use MockDMM for simulated measurements
- Context managers for clean resource handling
- The @litmus_test decorator for logged tests
- Why measurements use Decimal

## Next Step

Now let's add pass/fail criteria with limits.

[Step 3: Add Limits →](03-limits.md)
