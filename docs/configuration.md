# Configuration Reference

Litmus uses YAML files for configuration, validated by Pydantic models.

## Product Specification

**Location:** `products/<product_id>/spec.yaml`

```yaml
product:
  id: string              # Unique identifier
  name: string            # Display name
  description: string     # Optional
  revision: string        # Version/revision
  datasheet: string       # Optional path/URL
  schematic: string       # Optional path/URL

pins:                     # Physical connection points
  <key>:                  # Pin reference name (used in characteristics)
    name: string          # Physical designator (e.g., "J1.1", "TP5")
    net: string           # Schematic net name (optional)
    type: signal | power | ground | nc
    description: string   # Optional

signal_groups:            # Bus interfaces
  <key>:
    protocol: string      # i2c, spi, uart, parallel, custom
    signals:
      - pin: string       # Reference to pins.<key>
        role: string      # data, clock, chip_select, etc.
        index: integer    # For multi-bit (DATA[0], DATA[1])
    parameters:           # Protocol-specific parameters
      frequency: integer
      <param>: value
    description: string

characteristics:
  <name>:                 # Characteristic identifier
    direction: input | output | bidir
    domain: voltage | current | resistance | frequency | time | digital
    signal_types: [dc, ac, pulse, sine, square, pwm]
    units: string         # e.g., "V", "A", "ohm"
    pins: [string]        # References to pins.<key>
    channel: string       # For multi-channel DUT outputs (optional)
    signal_group: string  # Reference to signal_groups.<key> (optional)
    datasheet_ref: string # Optional reference
    schematic_ref: string # Optional reference
    conditions:
      - nominal: float  # Expected value
        tolerance_pct: float    # Percentage tolerance
        tolerance_abs: float    # Or absolute tolerance
        limit_low: float        # Or explicit limits
        limit_high: float
        comparator: GELE | EQ | NE | LT | LE | GT | GE | GELT | GTLE | GTLT
        # Additional condition parameters (e.g., temperature, load)
        <param>: value

test_requirements:
  <name>:
    characteristic_ref: string   # Reference to characteristic
    conditions: dict            # Which conditions to test
    guardband_pct: float      # Tighten limits by this percentage (default 0)
    priority: integer           # Test order priority
    description: string
```

### Pin Types

| Type | Description |
|------|-------------|
| `signal` | General signal pin (default) |
| `power` | Power supply pin |
| `ground` | Ground reference |
| `nc` | No connect / reserved |

### Comparator Reference

| Comparator | Meaning | Pass Condition |
|------------|---------|----------------|
| `EQ` | Equals | value == nominal |
| `NE` | Not equals | value != nominal |
| `LT` | Less than | value < high |
| `LE` | Less or equal | value <= high |
| `GT` | Greater than | value > low |
| `GE` | Greater or equal | value >= low |
| `GELE` | In range (inclusive) | low <= value <= high |
| `GELT` | In range (high exclusive) | low <= value < high |
| `GTLE` | In range (low exclusive) | low < value <= high |
| `GTLT` | In range (exclusive) | low < value < high |

## Station Configuration

**Location:** `stations/<station_id>.yaml`

```yaml
station:
  id: string              # Unique identifier
  name: string            # Display name
  location: string        # Physical location
  description: string

instruments:
  <name>:                 # Instrument alias (used in tests)
    type: string          # Instrument type (dmm, scope, psu, eload, etc.)
    resource: string      # VISA address
    mock_config:          # Values for --mock-instruments mode
      voltage: float
      current: float
      resistance: float

supported_phases:         # Optional: which test phases this station supports
  - validation
  - production
  - debug
```

### Common Instrument Types

| Type | Description | Capabilities |
|------|-------------|--------------|
| `dmm` | Digital Multimeter | voltage, current, resistance |
| `scope` | Oscilloscope | voltage (AC), frequency, time |
| `power_supply` | DC Power Supply | voltage output, current output |
| `eload` | Electronic Load | current sink |
| `funcgen` | Function Generator | waveform output |

## Fixture Configuration

**Location:** `fixtures/<fixture_id>.yaml`

Fixtures define pin-to-instrument mappings, bridging product pins to station instruments.

```yaml
fixture:
  id: string              # Unique identifier
  name: string            # Display name
  product_id: string      # Specific product (preferred)
  product_family: string  # Or product family for shared fixtures
  product_revision: string # Optional: specific revision

points:
  <name>:                 # Fixture point name
    dut_pin: string       # Product pin reference
    net: string           # Or schematic net name
    instrument: string    # Station instrument name
    instrument_channel: string  # Channel on instrument
```

### Example

```yaml
# fixtures/power_board_fixture.yaml
fixture:
  id: power_board_fixture
  name: "Power Board Test Fixture"
  product_id: power_board

points:
  VIN:
    dut_pin: VIN
    net: VIN_5V
    instrument: psu
    instrument_channel: "1"
  VOUT:
    dut_pin: VOUT
    net: VOUT_3V3
    instrument: dmm
```

