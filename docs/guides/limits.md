# Test Limits

Limits define pass/fail criteria for measurements. Litmus checks return values against configured limits and records the outcome.

## Where Limits Are Specified

Limits can come from two sources (in order of precedence):

1. **Sequence steps** — Primary source for orchestrated runs
2. **Inline decorator** — Fallback for ad-hoc pytest runs

### In a Sequence Step

```yaml
# sequences/power_board_smoke.yaml
steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    limits:
      test_output_voltage:              # Measurement name
        low: 3.135
        high: 3.465
        nominal: 3.3
        units: V
        spec_ref: "output_voltage @ 5%"
```

### In the Inline Decorator

```python
from litmus.execution import litmus_test

@litmus_test(
    limits={"test_output_voltage": {"low": 3.135, "high": 3.465, "nominal": 3.3, "units": "V"}},
)
def test_output_voltage(context, psu, dmm):
    psu.set_voltage(context.get_in("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

## How Limits Are Automatically Used

When you use `@litmus_test`, the framework:

1. **Resolves config** — From sequence step (if active) or inline decorator
2. **Expands vectors** — Creates test iterations from vectors config
3. **Runs your test** — Executes your function for each vector
4. **Captures return value** — Your return value becomes a measurement
5. **Applies limits** — Checks the measurement against configured limits
6. **Records result** — Saves measurement with outcome to Parquet

## Limit Structure

```yaml
test_name:
  limits:
    measurement_name:
      low: 3.135          # Minimum acceptable value
      high: 3.465         # Maximum acceptable value
      nominal: 3.3        # Expected/target value (optional)
      units: V            # Unit of measure (for reporting)
      comparator: GELE    # How to compare (default: GELE)
      spec_ref: "..."     # Reference to specification (optional)
```

### Required vs Optional Fields

| Field | Required | Description |
|-------|----------|-------------|
| `low` | ✓* | Lower limit (* or `high` required) |
| `high` | ✓* | Upper limit (* or `low` required) |
| `nominal` | | Expected value (for EQ/NE comparators) |
| `units` | | Unit of measure |
| `comparator` | | Comparison type (default: GELE) |
| `spec_ref` | | Traceability reference |

## Multiple Measurements

When your test returns multiple values:

```python
@litmus_test
def test_power(context, psu, dmm):
    return {
        "input_voltage": psu.measure_voltage(),
        "output_voltage": dmm.measure_dc_voltage(),
        "current": psu.measure_current(),
    }
```

Configure limits for each measurement:

```yaml
test_power:
  limits:
    input_voltage:
      low: 4.5
      high: 5.5
      units: V
    output_voltage:
      low: 3.135
      high: 3.465
      units: V
    current:
      low: 0
      high: 1.0
      units: A
```

## Limit Sources

### 1. Direct Values (Most Common)

Specify limits directly in a sequence step or inline decorator:

```yaml
# In a sequence step
limits:
  test_output_voltage:
    low: 3.135
    high: 3.465
```

### 2. Derived from Product Spec

Reference the product spec and apply guardbanding:

```yaml
# In a sequence step or inline config
limits:
  test_output_voltage:
    ref: specs.power_board.characteristics.output_voltage
    guardband_pct: 10  # Tighten by 10%
```

This:
1. Loads the spec value (e.g., 3.3V ± 5%)
2. Calculates limits (3.135 to 3.465)
3. Applies guardband (3.152 to 3.449)

### 3. Callable Limits (Dynamic)

For limits that depend on test conditions or require complex logic, use callable limits:

```yaml
# In a sequence step or inline config
limits:
  test_output_voltage:
    callable: myproject.limits.output_voltage

  test_efficiency:
    callable: "Limit(low=80 if ctx.get_in('load') > 0.5 else 85, units='%')"

  test_ripple:
    callable: |
      vin = ctx.get_in('vin')
      if vin < 5.0:
        return Limit(high=vin * 0.01, units='V')
      else:
        return Limit(high=0.05, units='V')
```

**Module function example:**

```python
# myproject/limits.py
from litmus.config.models import Limit

def output_voltage(context) -> Limit:
    """Temperature-dependent limits with full context access."""
    temp = context.get_in("temperature", 25)

    if temp < 0:
        return Limit(low=3.0, high=3.6, units="V")
    elif temp < 50:
        return Limit(low=3.1, high=3.5, units="V")
    else:
        return Limit(low=3.0, high=3.6, units="V")
