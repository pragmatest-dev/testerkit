# Step 6: Product Specifications

**Goal:** Define product specifications and link test limits to them.

## What You'll Build

A product specification that documents your device and provides traceability for test limits.

## Project Structure

```
my_project/
├── products/
│   └── power_board.yaml    # Product specification
├── sequences/
│   └── power_board.yaml    # Test steps with limits derived from spec
├── tests/
│   └── test_power.py       # Test code
└── pyproject.toml
```

## The Product Spec

Define what you're testing:

```yaml
# products/power_board.yaml
id: power_board
name: "5V to 3.3V Converter"
revision: "A"
description: "Low-dropout regulator module"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    role: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    role: signal
  GND:
    name: "J1.2"
    net: "GND"
    role: ground

characteristics:
  input_voltage:
    direction: input
    function: dc_voltage
    units: V
    pins: [VIN]
    specs:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

## What the Spec Defines

### Product Identity

```yaml
id: power_board           # Unique identifier
name: "5V to 3.3V Converter"
revision: "A"
description: "..."
```

### Pins

Physical connection points on the device:

```yaml
pins:
  VIN:
    name: "J1.1"           # Physical marking
    net: "VIN_5V"          # Schematic net name
    role: power            # power, signal, ground, control
```

### Characteristics

Measurable properties with expected values:

```yaml
characteristics:
  output_voltage:
    direction: output      # DUT outputs this
    function: dc_voltage   # DC voltage measurement
    units: V
    pins: [VOUT]           # Measured at this pin
    specs:
      - value: 3.3         # Expected value
        accuracy:
          pct_reading: 5   # ±5% tolerance
```

## Deriving Limits from Specs

The spec says: output_voltage = 3.3V ± 5%

Calculate limits:
- Low: 3.3 × (1 - 0.05) = 3.135V
- High: 3.3 × (1 + 0.05) = 3.465V

Put these in your sequence step:

```yaml
# sequences/power_board.yaml
steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    limits:
      test_output_voltage:
        low: 3.135
        high: 3.465
        nominal: 3.3
        units: V
        spec_ref: "output_voltage @ tolerance_pct=5"  # Traceability!
```

The `spec_ref` field provides traceability back to the specification.

## Guardbanding

For production testing, you often want tighter limits than the spec allows. This is called guardbanding:

```
Spec:       3.3V ± 5%  = 3.135V to 3.465V
Guardband:  10% tighter
Production: 3.152V to 3.449V
```

Document this in the spec:

```yaml
# products/power_board.yaml
specs:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10
    priority: 1
```

Then calculate guardbanded limits for your sequence step:

```yaml
# sequences/power_board.yaml
steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    limits:
      test_output_voltage:
        low: 3.152      # With 10% guardband
        high: 3.449
        spec_ref: "output_voltage @ guardband=10%"
```

## Conditions

Characteristics can have different values at different operating conditions:

```yaml
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    specs:
      - value: 3.3
        accuracy: {pct_reading: 5}
        conditions:
          temperature: 25    # At room temperature
          load: 0.5

      - value: 3.3
        accuracy: {pct_reading: 7}   # Wider tolerance at high temp
        conditions:
          temperature: 85
          load: 0.5
```

Your sequence step vectors should sweep these conditions:

```yaml
# sequences/power_board_char.yaml
steps:
  - id: output_voltage_sweep
    test: tests/test_power.py::test_output_voltage
    vectors:
      expand: product
      temperature: [25, 85]
      load: [0.5]
    limits:
      # Different limits for each condition...
```

## Why Separate Spec from Sequence?

| Spec (products/*.yaml) | Sequence (sequences/*.yaml) |
|-------|--------|
| What the product SHOULD do | How we TEST it |
| From datasheet/requirements | Test-specific parameters |
| Rarely changes | May change per environment |
| Shared across test suites | Specific to test phase |

## Complete Example

**products/power_board.yaml:**
```yaml
id: power_board
name: "5V to 3.3V Converter"

pins:
  VIN:
    name: "J1.1"
    role: power
  VOUT:
    name: "J1.3"
    role: signal

characteristics:
  input_voltage:
    direction: input
    function: dc_voltage
    units: V
    specs:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    specs:
      - value: 3.3
        accuracy: {pct_reading: 5}

specs:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10
```

**sequences/power_board.yaml:**
```yaml
id: power_board
product_family: power_board
test_phase: production

steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    limits:
      test_output_voltage:
        low: 3.152
        high: 3.449
        nominal: 3.3
        units: V
        spec_ref: "output_voltage @ guardband=10%"
    mocks:
      dmm.measure_voltage: 3.31
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, dmm):
    """Verify output voltage meets guardbanded spec."""
    return dmm.measure_voltage()
```

## Traceability Chain

```
Datasheet → Spec → Test Requirement → Test Config → Test Code → Measurement
     ↓          ↓           ↓               ↓            ↓           ↓
  3.3V±5%   conditions   guardband      low/high     return     3.31V PASS
```

Every measurement can be traced back to the original specification.

## What You Learned

- Product specification structure (product, pins, characteristics)
- Conditions for operating points
- Guardbanding for production margins
- Traceability from spec to test results

## Next Step

Now let's connect to real instruments.

[Step 7: Real Instruments →](07-real-instruments.md)
