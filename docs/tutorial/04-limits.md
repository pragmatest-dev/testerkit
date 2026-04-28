# Step 4: Add Limits

**Goal:** Add pass/fail criteria to measurements.

## What You'll Build

A test that measures voltage and passes/fails based on configurable limits.

## The Problem

In Step 3, we used `verify(...)` to compare against a product spec. For
one-off measurements without a spec, you need explicit limits:

```python
def test_voltage(dmm, logger):
    logger.measure("output_voltage", dmm.measure_voltage())  # Logged, but is it passing?
```

Without a limit, the measurement is recorded but unchecked. We need limits
to determine pass/fail.

## Understanding Limits

A `Limit` defines acceptable bounds for a measurement:

```python
from litmus.models.test_config import Limit

limit = Limit(
    low=3.135,      # Minimum acceptable value
    high=3.465,     # Maximum acceptable value
    nominal=3.3,    # Expected value (optional)
    units="V",      # Unit of measure
)
```

## The Measurement Model

Under the hood, the logger creates `Measurement` objects:

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

## Inline Limits with `logger.measure`

Pass an explicit `Limit` to the logger:

```python
from litmus.models.test_config import Limit


def test_output_voltage(dmm, logger):
    logger.measure(
        "output_voltage",
        dmm.measure_voltage(),
        limit=Limit(low=3.135, high=3.465, units="V"),
    )
```

If the measurement is outside limits, the logger records `outcome=FAIL` and
raises an `AssertionError`.

## Limits via Marker

For a whole-test limit injection that reads nicely at the top of the test,
use the `litmus_limits` marker. Values merge with sidecar `limits:`:

```python
import pytest


@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "units": "V"})
def test_output_voltage(dmm, logger):
    logger.measure("output_voltage", dmm.measure_voltage())
```

## Limits via Sidecar YAML

The cleanest option for non-trivial tests is a sidecar `test_<module>.yaml`
next to the test file:

```yaml
# test_voltage.yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, units: "V"}
```

The test is then just:

```python
def test_output_voltage(dmm, logger):
    logger.measure("output_voltage", dmm.measure_voltage())
```

`logger.measure` resolves the limit from the sidecar automatically.

## What If It Fails?

Configure a mock value outside the limit range. Using a sidecar:

```yaml
# test_voltage.yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, units: "V"}
mocks:
  dmm.measure_voltage: 2.5   # Below limit - will fail!
```

Run the test:

```bash
pytest tests/test_voltage.py --station-config=stations/my_station.yaml --mock-instruments -v
```

Output:
```
AssertionError: Measurement 'output_voltage' FAILED at vector 0:
2.5 not in [3.135, 3.465]
```

## Characterization Mode

During development, you may want to record values without failing. Drop the
limit and just call `logger.measure` without one — the value is recorded
with `outcome=unchecked`.

## Comparators

By default, limits use `GELE` (greater-or-equal to low, less-or-equal to high):

```
low <= value <= high
```

Other comparators are available:

```python
from litmus.models.enums import Comparator
from litmus.models.test_config import Limit

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

Inline `Limit(...)` in code has issues:

```python
def test_voltage(dmm, logger):
    logger.measure(
        "voltage",
        dmm.measure_voltage(),
        limit=Limit(low=3.135, high=3.465, units="V"),
    )
```

Problems:
- Changing limits requires code changes
- Non-developers can't modify limits
- No link to product specifications
- Different limits for different conditions (temperature, load) are awkward

Solution: **sidecar YAML** (next step).

## Dynamic Limits (Callable)

For limits that vary based on test conditions (temperature, load, etc.), use callable limits.

### Why Callable Limits?

Sometimes limits depend on test conditions:
- Tighter tolerance at room temperature, looser at extremes
- Different limits for different loads
- Limits that scale with input voltage

### Inline Python (Simple)

Define limits as Python expressions in a sidecar:

```yaml
# tests/test_voltage.yaml
sweeps:
  - {temperature: [-40, 25, 85]}
limits:
  output_voltage:
    callable: |
      temp = ctx.get_param("temperature")
      if temp < 0:
        return Limit(low=3.15, high=3.45, units="V")
      elif temp < 50:
        return Limit(low=3.25, high=3.35, units="V")
      else:
        return Limit(low=3.10, high=3.50, units="V")
```

The callable has access to:
- `ctx.get_param(key)` - Input parameters from test vectors
- `ctx.get_observation(key)` - Observations from context.observe()
- `Limit` class - For constructing return limits

### Module Function (Complex)

For more complex logic, use a Python function:

```python
# myproject/limits.py
from litmus.models.test_config import Limit

def output_voltage(ctx):
    """Temperature-dependent voltage limit."""
    temp = ctx.get_param("temperature")
    load = ctx.get_param("load_current")

    # Tighter limits at room temp, nominal load
    if temp >= 20 and temp <= 30 and load < 0.5:
        return Limit(low=3.25, high=3.35, units="V")
    else:
        return Limit(low=3.10, high=3.50, units="V")
```

Reference it in YAML:

```yaml
limits:
  output_voltage:
    callable: myproject.limits.output_voltage
```

See the [Limits Guide](../guides/limits.md) for full details on callable limits.

## Accessing Limits in Tests

Tests can retrieve resolved limits via context:

```python
def test_voltage_with_limit_logging(dmm, context, logger):
    # Get the resolved limit
    limit = context.get_limit("output_voltage")

    # Log limit info for traceability
    if limit:
        context.observe("limit_low", limit.low)
        context.observe("limit_high", limit.high)
        context.observe("spec_ref", limit.spec_ref)

    logger.measure("output_voltage", dmm.measure_voltage())
```

This is useful for:
- **Adaptive test behavior**: Take more samples if near limit
- **Enhanced logging**: Record limit context alongside measurements
- **Custom validation**: Implement domain-specific pass/fail logic

## Complete Example

**stations/my_station.yaml:**
```yaml
id: my_station

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
```

**tests/test_limits.yaml:**
```yaml
limits:
  output_voltage:
    low: 3.135
    high: 3.465
    nominal: 3.3
    units: V
mocks:
  - {target: dmm.measure_voltage, return_value: 3.31}
```

**tests/test_limits.py:**
```python
def test_output_voltage(dmm, logger):
    """Verify output voltage is within spec."""
    logger.measure("output_voltage", dmm.measure_voltage())
```

**Run:**
```bash
pytest tests/test_limits.py --station=my_station --mock-instruments -v
```

## What You Learned

- The `Limit` model for pass/fail criteria
- How measurements are checked against limits
- Different comparator types (GELE, LE, GE, EQ, etc.)
- Callable limits for condition-dependent pass/fail criteria
- Why we need external configuration

## Next Step

Move limits out of code and into YAML configuration.

[Step 5: Test Configuration →](05-configuration.md)