```

Callable limits have access to:
- `ctx.get_in(key)` — Input parameters (from vectors)
- `ctx.get_out(key)` — Observations (from `context.observe()`)
- `ctx.inputs` — All input parameters as dict
- `ctx.outputs` — All observations as dict
- `Limit` — The Limit class for creating limits

### 4. Inline Decorator (Dev Fallback)

For ad-hoc runs without a sequence, pass limits in the decorator:

```python
@litmus_test(
    limits={"test_voltage": Limit(low=3.0, high=3.6, units="V")}
)
def test_voltage(context, dmm):
    return dmm.measure_dc_voltage()
```

When running with `--sequence`, the sequence step limits override these.

## Comparators

The `comparator` field determines how values are checked:

| Comparator | Pass Condition | Use Case |
|------------|----------------|----------|
| `GELE` (default) | `low ≤ value ≤ high` | Normal range checking |
| `GELT` | `low ≤ value < high` | Left-inclusive only |
| `GTLE` | `low < value ≤ high` | Right-inclusive only |
| `GTLT` | `low < value < high` | Exclusive range |
| `GE` | `value ≥ low` | Only lower bound |
| `GT` | `value > low` | Strictly greater |
| `LE` | `value ≤ high` | Only upper bound |
| `LT` | `value < high` | Strictly less |
| `EQ` | `value == nominal` | Exact match |
| `NE` | `value ≠ nominal` | Must not equal |

Examples:

```yaml
# Standard range (3.135 to 3.465 V)
test_voltage:
  limits:
    test_voltage:
      low: 3.135
      high: 3.465
      comparator: GELE  # Default

# Minimum only (must be ≥ 60%)
test_efficiency:
  limits:
    test_efficiency:
      low: 60
      comparator: GE
      units: "%"

# Maximum only (must be ≤ 10mA)
test_quiescent:
  limits:
    test_quiescent:
      high: 10
      comparator: LE
      units: mA

# Exact match (for calibration)
test_reference:
  limits:
    test_reference:
      nominal: 1.000
      comparator: EQ
      units: V
```

## No Limits (Characterization Mode)

If no limits are configured for a test, measurements still get recorded but always pass:

```python
@litmus_test
def test_characterize(context, dmm):
    """Collect data without pass/fail."""
    return dmm.measure_dc_voltage()  # No limits → always PASS
```

To prevent accidental test runs without limits, use `raise_on_fail=True` (default) and ensure limits exist.

## Config Resolution

Limits are resolved in this order:

1. **Sequence step** `limits:` — When running with `--sequence`
2. **Inline decorator** `limits=` — For ad-hoc pytest runs
3. **Product spec** `ref:` — Derived from spec characteristics
4. **No limits** — Characterization mode (always passes)

## Complete Example

**Sequence:**
```yaml
# sequences/power_board_smoke.yaml
steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    vectors:
      - vin: 5.0
    limits:
      test_output_voltage:
        low: 3.135
        high: 3.465
        nominal: 3.3
        units: V
        spec_ref: "output_voltage @ tolerance_pct=5"

  - id: psu_check
    test: tests/test_power.py::test_psu
    vectors:
      expand: product
      load: [0.1, 0.5, 1.0]
    limits:
      voltage:
        low: 4.9
        high: 5.1
        units: V
      current:
        low: 0
        high: 1.5
        units: A
```

**Test code:**
```python
from litmus.execution import litmus_test

@litmus_test(
    limits={"test_output_voltage": {"low": 3.135, "high": 3.465, "nominal": 3.3, "units": "V"}},
)
def test_output_voltage(context, psu, dmm):
    """Inline limits for dev; sequence overrides in production."""
    psu.set_voltage(context.get_in("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()

@litmus_test
def test_psu(context, psu, dmm):
    load = context.inputs["load"]
    return {
        "voltage": psu.measure_voltage(),
        "current": psu.measure_current(),
    }
```

## Best Practices

1. **Always specify limits** — Tests without limits are just data collection

2. **Include spec_ref** — Link limits to specifications for traceability

3. **Use meaningful units** — Helps with reporting and debugging

4. **Derive from specs** — Use `ref` and `guardband_pct` when possible

5. **Match names** — Measurement name in limits must match what the test returns

6. **Use sequences for production** — Inline decorator for dev, sequence steps for production
