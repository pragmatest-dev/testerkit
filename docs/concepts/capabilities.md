# Capabilities

**Capabilities** describe what instruments can do and what products need. The capability system enables automatic matching between products and stations using an ATML/IEEE 1641-inspired signal-parameter model.

## What Is a Capability?

A capability (`FunctionCapability`) has three core dimensions:

| Dimension | Examples | Description |
|-----------|---------|-------------|
| `function` | dc_voltage, ac_voltage, resistance, waveform | Named measurement function |
| `direction` | input, output, bidir | Does it measure or source? |
| `parameters` | voltage: {range: 0-1000V}, bandwidth: {value: 50MHz} | Named signal parameters with range/accuracy/resolution |

### Example: DMM Capabilities

```yaml
# catalog/keysight_34461a.yaml or demo/instruments/dmm.yaml
capabilities:
  - function: dc_voltage
    direction: input      # Instrument measures (receives signal)
    parameters:
      voltage:
        range: {min: 0.0001, max: 1000, units: V}
        accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
        resolution: {digits: 6.5}

  - function: dc_current
    direction: input
    parameters:
      current:
        range: {min: 0.000001, max: 10, units: A}

  - function: resistance
    direction: input
    parameters:
      resistance:
        range: {min: 0.01, max: 100000000, units: Ohm}
```

### Example: Power Supply Capabilities

```yaml
# catalog/keysight_e36312a.yaml or demo/instruments/power_supply.yaml

# Top-level structured channels describe physical topology
channels:
  "1": {terminals: [hi, lo], connector: binding_post, ground: floating}
  "2": {terminals: [hi, lo], connector: binding_post, ground: floating}

capabilities:
  - function: dc_voltage
    direction: output     # Instrument sources (provides signal)
    parameters:
      voltage:
        range: {min: 0, max: 30, units: V}
      current:
        range: {min: 0, max: 5, units: A}
    channels: ["1", "2"]  # References to top-level channel keys

  - function: dc_voltage
    direction: input      # Built-in readback meter
    readback: true        # Excluded from auto-matching
    parameters:
      voltage:
        range: {min: 0, max: 30, units: V}
    channels: ["1", "2"]
```

## Direction Flip

The key insight is that **directions flip** between products and instruments:

```
Product Characteristic          Required Instrument Capability
─────────────────────          ────────────────────────────────
output_voltage (OUTPUT)   →    dc_voltage (INPUT) — need to measure
input_voltage (INPUT)     →    dc_voltage (OUTPUT) — need to source
```

### Why This Works

When a product **outputs** voltage, the instrument needs to **input** (measure) that voltage.

When a product **inputs** power, the instrument needs to **output** (source) that power.

```
Product (DUT)                    Instrument
────────────                     ──────────

output_voltage ────signal───►    DMM (measures dc_voltage)
   (OUTPUT)                      (INPUT)

                  ◄───power────  PSU (sources dc_voltage)
input_voltage                    (OUTPUT)
   (INPUT)
```

## Capability Matching

The matcher determines whether a station can test a product using 3-tier matching:

1. **Function match** — instrument has same `MeasurementFunction` as requirement
2. **Direction match** — directions pair correctly (OUTPUT↔INPUT, BIDIR satisfies both)
3. **Parameter range containment** — instrument's parameter ranges contain required values

```python
from litmus.matching.service import find_compatible_stations, load_product_by_id

product = load_product_by_id("power_board")
matches = find_compatible_stations(product)

for match in matches:
    print(f"{match.station_id}: {'Compatible' if match.compatible else 'Missing capabilities'}")
```

### Matching Algorithm

```python
# Product characteristic
char = product.characteristics["output_voltage"]
# function: dc_voltage, direction: OUTPUT

# Convert to requirement
req = char.to_capability_requirement()
# function: dc_voltage, direction: INPUT (direction flipped!)
# parameters: {voltage: {value: 3.3, units: V}}

# Check station — DMM provides:
# function: dc_voltage, direction: INPUT, parameters: {voltage: {range: 0-1000V}}
# → Function match ✓, Direction match ✓, Range contains 3.3V ✓
# → MATCH!
```

## MeasurementFunction

The `MeasurementFunction` enum provides fine-grained signal identification, aligned with IVI instrument class specifications:

