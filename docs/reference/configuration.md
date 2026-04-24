# Configuration Reference

Litmus uses YAML files for configuration, validated by Pydantic models.

## Product Specification

**Location:** `products/<product_id>.yaml`

```yaml
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

    specs:
      - value: float            # Nominal/expected value
        accuracy:               # Accuracy specification
          pct_reading: float    # Percentage of reading
          pct_range: float      # Percentage of range
          absolute: float       # Absolute accuracy
        conditions:             # Operating conditions (optional)
          temperature: {min: float, max: float}
          load: {min: float, max: float}
          <param>: value
        comparator: GELE | EQ | NE | LT | LE | GT | GE | GELT | GTLE | GTLT

specs:
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
id: string
name: string
description: string
test_phase: development | validation | characterization | production  # Required

steps:
  - id: string
    test: string              # Pytest test path
    description: string
    aliases:                  # Optional: remap fixture names to station roles
      <fixture_name>: <station_role>
    skip_on: [string]

    # Test config (overrides inline decorator config)
    vectors:                  # Parameter combinations (same syntax as inline)
      expand: product | zip
      <param>: [values]
    limits:                   # Measurement limits
      <measurement_name>:
        low: float
        high: float
        nominal: float
        units: string
        comparator: string
        spec_ref: string
    mocks:                    # Mock instrument return values
      <instrument.method>: value
    retry:
      max_attempts: integer
      delay_seconds: float
      strategy: string
    limit_ref: string         # Derive limits from spec
```

Per-vector mocks use `_mocks` inside vector dicts:

```yaml
vectors:
  - vin: 5.0
    _mocks:
      dmm.measure_dc_voltage: 3.31
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

Test config (vectors, limits, mocks) is resolved in priority order:

1. **Sequence steps** (primary) — When running with `--sequence`
2. **Profile overrides** — From `litmus.yaml` profile when `--litmus-profile` is set
3. **pytest markers** — `@pytest.mark.litmus_limits`, `@pytest.mark.parametrize`
4. **Sidecar YAML** — `test_<module>.yaml` next to the test file

Sequence step config **replaces** lower-priority sources for the keys it sets (not merged).

### Inline Marker Config

```python
import pytest


@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "units": "V"})
def test_example(vin, dmm, logger):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

### Sidecar YAML Config

```yaml
# test_example.yaml — same directory as the test module
vectors:
  vin: [4.5, 5.0, 5.5]

limits:
  output_voltage: {low: 3.135, high: 3.465, units: "V"}

mocks:
  dmm.measure_dc_voltage: 3.31
```

### Retries

For retries, use ecosystem-standard markers instead of inline config:

```python
import pytest


@pytest.mark.flaky(reruns=3, reruns_delay=0.5)  # pytest-rerunfailures
def test_flaky(dmm, logger):
    logger.measure("voltage", dmm.measure_dc_voltage())
```

Sequence steps can still specify `retry:` — see the sequence reference above.

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

**Range strings** — Compact numeric sweeps (anywhere a list is expected):
```yaml
vectors:
  expand: product
  voltage: "3.0:5.0:0.5"
# Creates: 3.0, 3.5, 4.0, 4.5, 5.0
```

**Recursive sub-blocks** — Compose product and zip:
```yaml
vectors:
  expand: product
  temperature: [25, 85]          # Outer loop (changes slowly)
  vectors:
    expand: zip
    voltage: [3.3, 5.0, 12.0]
    expected: [3.2, 4.9, 11.8]  # Paired with voltage
# Creates 6 vectors: 2 temps x 3 zipped pairs
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

## Project Configuration

**Location:** `litmus.yaml` (project root)

```yaml
name: string                  # Required — project identifier
results_dir: string           # Optional — override default results directory
default_station: string       # Default station for sessions (default: "station")
default_fixture: string       # Optional default fixture
mock_instruments: bool        # Force mock mode for all instruments (default: false)

outputs:                      # Optional list of format + transport targets
  - format: html              # Exporter (html, pdf, csv, stdf, ...)
    transport: s3             # Shipper (s3, snowflake, ...)
    bucket: my-results        # Format/transport-specific extras pass through

profiles:                     # Named config sets — see docs/guides/profiles.md
  <profile_name>:
    description: string       # Optional human-readable description

    pytest:                   # Pytest-level knobs applied to the session
      addopts: string         # Appended to PYTEST_ADDOPTS before collection
      markexpr: string        # Like -m: "not slow and not hardware"
      keyword: string         # Like -k: "rails"

    vectors:                  # Override vectors for matched node-ids
      "test_file.py::TestClass::test_method":
        vin: [5.0]            # Replaces sidecar vectors for this node-id
      "test_file.py::TestClass::*":  # fnmatch glob also supported
        temperature: [25]

    limits:                   # Override limits for matched node-ids
      "test_file.py::TestClass::test_method":
        output_voltage: {low: 3.25, high: 3.35, units: V}

    markers:                  # Inject pytest markers onto matched node-ids
      "test_file.py::TestSlow":
        - skip: "not run in validation"
      "test_file.py::TestFlaky":
        - flaky: {reruns: 2, reruns_delay: 1}
```

**Node-ID keys** follow pytest's own format (`path::Class::method`, `path::func`)
and support `fnmatch` globs like `TestClass::*`. Exact matches take precedence
over globs for vectors and limits; markers accumulate across every matching pattern.

**Selection:** `pytest --litmus-profile=<name>` or `LITMUS_PROFILE=<name> pytest`.
Only one profile is active per session.

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
product = load_product("products/my_product.yaml")
print(product.id)
print(product.characteristics["output_voltage"].nominal)
```

## Next Steps

- [pytest-native Reference](pytest-native.md) — Fixtures, markers, sidecar YAML
- [pytest Plugin Guide](pytest-plugin.md) — Plugin CLI flags and fixture reference
- [Core Concepts](concepts.md) — Understanding the data model
