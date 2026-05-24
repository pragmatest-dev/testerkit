# Step 6: Product Specifications

**Goal:** Define product specifications and link test limits to them.

## What You'll Build

A product specification that documents your device and provides traceability for test limits.

## Project Structure

```
my_project/
├── products/
│   └── power_board.yaml         # Product specification
├── tests/
│   ├── test_power.py            # Test code (pytest functions or classes)
│   └── test_power.yaml          # Sidecar — limits, sweeps, mocks for test_power.py
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
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    bands:
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
    role: power            # signal, ground, power, reference
```

### [Characteristics](../concepts/configuration/capabilities.md)

Measurable properties with expected values (each entry in `bands:` is a [`SpecBand`](../reference/data/models.md) — a value-plus-condition record):

```yaml
characteristics:
  output_voltage:
    direction: output      # DUT outputs this
    function: dc_voltage   # DC voltage measurement
    units: V
    pins: [VOUT]           # Measured at this pin
    bands:
      - value: 3.3         # Expected value
        accuracy:
          pct_reading: 5   # ±5% tolerance
```

## Deriving Limits from Specs

The spec says: output_voltage = 3.3V ± 5%

Calculate limits:
- Low: 3.3 × (1 - 0.05) = 3.135V
- High: 3.3 × (1 + 0.05) = 3.465V

Put these in the sidecar YAML next to the test file:

```yaml
# tests/test_power.yaml
limits:
  output_voltage:
    low: 3.135
    high: 3.465
    nominal: 3.3
    units: V
    spec_ref: "output_voltage @ tolerance_pct=5"  # Traceability!
```

The `spec_ref` field provides [traceability](../how-to/execution/traceability.md) back to the specification.

## Guardbanding

For production testing, you often want tighter limits than the spec allows. This is called guardbanding:

```
Spec:       3.3V ± 5%  = 3.135V to 3.465V
Guardband:  10% tighter
Production: 3.152V to 3.449V
```

Calculate guardbanded limits in the sidecar — the product spec stays
the source-of-truth for the characteristic value/accuracy, and the
sidecar narrows it via `tolerance_pct` (or hard `low`/`high`) for
the production run:

```yaml
# tests/test_power.yaml
limits:
  output_voltage:
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
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
        when:
          temperature: 25    # At room temperature
          load: 0.5

      - value: 3.3
        accuracy: {pct_reading: 7}   # Wider tolerance at high temp
        when:
          temperature: 85
          load: 0.5
```

Sweep these conditions from the sidecar:

```yaml
# tests/test_power.yaml
sweeps:
  - temperature: [25, 85]
  - load: [0.5]
limits:
  # Different limits per condition resolve from the spec at runtime
```

## Why Separate Spec from Sidecar?

| Spec (products/*.yaml) | Sidecar (tests/test_*.yaml) |
|-------|--------|
| What the product SHOULD do | How this test file exercises it |
| From datasheet/requirements | Test-specific parameters |
| Rarely changes | May change per environment |
| Shared across test files | Co-located with one test file |

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
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

**tests/test_power.yaml** (sidecar):
```yaml
limits:
  output_voltage:
    low: 3.152
    high: 3.449
    nominal: 3.3
    units: V
    spec_ref: "output_voltage @ guardband=10%"
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.31
```

**tests/test_power.py:**
```python
def test_output_voltage(dmm, verify):
    """Verify output voltage meets guardbanded spec."""
    verify("output_voltage", dmm.measure_dc_voltage())
```

## Traceability Chain

```
Datasheet → Spec → Test Requirement → Sidecar Limits → Test Code → Measurement
     ↓          ↓           ↓                ↓             ↓           ↓
  3.3V±5%   conditions   guardband      low/high      verify     3.31V PASS
```

Every measurement can be traced back to the original specification.

## What You Learned

- Product specification structure (product, pins, characteristics)
- Conditions for operating points
- Guardbanding for production margins
- Traceability from spec to test results

## Continue

Now let's connect to real instruments.

← [Step 5: Test Configuration](05-configuration.md)  |  [Step 7: Real Instruments →](07-real-instruments.md)
