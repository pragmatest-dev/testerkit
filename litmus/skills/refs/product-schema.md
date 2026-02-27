# Products

A **Product** is what you're testing — a PCB, module, or device. Product specs define the physical interface and electrical characteristics that need to be tested.

## Product Specification

Product specs are defined in YAML files, in `products/{product_id}/spec.yaml`:

```yaml
# products/power_board/spec.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"
  part_number: "DPB-001"
  revision: "A"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    role: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    role: signal

characteristics:
  input_voltage:
    direction: input       # DUT receives this
    function: dc_voltage
    units: V
    pins: [VIN]
    specs:
      - value: 5.0
        accuracy:
          pct_reading: 10  # ±10% tolerance

  output_voltage:
    direction: output      # DUT provides this
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 3.3
        accuracy:
          pct_reading: 5   # ±5% tolerance
```

## Pins

**Pins** represent physical connection points on the DUT — connectors, test points, or pads.

```yaml
pins:
  VIN:
    name: "J1.1"           # Physical designator
    net: "VIN_5V"          # Schematic net name
    role: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    role: signal
  SDA:
    name: "J2.1"
    net: "I2C_SDA"
    role: signal
```

### Pin Roles

| Role | Description |
|------|-------------|
| `signal` | Measured/stimulated signal (default) |
| `power` | Power input/output (VIN, VOUT) |
| `ground` | Current return / reference |
| `reference` | Voltage reference, not driven |

## Characteristics

**ProductCharacteristic extends Capability** — all rules from `docs/capability-schema.md` apply.

Each characteristic uses the **four-dict structure**:

| Dict | Purpose | Product Example |
|------|---------|-----------------|
| `signals` | Primary measurable quantities | `voltage: {range: {min: 3.0, max: 3.6}, value: 3.3}` |
| `conditions` | Operating conditions for specs | `temperature: {range: {min: -40, max: 85, units: degC}}` |
| `controls` | Instrument settings needed | `coupling: {options: [AC, DC], default: DC}` |
| `attributes` | Non-numeric metadata | `connector_type: {value: BNC}` |

Plus product-specific fields: `pin`, `pins`, `net`, `signal_group`, `datasheet_ref`.

### SpecBand `when` clause types

| Type | YAML | Matching |
|------|------|----------|
| `RangeSpec` | `{min: 0, max: 50, units: degC}` | Value within range |
| `PointSpec` | `{value: 1e6, units: Hz}` | Exact equality |
| `ListSpec` | `{values: [50, 600], units: ohm}` | Membership |
| scalar | `25` or `"AC"` | Equality |
| list | `[50, 600]` | Membership |

### Antipatterns

- **Name-encoded characteristics** — DON'T: `output_3v3_at_25C`. DO: use `when` clauses.
- **Flat attribute pairs** — DON'T: `accuracy_min: 0.01, accuracy_max: 0.05`. DO: use `accuracy: {pct_reading: 1.0}`.

Each characteristic has:

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
    role: signal

characteristics:
  output_voltage:
    pins: [VOUT]
    direction: output
    function: dc_voltage
    units: V
    specs:
      - value: 3.3
        accuracy:
          pct_reading: 5

  output_ripple:
    pins: [VOUT]           # Same pin, different measurement
    direction: output
    function: ac_voltage
    units: mV
    specs:
      - value: 0
        accuracy:
          absolute: 50
```

## Specifications with Conditions

Each characteristic has one or more **specs** (SpecBands) that define expected values at specific operating conditions:

```yaml
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - when:
          temperature: {min: 0, max: 50}
          load: {min: 0.1, max: 0.5}
        value: 3.3
        accuracy:
          pct_reading: 5     # ±5% tolerance

      - when:
          temperature: {min: 50, max: 85}
          load: {min: 0.5, max: 1.0}
        value: 3.3
        accuracy:
          pct_reading: 7     # Wider tolerance at high temp
```

### Accuracy Options

| Field | Description |
|-------|-------------|
| `pct_reading` | Percentage of the measured value |
| `pct_range` | Percentage of the full range |
| `absolute` | Fixed absolute tolerance value |

Multiple accuracy components can be combined (e.g., `pct_reading: 1.0, absolute: 0.01` means ±(1% of reading + 0.01)).

## Signal Groups (Buses)

Group related signals for protocols like I2C, SPI, or UART:

```yaml
pins:
  SDA:
    name: "J2.1"
    role: signal
  SCL:
    name: "J2.2"
    role: signal

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
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 5.0
        accuracy:
          pct_reading: 10
```

## Part Numbers

The `part_number` field maps a product to its manufacturing part number. When present, it automatically populates `dut_part_number` in test results (unless overridden by `--dut-part-number` on the CLI). This enables yield analytics filtering by part number.

```yaml
product:
  id: power_board
  part_number: "DPB-001"
  name: "5V to 3.3V Converter"
```

## Variant Inheritance

Product families can share specs using the `base` field. A variant inherits all fields from its base product and overrides specific sections:

```yaml
# products/power_board_industrial/spec.yaml
product:
  id: power_board_industrial
  base: power_board              # Inherits from products/power_board/spec.yaml
  part_number: "DPB-001-IND"
  name: "5V to 3.3V Converter (Industrial)"

# Omitted sections (pins, signal_groups) are inherited from base.
# Sections that ARE present replace the base entirely:
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 3.3
        accuracy:
          pct_reading: 3          # Tighter tolerance for industrial
```

Inheritance rules:
- **Header fields** (`name`, `description`, `revision`, `part_number`, `datasheet`, `schematic`) — inherited when absent in variant
- **Sections** (`pins`, `characteristics`, `signal_groups`) — variant replaces entirely if present, otherwise inherited
- `id` and `base` always come from the variant
- Max inheritance depth: 5 levels. Circular references raise an error.

## Loading Products

In Python:

```python
from litmus.products.loader import load_product

product = load_product("products/power_board/spec.yaml")
print(product.id)
print(product.characteristics["output_voltage"].nominal)
```

## Next Steps

- [Stations](stations.md) — Configuring test benches
- [Capabilities](capabilities.md) — Understanding capability matching
- [Configuration Reference](../reference/configuration.md) — YAML schema details
