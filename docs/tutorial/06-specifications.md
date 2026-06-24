# Step 6: Part Specifications

**Goal:** Define part specifications and link test limits to them.

## What You'll Build

A part specification that documents your device and provides traceability for test limits.

## Project Structure

```
my_project/
├── parts/
│   └── power_board.yaml         # Part specification
├── tests/
│   ├── test_power.py            # Test code (pytest functions or classes)
│   └── test_power.yaml          # Sidecar — limits, sweeps, mocks for test_power.py
└── pyproject.toml
```

## The Part Spec

Define what you're testing:

```yaml
# parts/power_board.yaml
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
    unit: V
    pins: [VIN]
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    unit: V
    pins: [VOUT]
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

## What the Spec Defines

### Part Identity

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

Measurable properties with expected values (each entry in `bands:` is a [`SpecBand`](../reference/data/models.md) — an expected value with the operating conditions it applies at):

```yaml
characteristics:
  output_voltage:
    direction: output      # UUT outputs this
    function: dc_voltage   # DC voltage measurement
    unit: V
    pins: [VOUT]           # Measured at this pin
    bands:
      - value: 3.3         # Expected value
        accuracy:
          pct_reading: 5   # ±5% tolerance
```

## Deriving Limits from Specs

A sidecar limit can pull its bounds straight from the part spec. Set `characteristic:` on the limit and Litmus reads that characteristic's value and accuracy from the part spec — at the operating condition in play — and turns it into `low`/`high`:

```yaml
# tests/test_power.yaml
limits:
  output_voltage:
    characteristic: output_voltage    # pull value + accuracy from the part spec
```

With the spec above (`3.3 V ± 5%`) that resolves to `low: 3.135`, `high: 3.465` automatically. Change the spec and every test that references it follows — no hand-computed numbers to keep in sync.

When a limit doesn't come from a part spec, write explicit `low`/`high` instead:

```yaml
limits:
  output_voltage:
    low: 3.135
    high: 3.465
    nominal: 3.3
    unit: V
    spec_ref: "TPS54302 datasheet, Table 6.5"   # free-text note, documentation only
```

`spec_ref` is a free-text [traceability](../how-to/execution/traceability.md) note recorded with the measurement — it isn't read to compute the limit. The field that links a limit to the spec is `characteristic:`.

## Guardbanding

For production testing, you often want tighter limits than the spec allows. This is called guardbanding:

```
Spec:       3.3V ± 5%  = 3.135V to 3.465V
Guardband:  10% tighter
Production: 3.152V to 3.449V
```

Add `guardband_pct:` to a spec-derived limit to pull both bounds inward:

```yaml
# tests/test_power.yaml
limits:
  output_voltage:
    characteristic: output_voltage
    guardband_pct: 10        # tighten the spec band by 10% for production
```

The part spec stays the master copy of the value and accuracy; the sidecar tightens it per run. (To set a band width directly from a nominal instead of the spec's accuracy, use `tolerance_pct:`.)

## Conditions

Characteristics can have different values at different operating conditions:

```yaml
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    unit: V
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

Sweep these conditions from the sidecar and bind the limit to the characteristic — Litmus picks the `SpecBand` whose `when:` matches each vector:

```yaml
# tests/test_power.yaml
sweeps:
  - temperature: [25, 85]
  - load: [0.5]
limits:
  output_voltage:
    characteristic: output_voltage    # ±5% at 25 °C, ±7% at 85 °C — picked per vector
```

## Why Separate Spec from Sidecar?

| Spec (parts/*.yaml) | Sidecar (tests/test_*.yaml) |
|-------|--------|
| What the part SHOULD do | How this test file exercises it |
| From datasheet/requirements | Test-specific parameters |
| Rarely changes | May change per environment |
| Shared across test files | Co-located with one test file |

## Complete Example

**parts/power_board.yaml:**
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
    unit: V
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output
    function: dc_voltage
    unit: V
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

**tests/test_power.yaml** (sidecar):
```yaml
limits:
  output_voltage:
    characteristic: output_voltage
    guardband_pct: 10
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

- Part specification structure (part, pins, characteristics)
- Auto-deriving limits from the spec with `characteristic:`
- Conditions for operating points
- Guardbanding for production margins with `guardband_pct:`
- Traceability from spec to test results

## Continue

Now let's connect to real instruments.

← [Step 5: Test Configuration](05-configuration.md)  |  [Step 7: Real Instruments →](07-real-instruments.md)
