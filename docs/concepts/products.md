# Products

A **Product** is what you're testing — a PCB, module, or device. Product specs define the physical interface and electrical characteristics that need to be tested.

## Product Specification

Product specs are defined in YAML files, typically in `specs/` or `products/*/spec.yaml`:

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"
  revision: "A"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal

characteristics:
  input_voltage:
    direction: input       # DUT receives this
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VIN]
    conditions:
      - nominal: 5.0
        tolerance_pct: 10

  output_voltage:
    direction: output      # DUT provides this
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5

test_requirements:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10      # Tighten limits by 10%
    priority: 1
```

## Pins

**Pins** represent physical connection points on the DUT — connectors, test points, or pads.

```yaml
pins:
  VIN:
    name: "J1.1"           # Physical designator
    net: "VIN_5V"          # Schematic net name
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal
  SDA:
    name: "J2.1"
    net: "I2C_SDA"
    type: signal
```

### Pin Types

| Type | Description |
|------|-------------|
| `signal` | General signal pin (default) |
| `power` | Power supply pin |
| `ground` | Ground reference |
| `nc` | No connect / reserved |

## Characteristics

**Characteristics** are measurable properties of the product. Each characteristic has:

- **Direction** — Does the DUT provide or receive this?
- **Domain** — What physical quantity? (voltage, current, etc.)
- **Signal types** — DC, AC, pulsed?
- **Conditions** — Expected values and tolerances

### Direction Matters

The `direction` field describes the DUT's perspective:

| Direction | Meaning | Instrument Needs |
|-----------|---------|------------------|
| `input` | DUT receives power/signal | Instrument must **source** |
| `output` | DUT provides power/signal | Instrument must **measure** |
| `bidir` | DUT both receives and provides | Instrument must do both |

### Multiple Characteristics Per Pin

A single pin can have multiple characteristics:

```yaml
pins:
  VOUT:
    name: "J1.3"
    type: signal

characteristics:
  output_voltage:
    pins: [VOUT]
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V

  output_ripple:
    pins: [VOUT]           # Same pin, different measurement
    direction: output
    domain: voltage
    signal_types: [ac]
    units: mV
```

## Conditions

Conditions define expected values at specific operating points:

```yaml
characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
        temperature: 25
        load: 0.5

      - nominal: 3.3
        tolerance_pct: 7      # Wider tolerance at high temp
        temperature: 85
        load: 1.0
```

### Tolerance Options

| Field | Description |
|-------|-------------|
| `nominal` | Expected value |
| `tolerance_pct` | Percentage tolerance (e.g., 5 = ±5%) |
| `tolerance_abs` | Absolute tolerance |
| `limit_low` / `limit_high` | Explicit limits |

## Signal Groups (Buses)

Group related signals for protocols like I2C, SPI, or UART:

```yaml
pins:
  SDA:
    name: "J2.1"
    type: signal
  SCL:
    name: "J2.2"
    type: signal

signal_groups:
  i2c_main:
    protocol: i2c
    signals:
      - pin: SDA
        role: data
      - pin: SCL
        role: clock
    parameters:
      frequency: 400000
```

## Test Requirements

**Test requirements** specify which characteristics to test, with optional guardbanding:

```yaml
test_requirements:
  verify_output:
    characteristic_ref: output_voltage
    conditions:
      temperature: 25
      load: 0.5
    guardband_pct: 10      # Tighten limits by 10% for manufacturing
    priority: 1

  verify_output_hot:
    characteristic_ref: output_voltage
    conditions:
      temperature: 85
    priority: 2
```

### Guardband

Guardbanding tightens limits to provide manufacturing margin:

```
Spec: 3.3V ± 5% = 3.135V to 3.465V
With 10% guardband:
  Range: 0.33V
  Guardband: 0.033V
  New limits: 3.168V to 3.432V
```

## Minimal Spec

The simplest spec that works:

```yaml
product:
  id: minimal_board
  name: "Minimal Example"

pins:
  VOUT:
    name: "J1.1"

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 5.0
        tolerance_pct: 10
```

## Loading Products

In Python:

```python
from litmus.products.loader import load_product

product = load_product("specs/power_board.yaml")
print(product.id)
print(product.characteristics["output_voltage"].nominal)
```

## Next Steps

- [Stations](stations.md) — Configuring test benches
- [Capabilities](capabilities.md) — Understanding capability matching
- [Configuration Reference](../reference/configuration.md) — YAML schema details
