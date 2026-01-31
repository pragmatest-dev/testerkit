# Step 3: Add Limits

**Goal:** Add pass/fail criteria to measurements.

## What You'll Build

A test that measures voltage and passes/fails based on configurable limits.

## The Code

```python
# tests/test_with_limits.py
from decimal import Decimal
from litmus.instruments import MockDMM
from litmus.data import Measurement, Outcome

def test_output_voltage():
    """Verify output voltage is within limits."""
    dmm = MockDMM(voltage=3.31)
    dmm.connect()

    # Create a measurement with limits
    m = Measurement(
        name="output_voltage",
        value=dmm.measure_voltage(),
        units="V",
        low_limit=Decimal("3.135"),  # 3.3V - 5%
        high_limit=Decimal("3.465"), # 3.3V + 5%
    )

    # Check against limits
    m.check_limit()

    # Verify it passed
    assert m.outcome == Outcome.PASS

    dmm.disconnect()
```

Run it:

```bash
pytest tests/test_with_limits.py -v
```

## What's Happening

1. **Measurement** wraps a value with metadata (name, units, limits)
2. **check_limit()** compares the value to limits and sets the outcome
3. **Outcome.PASS** means the value was within limits

## The Measurement Model

```python
from litmus.data import Measurement, Outcome

m = Measurement(
    name="output_voltage",      # Measurement identifier
    value=Decimal("3.31"),      # The measured value
    units="V",                  # Unit of measure
    low_limit=Decimal("3.0"),   # Minimum acceptable value
    high_limit=Decimal("3.6"),  # Maximum acceptable value
    nominal=Decimal("3.3"),     # Expected value (optional)
    spec_ref="Section 7.2",     # Datasheet reference (optional)
)

m.check_limit()
print(m.outcome)  # Outcome.PASS, Outcome.FAIL, etc.
```

## Outcome Values

| Outcome | Meaning |
|---------|---------|
| `PASS` | Value within limits |
| `FAIL` | Value outside limits |
| `SKIP` | Test was skipped |
| `ERROR` | Test encountered an error |
| `ABORTED` | Test was aborted |

## What If It Fails?

Change the mock value to something out of range:

```python
def test_out_of_range():
    """Demonstrate a failing measurement."""
    dmm = MockDMM(voltage=2.5)  # Below limit!
    dmm.connect()

    m = Measurement(
        name="output_voltage",
        value=dmm.measure_voltage(),
        units="V",
        low_limit=Decimal("3.135"),
        high_limit=Decimal("3.465"),
    )

    m.check_limit()

    print(f"Value: {m.value}")
    print(f"Outcome: {m.outcome}")
    # Value: 2.5
    # Outcome: Outcome.FAIL

    dmm.disconnect()
```

## Comparators

By default, limits use `GELE` (greater-or-equal to low, less-or-equal to high):

```
low <= value <= high
```

Other comparators are available:

```python
from litmus.config.models import Comparator

m = Measurement(
    name="voltage",
    value=Decimal("5.0"),
    nominal=Decimal("5.0"),
    comparator=Comparator.EQ,  # Must equal nominal
)
```

| Comparator | Pass Condition |
|------------|----------------|
| `GELE` | low ≤ value ≤ high (default) |
| `EQ` | value = nominal |
| `NE` | value ≠ nominal |
| `LT` | value < high |
| `LE` | value ≤ high |
| `GT` | value > low |
| `GE` | value ≥ low |

## Using with @litmus_test

The decorator automatically creates Measurements from return values:

```python
from litmus.execution import litmus_test
from litmus.instruments import MockDMM

@litmus_test
def test_voltage(vector):
    """Return value becomes a measurement."""
    with MockDMM(voltage=3.31) as dmm:
        return dmm.measure_voltage()
```

But how does it know the limits? That's where configuration comes in...

## Hardcoded Limits Problem

Right now, limits are in code:

```python
low_limit=Decimal("3.135"),
high_limit=Decimal("3.465"),
```

Problems:
- Changing limits requires code changes
- Non-developers can't modify limits
- No link to product specifications

Solution: **YAML configuration** (next step).

## What You Learned

- The Measurement model for tracking results
- How check_limit() determines pass/fail
- Different comparator types
- Why we need external configuration

## Next Step

Move limits out of code and into YAML configuration.

[Step 4: YAML Configuration →](04-configuration.md)