### When to Use Fixtures

| Scenario | Use Fixture? |
|----------|--------------|
| Simple bench, one product | No — use direct instrument fixtures |
| Multiple products on same bench | Yes — map each product's pins |
| Production test with compliance needs | Yes — provides traceability |
| Development/CI | No — use Mock(DMM), Mock(PSU) |

## Test Configuration

**Location:** `tests/config.yaml` (in same directory as tests)

```yaml
<test_function_name>:
  vectors:                # Parameter combinations
    expand: product | zip | range | nested
    <param>: [values]     # For product/zip expansion
    loops:                # For nested expansion
      - name: string
        values: [...]

  limits:
    <measurement_name>:
      low: float
      high: float
      nominal: float
      units: string
      spec_ref: string
      comparator: string  # Default: GELE

  retry:
    max_attempts: integer # Default: 1
    delay_seconds: float  # Delay between retries

  _mock:                  # Mock instrument values (for --mock-instruments)
    <instrument>.<method>: value  # Test-level constant (e.g., dmm.measure_voltage)
```

### Per-Vector Mock Configuration

For simulation mode, you can configure mock instrument return values:

```yaml
test_load_sweep:
  vectors:
    - load: 0.1
      _mock:                    # Per-vector mock values
        dmm.measure_voltage: 3.32
        psu.measure_current: 0.12
    - load: 0.5
      _mock:
        dmm.measure_voltage: 3.30
        psu.measure_current: 0.52
  _mock:                        # Test-level fallback
    eload.measure_voltage: 3.3
  limits:
    test_load_sweep:
      low: 3.2
      high: 3.4
      units: V
```

Mock value resolution order:
1. Vector-level `_mock`
2. Test-level `_mock`
3. Limit `nominal` value
4. Zero (default)

### Vector Expansion Modes

**product** — Cartesian product of all parameters:
```yaml
vectors:
  expand: product
  voltage: [3.3, 5.0, 12.0]
  temperature: [25, 85]
# Creates 6 vectors: (3.3, 25), (3.3, 85), (5.0, 25), ...
```

**zip** — Parallel iteration:
```yaml
vectors:
  expand: zip
  voltage: [3.3, 5.0, 12.0]
  current: [0.1, 0.5, 1.0]
# Creates 3 vectors: (3.3, 0.1), (5.0, 0.5), (12.0, 1.0)
```

**range** — Numeric range:
```yaml
vectors:
  expand: range
  voltage:
    start: 3.0
    stop: 5.0
    step: 0.5
# Creates: 3.0, 3.5, 4.0, 4.5, 5.0
```

**nested** — Nested loops with change detection:
```yaml
vectors:
  expand: nested
  loops:
    - name: temperature   # Outer loop (changes less frequently)
      values: [25, 85]
    - name: load          # Inner loop
      values: [0, 50, 100]
# Creates 6 vectors, temperature changes every 3 iterations
```

## Instrument Library

**Location:** `litmus/instruments/library/<type>.yaml`

```yaml
name: string              # Display name
type: string              # Type identifier
manufacturer: string
models:                   # Supported models
  - pattern: string       # Regex pattern for *IDN? response
    name: string

capabilities:
  - name: string
    direction: input | output | bidir
    domain: voltage | current | resistance | frequency | time | digital
    signal_types: [dc, ac, ...]
    channels:
      count: integer      # Number of channels
      simultaneous: boolean   # Can measure all channels at once
      naming: string      # Pattern: "CH{n}", "ai{n}"
      labels: [string]    # Explicit: ["CH1", "CH2", "CH3", "CH4"]
    range:
      min: float
      max: float
      units: string
    resolution: float
    accuracy_pct: float
```

### Channel Naming

Instruments with multiple channels can define naming patterns:

```yaml
capabilities:
  - name: voltage_dc
    direction: input
    domain: voltage
    channels:
      count: 4
      naming: "CH{n}"     # Generates: CH1, CH2, CH3, CH4
```

Or explicit labels:

```yaml
channels:
  count: 2
  labels: ["HI", "LO"]
```

## Environment Variables

Configuration values can reference environment variables:

```yaml
instruments:
  dmm:
    resource: "${DMM_VISA_ADDRESS}"
```

## Pydantic Models

All configuration is validated by Pydantic models in `litmus/config/models.py` and `litmus/products/models.py`:

```python
from litmus.products.models import Product
from litmus.products.loader import load_product

# Load and validate
product = load_product("products/my_product/spec.yaml")
print(product.id)
print(product.characteristics["output_voltage"].nominal)
```

## Next Steps

- [pytest Plugin Guide](pytest-plugin.md) — Using configuration in tests
- [Core Concepts](concepts.md) — Understanding the data model
