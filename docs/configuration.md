# Configuration Reference

Litmus uses YAML files for configuration, validated by Pydantic models.

## Product Specification

**Location:** `specs/<product_id>.yaml`

```yaml
product:
  id: string              # Unique identifier
  name: string            # Display name
  description: string     # Optional
  revision: string        # Version/revision
  datasheet: string       # Optional path/URL
  schematic: string       # Optional path/URL

characteristics:
  <name>:                 # Characteristic identifier
    direction: input | output
    domain: voltage | current | resistance | frequency | time | digital
    signal_types: [dc, ac, pulse, sine, square, pwm]
    units: string         # e.g., "V", "A", "ohm"
    datasheet_ref: string # Optional reference
    schematic_ref: string # Optional reference
    conditions:
      - nominal: decimal  # Expected value
        tolerance_pct: decimal    # Percentage tolerance
        tolerance_abs: decimal    # Or absolute tolerance
        limit_low: decimal        # Or explicit limits
        limit_high: decimal
        comparator: GELE | EQ | NE | LT | LE | GT | GE | GELT | GTLE | GTLT
        # Additional condition parameters (e.g., temperature, load)
        <param>: value

test_requirements:
  <name>:
    characteristic_ref: string   # Reference to characteristic
    conditions: dict            # Which conditions to test
    guardband_pct: decimal      # Tighten limits by this percentage (default 0)
    priority: integer           # Test order priority
    description: string
```

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
    type: string          # Instrument type (dmm, scope, power_supply, etc.)
    resource: string      # VISA address or "SIM::<name>"
    simulated: boolean    # Use simulated driver (default false)
    sim_values:           # Values for simulation
      voltage: decimal
      current: decimal
      resistance: decimal

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
      low: decimal
      high: decimal
      nominal: decimal
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

capabilities:
  - name: string
    direction: input | output | bidir
    domain: voltage | current | resistance | frequency | time | digital
    signal_types: [dc, ac, ...]
    range:
      min: decimal
      max: decimal
      units: string
    resolution: decimal
    accuracy_pct: decimal
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
product = load_product("specs/my_product.yaml")
print(product.id)
print(product.characteristics["output_voltage"].nominal)
```

## Next Steps

- [pytest Plugin Guide](pytest-plugin.md) — Using configuration in tests
- [Core Concepts](concepts.md) — Understanding the data model
