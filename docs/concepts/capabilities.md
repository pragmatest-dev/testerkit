# Capabilities

**Capabilities** describe what instruments can do and what products need. The capability system enables automatic matching between products and stations using an ATML/IEEE 1641-inspired signal-parameter model.

## What Is a Capability?

A capability has three core dimensions:

| Dimension | Examples | Description |
|-----------|---------|-------------|
| `function` | dc_voltage, ac_voltage, resistance, waveform, s_parameters, phase_noise, ... | Named measurement function |
| `direction` | input, output, bidir, transform | Does it measure, source, or transform? |
| `signals/conditions/controls/attributes` | voltage: {range: 0-1000V}, bandwidth: {value: 50MHz} | Named signal parameters organized by semantic role |

### Model Hierarchy

```
Capability (base)
├── InstrumentCapability    — adds channels, modes, readback
└── ProductCharacteristic   — adds pin/net, datasheet_ref, specs
```

Both share the same `function + direction + signals/conditions/controls/attributes` core. Direction always describes the hardware it's on: "input" means "this device receives/sinks signal."

### Example: DMM Capabilities

```yaml
# catalog/keysight_34461a.yaml or examples/instruments/dmm.yaml
capabilities:
  - function: dc_voltage
    direction: input      # Instrument measures (receives signal)
    signals:
      voltage:
        range: {min: 0.0001, max: 1000, units: V}
        accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
        resolution: {digits: 6.5}

  - function: dc_current
    direction: input
    signals:
      current:
        range: {min: 0.000001, max: 10, units: A}

  - function: resistance
    direction: input
    signals:
      resistance:
        range: {min: 0.01, max: 100000000, units: Ohm}
```

### Example: Power Supply Capabilities

```yaml
# catalog/keysight_e36312a.yaml or examples/instruments/psu.yaml

# Top-level structured channels describe physical topology
channels:
  "1": {terminals: [hi, lo], connector: binding_post, ground: floating}
  "2": {terminals: [hi, lo], connector: binding_post, ground: floating}

capabilities:
  - function: dc_voltage
    direction: output     # Instrument sources (provides signal)
    signals:
      voltage:
        range: {min: 0, max: 30, units: V}
      current:
        range: {min: 0, max: 5, units: A}
    channels: ["1", "2"]  # References to top-level channel keys

  - function: dc_voltage
    direction: input      # Built-in readback meter
    readback: true        # Excluded from auto-matching
    signals:
      voltage:
        range: {min: 0, max: 30, units: V}
    channels: ["1", "2"]
```

## Direction Pairing

The key insight is that **directions pair** between products and instruments:

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

Direction pairing happens in the matching service (`_directions_compatible()`), not in the models. Both sides store direction as-is — "input" always means "sink" regardless of whether it's on a product or instrument.

## Capability Matching

The matcher determines whether a station can test a product using tiered matching controlled by `MatchDepth`:

1. **Function match** — instrument has same `MeasurementFunction` as requirement
2. **Direction match** — directions pair correctly (OUTPUT↔INPUT, BIDIR satisfies both)
3. **Parameter range containment** — instrument's parameter ranges contain required values
4. **Accuracy** — instrument accuracy must be better than required (condition-aware via SpecBand)
5. **Resolution** — instrument resolution must meet or exceed required

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

# Matching wraps characteristics into CapabilityRequirement
# Direction stays as-is (OUTPUT) — pairing happens in capability_satisfies()

