# Parts

A **Part** is what you're testing — a PCB, module, or device. Part specs define the physical interface and electrical characteristics that need to be tested.

## Part Specification

Part specs are defined in YAML files, in `parts/{part_id}.yaml`:

```yaml
# parts/power_board.yaml
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
    direction: input       # UUT receives this
    function: dc_voltage
    units: V
    pins: [VIN]
    bands:
      - value: 5.0
        accuracy:
          pct_reading: 10  # ±10% tolerance

  output_voltage:
    direction: output      # UUT provides this
    function: dc_voltage
    units: V
    pins: [VOUT]
    bands:
      - value: 3.3
        accuracy:
          pct_reading: 5   # ±5% tolerance
```

## Pins

**Pins** represent physical connection points on the UUT — connectors, test points, or pads.

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

### Pin Types

| Role | Description |
|------|-------------|
| `signal` | General measured/stimulated signal (default) |
| `ground` | Current return / reference |
| `power` | Power input/output (VIN, VOUT) |
| `reference` | Voltage reference, not driven |

## Characteristics

**Characteristics** are measurable properties of the part. Each characteristic has:

- **Direction** — Does the UUT provide or receive this?
- **Domain** — What physical quantity? (voltage, current, etc.)
- **Signal types** — DC, AC, pulsed?
- **Conditions** — Expected values and tolerances

### Direction Matters

The `direction` field describes the UUT's perspective:

| Direction | Meaning | Instrument Needs |
|-----------|---------|------------------|
| `input` | UUT receives power/signal | Instrument must **source** |
| `output` | UUT provides power/signal | Instrument must **measure** |
| `bidir` | UUT both receives and provides | Instrument must do both |

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
    bands:
      - value: 3.3
        accuracy:
          pct_reading: 5

  output_ripple:
    pins: [VOUT]           # Same pin, different measurement
    direction: output
    function: ac_voltage
    units: mV
    bands:
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
    bands:
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
    bands:
      - value: 5.0
        accuracy:
          pct_reading: 10
```

## Part Numbers

The `part_number` field maps a part to its manufacturing part number. When present, it automatically populates `uut_part_number` in test results (`uut_part_number` is the operator-facing identifier — the printed/scanned part number — as opposed to the internal `part_id`; see [how-to/traceability](../../how-to/execution/traceability.md)). Overridable via `--uut-part-number` on the CLI. This enables yield analytics filtering by part number.

```yaml
id: power_board
part_number: "DPB-001"
name: "5V to 3.3V Converter"
```

## Variant Inheritance

Part families can share specs using the `base` field. A variant inherits all fields from its base part and overrides specific sections:

```yaml
# parts/power_board_industrial.yaml
id: power_board_industrial
base: power_board              # Inherits from parts/power_board.yaml
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
    bands:
      - value: 3.3
        accuracy:
          pct_reading: 3          # Tighter tolerance for industrial
```

Inheritance rules:
- **Header fields** (`name`, `description`, `revision`, `part_number`, `datasheet`, `schematic`) — inherited when absent in variant
- **Sections** (`pins`, `characteristics`, `signal_groups`) — variant replaces entirely if present, otherwise inherited
- `id` and `base` always come from the variant
- Max inheritance depth: 5 levels. Circular references raise an error.

## Loading Parts

In Python:

```python
from litmus.store import load_part

part = load_part("parts/power_board.yaml")
print(part.id)
# Nominal lives on each SpecBand, not on the characteristic itself.
# Resolve the right band for the current operating point:
char = part.characteristics["output_voltage"]
band = char.get_spec_at({"temperature": 25, "load": 0.5})
if band is not None:
    print(band.value)
```

## Next Steps

- [Stations](stations.md) — Configuring test benches
- [Capabilities](capabilities.md) — Understanding capability matching
- [Configuration Reference](../../reference/configuration.md) — YAML schema details
