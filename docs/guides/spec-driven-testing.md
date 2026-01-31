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
spec = SpecContext.from_file("specs/power_board.yaml")

# Get limit for characteristic
limit = spec.get_limit("output_voltage", temperature=25, load=0.1)
# Returns: Limit(low=3.135, high=3.465, spec_ref="Section 7.2 @ ...")
```

## Product Specification

Define characteristics with conditions:

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
    datasheet_ref: "Section 7.2"
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
        temperature: 25
        load: 0.5

      - nominal: 3.3
        tolerance_pct: 7
        temperature: 85
        load: 1.0

test_requirements:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10
    priority: 1
```

## Using SpecContext

### Basic Usage

```python
from litmus.products import SpecContext

spec = SpecContext.from_file("specs/power_board.yaml")

# Get limit
limit = spec.get_limit("output_voltage")
print(f"Low: {limit.low}, High: {limit.high}")

# Get limit at specific conditions
limit = spec.get_limit("output_voltage", temperature=85)
print(f"At 85°C: {limit.low} to {limit.high}")
```

### With Guardband

```python
from decimal import Decimal

# Apply 10% guardband (tighten limits)
spec = SpecContext.from_file(
    "specs/power_board.yaml",
    guardband_pct=Decimal("10")
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

spec = SpecContext.from_file("specs/power_board.yaml", guardband_pct=Decimal("10"))

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

Configure spec reference in YAML:

```yaml
# tests/config.yaml
test_output_voltage:
  spec: specs/power_board.yaml
  guardband_pct: 10
  limits:
    output_voltage:
      ref: output_voltage  # Reference to characteristic
```

```python
@litmus_test
def test_output_voltage(vector, instruments):
    return instruments["dmm"].measure_voltage()
```

## Condition Matching

SpecContext finds the best matching condition:

```python
# Spec has conditions for temp=25 and temp=85
spec = SpecContext.from_file("specs/power_board.yaml")

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
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
```

### 2. Define Test Requirements

```yaml
# In same file or separate
test_requirements:
  verify_output_voltage:
    characteristic_ref: output_voltage
    guardband_pct: 10
    priority: 1
```

### 3. Configure Test

```yaml
# tests/config.yaml
test_output_voltage:
  spec: specs/power_board.yaml
  guardband_pct: 10
  vectors:
    expand: product
    temperature: [25, 85]
    load: [0.5, 1.0]
```

### 4. Write Test

```python
@litmus_test
def test_output_voltage(vector, instruments, spec):
    """Verify output voltage per spec."""
    # Vector provides conditions
    temp = vector["temperature"]
    load = vector["load"]

    # Configure conditions
    set_chamber_temp(temp)
    set_load(load)

    # Measure
    voltage = instruments["dmm"].measure_voltage()

    return voltage
    # Limits resolved from spec at vector conditions
```

### 5. Run

```bash
pytest tests/ --station=bench_1 --dut-serial=SN12345
```

### 6. Results

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
