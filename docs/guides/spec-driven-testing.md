# Spec-Driven Testing

Derive test limits automatically from product specifications.

## Overview

Instead of hardcoding limits in tests:

```python
# Before: Limits in code
assert 3.135 < voltage < 3.465
```

Derive them from specifications:

```python
# After: Limits from spec
harness.measure("output_voltage", voltage)  # Limits auto-resolved
```

## The SpecContext

SpecContext bridges product specs and test execution:

```python
from litmus.products import SpecContext

# Load spec
spec = SpecContext.from_file("products/power_board/spec.yaml")

# Get limit for characteristic
limit = spec.get_limit("output_voltage", temperature=25, load=0.1)
# Returns: Limit(low=3.135, high=3.465, spec_ref="Section 7.2 @ ...")
```

## Product Specification

Define characteristics with specs at different operating conditions:

```yaml
# products/power_board/spec.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

pins:
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"

characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    datasheet_ref: "Section 7.2"
    specs:
      - conditions:
          temperature: {min: 0, max: 50}
          load: {min: 0.1, max: 0.5}
        value: 3.3
        accuracy:
          pct_reading: 5    # ±5% tolerance

      - conditions:
          temperature: {min: 50, max: 85}
          load: {min: 0.5, max: 1.0}
        value: 3.3
        accuracy:
          pct_reading: 7    # Wider tolerance at high temp
```

## Using SpecContext

### Basic Usage

```python
from litmus.products import SpecContext

spec = SpecContext.from_file("products/power_board/spec.yaml")

# Get limit
limit = spec.get_limit("output_voltage")
print(f"Low: {limit.low}, High: {limit.high}")

# Get limit at specific conditions
limit = spec.get_limit("output_voltage", temperature=85)
print(f"At 85°C: {limit.low} to {limit.high}")
```

### With Guardband

```python
# Apply 10% guardband (tighten limits)
spec = SpecContext.from_file(
    "products/power_board/spec.yaml",
    guardband_pct=10.0
)

limit = spec.get_limit("output_voltage")
# Original: 3.135 to 3.465
# Guardbanded: 3.152 to 3.449
```

### Pin Information

```python
# Get pin info for traceability
pin_info = spec.get_pin_info("output_voltage")
print(pin_info)
# {"dut_pin": "VOUT", "name": "J1.3", "net": "VOUT_3V3"}
```

## With TestHarness

```python
from litmus.execution.harness import TestHarness
from litmus.products import SpecContext

spec = SpecContext.from_file("products/power_board/spec.yaml", guardband_pct=10.0)

harness = TestHarness(
    step_name="test_output",
    spec_context=spec,
)

with harness.step():
    voltage = dmm.measure_voltage()
    harness.measure("output_voltage", voltage)
    # Limits automatically resolved from spec!
```

## With @litmus_test

Configure spec-based limit in YAML:

```yaml
# tests/config.yaml
test_output_voltage:
  limits:
    output_voltage:
      ref: output_voltage    # Reference to characteristic in product spec
      guardband_pct: 10
```

```python
@litmus_test
def test_output_voltage(context, instruments):
    return instruments["dmm"].measure_voltage()
```

## Condition Matching

SpecContext finds the best matching condition:

```python
# Spec has conditions for temp=25 and temp=85
spec = SpecContext.from_file("products/power_board/spec.yaml")

# Exact match
limit = spec.get_limit("output_voltage", temperature=25)

# Nearest match (no exact 50°C defined)
limit = spec.get_limit("output_voltage", temperature=50)
# Uses temp=25 condition (or interpolates if configured)
```

## Guardband Calculation

Guardband tightens limits for manufacturing margin:

```
Spec: 3.3V ± 5%
  Low: 3.3 - 0.165 = 3.135V
  High: 3.3 + 0.165 = 3.465V

With 10% guardband:
  Range: 3.465 - 3.135 = 0.33V
  Guardband: 0.33 × 0.10 = 0.033V
  New Low: 3.135 + 0.0165 = 3.152V
  New High: 3.465 - 0.0165 = 3.449V
```

## Traceability

Measurements link back to specs:

```python
harness.measure("output_voltage", voltage)

# Measurement includes:
# - spec_ref: "Section 7.2 @ temperature=25, load=0.5"
# - dut_pin: "VOUT"
# - characteristic: "output_voltage"
```

Query results with spec references:

```python
from litmus import LitmusClient

client = LitmusClient()
measurements = client.get_measurements(run_id)

for m in measurements:
    print(f"{m['measurement_name']}: {m['value']} {m['units']}")
    print(f"  Spec: {m['spec_ref']}")
    print(f"  Pin: {m['dut_pin']}")
```

## Complete Workflow

### 1. Define Product Spec

```yaml
# products/power_board/spec.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

pins:
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"

characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 3.3
        accuracy:
          pct_reading: 5
```

### 2. Configure Test

```yaml
# tests/config.yaml
test_output_voltage:
  vectors:
    expand: product
    temperature: [25, 85]
    load: [0.5, 1.0]
  limits:
    output_voltage:
      ref: output_voltage          # Reference to characteristic in spec
      guardband_pct: 10            # Tighten limits by 10%
      comparator: GELE
```

### 3. Write Test

```python
@litmus_test
def test_output_voltage(context, instruments, spec):
    """Verify output voltage per spec."""
    # context provides conditions
    temp = context["temperature"]
    load = context["load"]

    # Configure conditions
    set_chamber_temp(temp)
    set_load(load)

    # Measure
    voltage = instruments["dmm"].measure_voltage()

    return voltage
    # Limits resolved from spec at context conditions
```

### 4. Run

```bash
pytest tests/ --station=bench_1 --dut-serial=SN12345
```

### 5. Results

```
test_output_voltage[25-0.5] PASSED
  output_voltage: 3.31V (limit: 3.152-3.449)
  spec_ref: output_voltage @ temperature=25, load=0.5

test_output_voltage[85-1.0] PASSED
  output_voltage: 3.28V (limit: 3.119-3.481)
  spec_ref: output_voltage @ temperature=85, load=1.0
```

## Benefits

1. **Single source of truth** — Limits come from specs
2. **Automatic guardbanding** — Manufacturing margin built in
3. **Full traceability** — Results link to spec sections
4. **Condition handling** — Different limits at different conditions
5. **Reduced errors** — No manual limit entry

## Next Steps

- [Configuration Reference](../reference/configuration.md) — Spec YAML schema
- [Writing Tests](writing-tests.md) — Test patterns
- [Concepts: Products](../concepts/products.md) — Product specifications