# Station instrument provides:
# function: dc_voltage, direction: INPUT, signals: {voltage: {range: 0-1000V}}
# → Function match ✓, Direction pair (OUTPUT↔INPUT) ✓, Range contains 3.3V ✓
# → MATCH!
```

## MeasurementFunction

The `MeasurementFunction` enum provides fine-grained signal identification, aligned with IVI instrument class specifications:

| Function | Description | Typical Instrument |
|----------|-------------|--------------------|
| **Basic Electrical** | | |
| `dc_voltage` | DC voltage measurement/sourcing | DMM, PSU |
| `ac_voltage` | AC voltage measurement | DMM |
| `dc_current` | DC current measurement/sourcing | DMM, PSU, SMU |
| `ac_current` | AC current measurement | DMM, clamp meter |
| `dc_power` | DC power measurement/calculation | SMU, derived |
| `ac_power` | AC power measurement | Power meter |
| `resistance` | 2-wire resistance | DMM |
| `resistance_4w` | 4-wire resistance | DMM |
| `capacitance` | Capacitance measurement | LCR meter, DMM |
| `inductance` | Inductance measurement | LCR meter |
| `impedance` | Impedance measurement | Impedance analyzer |
| **RLC Parameters** | | |
| `quality_factor` | Quality factor (Q) | LCR meter |
| `dissipation_factor` | Dissipation factor (D) | LCR meter |
| **Time/Frequency** | | |
| `frequency` | Frequency measurement | DMM, counter |
| `period` | Period measurement | Counter |
| `time_interval` | Time interval between events | Counter |
| `pulse_width` | Pulse width measurement | Counter, scope |
| `duty_cycle` | Duty cycle measurement | Counter, scope |
| `phase` | Phase measurement | Counter, scope |
| **Waveform** | | |
| `waveform` | Time-domain waveform capture/generation | Oscilloscope, FGen |
| **Edge Timing** | | |
| `rise_time` | Rise time measurement | Oscilloscope |
| `fall_time` | Fall time measurement | Oscilloscope |
| **RF Measurements** | | |
| `rf_power` | RF power measurement | Power meter |
| `rf_cw` | CW signal generation | RF signal generator |
| `s_parameters` | S-parameter measurement | VNA |
| `spectrum` | Frequency-domain analysis | Spectrum analyzer |
| `phase_noise` | Phase noise measurement | Signal/spectrum analyzer |
| `noise_figure` | Noise figure measurement | NF analyzer |
| `harmonics` | Harmonic distortion | Spectrum analyzer |
| **Digital/Logic** | | |
| `digital_pattern` | Digital pattern generation/capture | Logic analyzer, pattern gen |
| `digital_io` | Digital I/O, GPIO | Digital I/O card |
| `serial_data` | Serial protocol decode | Logic analyzer |
| **Signal Integrity** | | |
| `jitter` | Jitter measurement | Oscilloscope, TIA |
| `eye_diagram` | Eye diagram analysis | Oscilloscope |
| `power_quality` | Power quality analysis | Power analyzer |
| **Miscellaneous** | | |
| `temperature` | Temperature measurement | DMM (RTD/TC), chamber |
| `diode` | Diode test | DMM |
| `continuity` | Continuity test | DMM |
| `optical_power` | Optical power measurement | Optical power meter |
| `wavelength` | Wavelength measurement | Optical spectrum analyzer |
| `magnetic_field` | Magnetic field measurement | Gaussmeter |
| `position` | Position/displacement | Encoder, stage controller |

The `transform` direction is used for signal-path components (amplifiers, filters, mixers) that modify signals rather than measuring or sourcing them.

This replaces the old `Domain + SignalType` combination, providing much finer granularity. A DMM measuring `dc_voltage` is now distinct from an scope capturing `waveform` — they can no longer be confused.

### Waveform Shapes

For function generators and waveform sources, the `MeasurementFunction.WAVEFORM` is used with a `WaveformShape` parameter to specify supported shapes:

```yaml
# Function generator waveform shapes
- sine
- square
- triangle
- ramp
- pulse
- arbitrary
- noise
- dc
```

Per IEEE 1641, waveform shapes are **characteristics of the signal**, not distinct signal types. Function generators should have a single `function: waveform` capability rather than separate capabilities for each shape.

## Typed Collections

Each capability organizes parameters into four semantic categories:

| Collection | Purpose | Examples |
|-----------|---------|----------|
| `signals` | What's being measured/sourced | voltage, current, resistance — has range, accuracy, resolution |
| `conditions` | Operating conditions affecting accuracy | frequency, temperature, load — range only, used for SpecBand matching |
| `controls` | User knobs that can be configured | attenuation, filter_type, range_select — range or options |
| `attributes` | Fixed facts about the capability | bandwidth, max_frequency — fixed value, no comparison needed |

### Condition-Dependent Specs (SpecBand)

Instrument accuracy often varies with operating conditions. A DMM's AC voltage accuracy depends on the input frequency. A `SpecBand` captures this:

```yaml
signals:
  voltage:
    range: {min: 0.1, max: 750, units: V}
    accuracy: {pct_reading: 0.07, pct_range: 0.02}  # default
    specs:
      - when:
          frequency: {min: 3, max: 5, units: Hz}
        accuracy: {pct_reading: 0.35, pct_range: 0.03}
      - when:
          frequency: {min: 5, max: 300, units: Hz}
        accuracy: {pct_reading: 0.07, pct_range: 0.02}
      - when:
          frequency: {min: 300, max: 300000, units: Hz}
        accuracy: {pct_reading: 0.14, pct_range: 0.05}
conditions:
  frequency:
    range: {min: 3, max: 300000, units: Hz}
```

The `conditions` keys in SpecBand reference sibling condition names. Multiple keys are ANDed — all must match. When no band matches, the top-level accuracy/resolution applies as a default.

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
# examples/instruments/temp_logger.yaml
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
    signals:
      temperature:
        range: {min: -200, max: 850, units: "°C"}
    channels: ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
```

## Next Steps

- [Fixtures](fixtures.md) — Mapping DUT pins to instruments
- [Architecture](architecture.md) — System data flow
- [Adding Instruments](../guides/adding-instruments.md) — Creating custom drivers
