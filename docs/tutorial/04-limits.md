# Step 4: Add Limits

**Goal:** Add pass/fail criteria to measurements.

## What You'll Build

A test that measures voltage and passes/fails based on configurable limits.

## The Problem

In Step 3, we logged measurements but didn't check if they were good or bad:

```python
@litmus_test
def test_voltage(context, instruments):
    return instruments["dmm"].measure_voltage()  # Logged, but is it passing?
```

We need limits to determine pass/fail.

## Understanding Limits

A `Limit` defines acceptable bounds for a measurement:

```python
from litmus.config.models import Limit

limit = Limit(
    low=3.135,      # Minimum acceptable value
    high=3.465,     # Maximum acceptable value
    nominal=3.3,    # Expected value (optional)
    units="V",      # Unit of measure
)
```

## The Measurement Model

Under the hood, @litmus_test creates `Measurement` objects:

```python
from litmus.data import Measurement, Outcome

m = Measurement(
    name="output_voltage",
    value=3.31,
    units="V",
    low_limit=3.135,
    high_limit=3.465,
)

# Check against limits
m.check_limit()

print(m.outcome)  # Outcome.PASS
```

## Outcome Values

| Outcome | Meaning |
|---------|---------|
| `PASS` | Value within limits |
| `FAIL` | Value outside limits |
| `SKIP` | Test was skipped |
| `ERROR` | Test encountered an error |
| `ABORTED` | Test was aborted |

## Inline Limits with @litmus_test

You can specify limits directly in the decorator:

```python
from litmus.execution import litmus_test
from litmus.config.models import Limit

@litmus_test(
    limits={
        "test_output_voltage": Limit(low=3.135, high=3.465, units="V"),
    }
)
def test_output_voltage(context, instruments):
    return instruments["dmm"].measure_voltage()
```

If the measurement is outside limits, the test fails with an `AssertionError`.

## What If It Fails?

Configure a mock value outside the limit range in your test config:

```yaml
# tests/config.yaml
test_output_voltage:
  _mock:
    dmm.measure_voltage: 2.5  # Below limit - will fail!
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      units: V
```

Run the test:

```bash
pytest tests/test_voltage.py --station-config=stations/my_station.yaml --mock-instruments -v
```

Output:
```
AssertionError: Measurement 'test_output_voltage' FAILED at vector 0:
2.5 not in [3.135, 3.465]
```

## Characterization Mode

During development, you may want to record values without failing:

```python
@litmus_test(raise_on_fail=False)
def test_characterize(vector, instruments):
    # Measurements recorded but won't fail test
    return instruments["dmm"].measure_voltage()
```

## Comparators

By default, limits use `GELE` (greater-or-equal to low, less-or-equal to high):

```
low <= value <= high
```

Other comparators are available:

```python
from litmus.config.models import Comparator, Limit

# Upper limit only
limit = Limit(high=1.0, comparator=Comparator.LE)  # value <= 1.0

# Lower limit only
limit = Limit(low=0.0, comparator=Comparator.GE)   # value >= 0.0

# Must equal nominal
limit = Limit(nominal=5.0, comparator=Comparator.EQ)  # value == 5.0
```

| Comparator | Pass Condition |
|------------|----------------|
| `GELE` | low <= value <= high (default) |
| `EQ` | value == nominal |
| `NE` | value != nominal |
| `LT` | value < high |
| `LE` | value <= high |
| `GT` | value > low |
| `GE` | value >= low |

## The Problem with Hardcoded Limits

Limits in code have issues:

```python
@litmus_test(
    limits={"test_voltage": Limit(low=3.135, high=3.465, units="V")}
)
def test_voltage(context, instruments):
    ...
```

Problems:
- Changing limits requires code changes
- Non-developers can't modify limits
- No link to product specifications
- Different limits for different conditions (temperature, load) are awkward

Solution: **YAML configuration** (next step).

## Dynamic Limits (Callable)

For limits that vary based on test conditions (temperature, load, etc.), use callable limits.

### Why Callable Limits?

Sometimes limits depend on test conditions:
- Tighter tolerance at room temperature, looser at extremes
- Different limits for different loads
- Limits that scale with input voltage

### Inline Python (Simple)

Define limits as Python expressions in YAML:

```yaml
# tests/config.yaml
test_output_voltage_temp:
  vectors:
    expand: product
    temperature: [-40, 25, 85]
  limits:
    test_output_voltage_temp:
      callable: |
        temp = ctx.get_in("temperature")
        if temp < 0:
          return Limit(low=3.15, high=3.45, units="V")
        elif temp < 50:
          return Limit(low=3.25, high=3.35, units="V")
        else:
          return Limit(low=3.10, high=3.50, units="V")
```

The callable has access to:
- `ctx.get_in(key)` - Input parameters from test vectors
- `ctx.get_out(key)` - Observations from context.observe()
- `Limit` class - For constructing return limits

### Module Function (Complex)

For more complex logic, use a Python function:

```python
# myproject/limits.py
from litmus.config.models import Limit

def output_voltage(ctx):
    """Temperature-dependent voltage limit."""
    temp = ctx.get_in("temperature")
    load = ctx.get_in("load_current")

    # Tighter limits at room temp, nominal load
    if temp >= 20 and temp <= 30 and load < 0.5:
        return Limit(low=3.25, high=3.35, units="V")
    else:
        return Limit(low=3.10, high=3.50, units="V")
```

Reference it in YAML:

```yaml
test_output_voltage:
  limits:
    output_voltage:
      callable: myproject.limits.output_voltage
```

See the [Limits Guide](../guides/limits.md) for full details on callable limits.

## Complete Example

**stations/my_station.yaml:**
```yaml
station:
  id: my_station

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
```

**tests/config.yaml:**
```yaml
test_output_voltage:
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
```

**tests/test_limits.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Verify output voltage is within spec."""
    return instruments["dmm"].measure_voltage()
```

**Run:**
```bash
pytest tests/test_limits.py --station-config=stations/my_station.yaml --mock-instruments -v
```

## What You Learned

- The Limit model for pass/fail criteria
- How measurements are checked against limits
- Different comparator types (GELE, LE, GE, EQ, etc.)
- Callable limits for condition-dependent pass/fail criteria
- Why we need external configuration

## Next Step

Move limits out of code and into YAML configuration.

[Step 5: Test Configuration →](05-configuration.md)