| Function | Description | Typical Instrument |
|----------|-------------|--------------------|
| `dc_voltage` | DC voltage measurement/sourcing | DMM, PSU |
| `ac_voltage` | AC voltage measurement | DMM |
| `dc_current` | DC current measurement/sourcing | DMM, PSU, SMU |
| `ac_current` | AC current measurement | DMM, clamp meter |
| `resistance` | 2-wire resistance | DMM |
| `resistance_4w` | 4-wire resistance | DMM |
| `frequency` | Frequency measurement | DMM, counter |
| `waveform` | Time-domain waveform capture | Oscilloscope |
| `dc_power` | DC power measurement/calculation | SMU, derived |
| `temperature` | Temperature measurement | DMM (RTD/TC) |

This replaces the old `Domain + SignalType` combination, providing much finer granularity. A DMM measuring `dc_voltage` is now distinct from an oscilloscope capturing `waveform` — they can no longer be confused.

## SignalParameter

Each capability has named parameters with optional range, accuracy, and resolution:

```yaml
parameters:
  voltage:
    range: {min: 0.001, max: 1000, units: V}
    accuracy: {pct_reading: 0.005, offset: 0.001}
    resolution: {digits: 6.5}
  bandwidth:
    value: 300000        # Fixed value (capability parameter)
    units: Hz
    role: capability
```

### Parameter Roles

| Role | Description |
|------|-------------|
| `controllable` | Can be set by the user (default) |
| `measurable` | Can be read/measured |
| `capability` | Performance limit (e.g., bandwidth) |
| `condition` | Operating condition (e.g., temperature) |

## Channel Specification

Channels describe the physical topology of each instrument channel:

```yaml
# Structured channel topology (on catalog/instrument library):
channels:
  "1":
    label: "6V/5A Output"
    terminals: [hi, lo, sense_hi, sense_lo]  # Physical terminals
    connector: binding_post                   # Connector type
    ground: floating                          # Isolated from other channels
  "2":
    terminals: [hi, lo]
    connector: binding_post
    ground: floating

# On capabilities, channels is a plain list of keys:
capabilities:
  - function: dc_voltage
    direction: output
    channels: ["1", "2"]  # Which channels support this capability
```

## 3-Tier Instrument Catalog

Capability data lives at three levels:

```
catalog/keysight_34461a.yaml       ← Universal: "what can this MODEL do"
instruments/dmm_bench_001.yaml     ← Unit-specific: serial, calibration, catalog_ref
stations/bench_01.yaml             ← Project-local: role, driver, resource, catalog_ref
```

When a station instrument has `catalog_ref: keysight_34461a`, the matching engine resolves capabilities from the catalog entry — providing detailed range/accuracy data without requiring each station config to repeat it.

## Using the Matcher

### Python API

```python
from litmus.matching.service import find_compatible_stations, check_station_compatibility

# Find all compatible stations
matches = find_compatible_stations(product)

# Check specific station
result = check_station_compatibility(product, station)
if not result.compatible:
    print(f"Missing: {result.missing_capabilities}")
```

### HTTP API

```bash
# Find all compatible stations
curl "http://localhost:8000/api/match?product_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?product_id=power_board&station_id=bench_1"
```

## Custom Instruments

When adding custom instruments, define their capabilities using the function-parameter model:

```yaml
# demo/instruments/temp_logger.yaml
instrument:
  type: temp_logger
  name: Custom Temperature Logger

channels:
  "T1": {terminals: [signal], connector: terminal_block, ground: shared}
  "T2": {terminals: [signal], connector: terminal_block, ground: shared}
  "T3": {terminals: [signal], connector: terminal_block, ground: shared}
  "T4": {terminals: [signal], connector: terminal_block, ground: shared}
  "T5": {terminals: [signal], connector: terminal_block, ground: shared}
  "T6": {terminals: [signal], connector: terminal_block, ground: shared}
  "T7": {terminals: [signal], connector: terminal_block, ground: shared}
  "T8": {terminals: [signal], connector: terminal_block, ground: shared}

capabilities:
  - function: temperature
    direction: input
    parameters:
      temperature:
        range: {min: -200, max: 850, units: "°C"}
    channels: ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
```

## Next Steps

- [Fixtures](fixtures.md) — Mapping DUT pins to instruments
- [Architecture](architecture.md) — System data flow
- [Adding Instruments](../guides/adding-instruments.md) — Creating custom drivers
