# Step 2: Mock Instruments

**Goal:** Measure a voltage using a simulated instrument.

## What You'll Build

A test that measures voltage from a mock DMM and verifies it's reasonable.

## The Code

```python
# tests/test_voltage.py
from litmus.instruments import MockDMM

def test_measure_voltage():
    """Measure voltage from a simulated DMM."""
    # Create a mock DMM
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

## Why Mock Instruments?

Real hardware testing requires real instruments. But during development:

- Instruments may not be available
- CI/CD runs on servers without hardware
- Iteration should be fast

Mock instruments provide the same interface as real drivers, letting you write tests that work with both.

## Context Managers

The connect/disconnect pattern is common. Use a context manager for cleaner code:

```python
# tests/test_voltage.py
from litmus.instruments import MockDMM

def test_measure_voltage():
    """Measure voltage using context manager."""
    with MockDMM(voltage=3.31) as dmm:
        voltage = dmm.measure_voltage()
        assert float(voltage) > 0
```

This automatically handles connect/disconnect and cleanup on errors.

## Available Mock Instruments

Litmus provides mocks for common instrument types:

```python
from litmus.instruments import MockDMM, MockPSU, MockELoad

# Digital multimeter - measures voltage, current, resistance
dmm = MockDMM(voltage=3.3, current=0.1, resistance=1000)

# Power supply - sources voltage/current
psu = MockPSU()
psu.set_voltage(5.0)
psu.set_current_limit(1.0)
psu.enable_output()

# Electronic load - sinks current
eload = MockELoad()
eload.set_current(0.5)
eload.enable()
```

## Changing Simulated Values

Mock instruments can change their return values:

```python
def test_multiple_readings():
    with MockDMM(voltage=5.0) as dmm:
        v1 = dmm.measure_voltage()
        assert float(v1) == 5.0

        # Change the simulated value
        dmm.set_value("voltage", 3.3)

        v2 = dmm.measure_voltage()
        assert float(v2) == 3.3
```

## A Complete Test Pattern

Here's a realistic test using multiple instruments:

```python
from litmus.instruments import MockDMM, MockPSU

def test_power_supply_output():
    """Verify PSU outputs correct voltage."""
    with MockPSU() as psu, MockDMM(voltage=5.02) as dmm:
        # Configure power supply
        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        # Measure output
        voltage = dmm.measure_voltage()

        # Verify
        assert 4.5 < float(voltage) < 5.5

        # Cleanup
        psu.disable_output()
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

- MockDMM, MockPSU, MockELoad for simulated instruments
- Context managers for clean resource handling
- Why measurements use Decimal

## Next Step

Now let's use the @litmus_test decorator to log measurements to results.

[Step 3: The @litmus_test Decorator →](03-decorator.md)
