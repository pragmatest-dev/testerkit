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
    role: signal | power | ground | reference   # Pin role (default: signal)
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
    function: dc_voltage | ac_voltage | dc_current | ac_current | resistance | waveform | ...
    direction: input | output | bidir
    units: string         # e.g., "V", "A", "ohm"

    # Physical interface (at least one required)
    pin: string           # Single pin reference (Product.pins key)
    pins: [string] | string  # Multiple pins (list or range: "GPIO[0:7]")
    net: string           # Schematic net name
    signal_group: string  # Reference to signal_groups.<key>

    # For multi-channel DUT outputs
    channel: string       # Single channel
    channels: [string] | string  # Multiple channels (list or range: "CH[1:4]")

    # Traceability
    datasheet_ref: string # Optional reference
    schematic_ref: string # Deprecated: use net instead

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

| Role | Description |
|------|-------------|
| `signal` | General signal pin (default) |
| `power` | Power supply pin |
| `ground` | Ground reference |
| `reference` | Voltage reference (not driven) |

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
  <name>:                 # Instrument alias / role name (used in tests as fixture)
    type: string          # Instrument type (psu, dmm, eload, scope, smu)
    driver: string        # Python import path to driver class
    resource: string      # VISA address
    catalog_ref: string   # Optional: catalog entry ID for capability/topology resolution
    channels: [string]    # Optional: channel keys (resolved from catalog if omitted)
    mock: boolean         # If true, uses Mock with mock_config values
    mock_config:          # Values for --mock-instruments mode
      voltage: float
      current: float
      resistance: float

supported_phases:         # Optional: which test phases this station supports
  - validation
  - production
  - debug
```

## Test Sequence

**Location:** `sequences/<sequence_id>.yaml`

```yaml
sequence:
  id: string
  name: string
  description: string

steps:
  - id: string
    test: string              # Pytest test path
    description: string
    aliases:                  # Optional: remap fixture names to station roles
      <fixture_name>: <station_role>
    skip_on: [string]
    retry:
      max_attempts: integer
      delay_seconds: float
    limit_ref: string
```

### Common Instrument Types

| Type | Description | Capabilities |
|------|-------------|--------------|
| `dmm` | Digital Multimeter | voltage, current, resistance |
| `scope` | Oscilloscope | voltage (AC), frequency, time |
| `psu` | DC Power Supply | voltage output, current output |
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
    instrument_terminal: string # Terminal on channel (hi, lo, signal, etc.)
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
```

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

channels:                   # Structured channel topology
  "1":
    terminals: [hi, lo]     # Physical terminals (hi, lo, signal, sense_hi, etc.)
    connector: binding_post # Connector type
    ground: floating        # Ground topology (floating, shared, earth)

capabilities:
  - function: dc_voltage    # MeasurementFunction enum
    direction: input        # input (measure) or output (source)
    readback: false         # true for built-in meters
    channels: ["1"]         # Which channels support this capability
    parameters:
      voltage:
        range: {min: 0, max: 1000, units: V}
        accuracy: {pct_reading: 0.005, pct_range: 0.001}
        resolution: {digits: 6.5}
```

### Channel Topology

Channels describe the physical interface of each instrument channel:

```yaml
channels:
  "CH1":
    label: "Input 1"                  # Optional display name
    terminals: [signal]               # Physical terminal types
    connector: bnc                    # Connector type
    ground: shared                    # Ground topology
```

Terminal types: `hi`, `lo`, `sense_hi`, `sense_lo`, `guard`, `signal`, `trigger`
Connector types: `binding_post`, `banana`, `bnc`, `terminal_block`, `probe`, `triax`, `sma`, `smb`, `spring`, `pxi`, `screw_terminal`
Ground topology: `floating` (isolated), `shared` (common ground), `earth` (referenced to earth)

### Catalog Variant Inheritance (`base`)

Catalog entries can inherit from a base entry using the `base` field to avoid YAML duplication. The variant only needs to specify what differs from the base.

```yaml
# catalog/keysight_34465a.yaml — inherits from 34461A, overrides capabilities
catalog_entry:
  id: keysight_34465a
  model: "34465A"
  name: "Keysight 34465A Digital Multimeter"
  base: keysight_34461a    # Inherits manufacturer, type, channels

capabilities:              # Replaces base capabilities entirely
  - function: dc_voltage
    direction: input
    parameters:
      voltage:
        range: {min: 0.0001, max: 1000, units: V}
        accuracy: {pct_reading: 0.0015, pct_range: 0.0003}
        resolution: {digits: 6.5}
```

**Merge rules** (section-level override, not deep merge):

| Section | Variant provides it | Variant omits it |
|---------|-------------------|------------------|
| `capabilities:` | Replaces base entirely | Inherits from base |
| `channels:` | Replaces base entirely | Inherits from base |
| `manufacturer` | Uses variant's | Inherits from base |
| `type` | Uses variant's | Inherits from base |
| `id`, `model`, `name` | Always from variant | — |

Chains are supported (A → B → C) up to depth 5. Circular references raise `ValueError`.

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
