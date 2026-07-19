# Capabilities

**Capabilities** describe what instruments can do and what parts need. The capability system enables automatic matching between parts and stations using a signal-parameter model inspired by ATML (Automatic Test Markup Language) and IEEE 1641.

## The problem

A Keysight 34461A datasheet says it can measure "DC Voltage: 100 mV to 1000 V, 0.0035% + 0.0006% accuracy, 6.5-digit resolution." A part spec says "3.3 V output, ±5% tolerance." How do we connect these two worlds in a machine-readable way so the system can automatically determine whether a given instrument can test a given part?

We need a shared language that works for both sides. That language is `Capability`.

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
├── InstrumentCapability    — adds channels, readback
└── PartCharacteristic   — adds pin / pins / net / signal_group, datasheet_ref
```

Every part characteristic must name at least one physical interface (`pin`, `pins`, `net`, or `signal_group`) — TesterKit won't load one without it.

Both share the same `function + direction + signals/conditions/controls/attributes` core. Direction always describes the hardware it's on: "input" means "this device receives/sinks signal."

### Example: DMM Capabilities

```yaml
# catalog/keysight_34461a.yaml or instruments/dmm.yaml
capabilities:
  - function: dc_voltage
    direction: input      # Instrument measures (receives signal)
    signals:
      voltage:
        range: {min: 0.0001, max: 1000, unit: V}
        accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
        resolution: {digits: 6.5}

  - function: dc_current
    direction: input
    signals:
      current:
        range: {min: 0.000001, max: 10, unit: A}

  - function: resistance
    direction: input
    signals:
      resistance:
        range: {min: 0.01, max: 100000000, unit: Ohm}
```

### Example: Power Supply Capabilities

```yaml
# catalog/keysight_e36312a.yaml or instruments/psu.yaml

# Top-level structured channels describe physical topology
channels:
  "1": {terminals: [hi, lo], connector: binding_post, ground: floating}
  "2": {terminals: [hi, lo], connector: binding_post, ground: floating}

capabilities:
  - function: dc_voltage
    direction: output     # Instrument sources (provides signal)
    signals:
      voltage:
        range: {min: 0, max: 30, unit: V}
      current:
        range: {min: 0, max: 5, unit: A}
    channels: ["1", "2"]  # References to top-level channel keys

  - function: dc_voltage
    direction: input      # Built-in readback meter
    readback: true        # Marks a built-in readback meter
    signals:
      voltage:
        range: {min: 0, max: 30, unit: V}
    channels: ["1", "2"]
```

## Direction Pairing

The key insight is that **directions pair** between parts and instruments:

```
Part Characteristic          Required Instrument Capability
─────────────────────          ────────────────────────────────
output_voltage (OUTPUT)   →    dc_voltage (INPUT) — need to measure
input_voltage (INPUT)     →    dc_voltage (OUTPUT) — need to source
```

### Why This Works

When a part **outputs** voltage, the instrument needs to **input** (measure) that voltage.

When a part **inputs** power, the instrument needs to **output** (source) that power.

```
Part (UUT)                    Instrument
────────────                     ──────────

output_voltage ────signal───►    DMM (measures dc_voltage)
   (OUTPUT)                      (INPUT)

                  ◄───power────  PSU (sources dc_voltage)
input_voltage                    (OUTPUT)
   (INPUT)
```

You always write `direction` from the point of view of the hardware it sits on: `input` means that device receives signal, `output` means it sources signal — the same on a part or an instrument. The matcher pairs an OUTPUT on one side with an INPUT on the other.

## Capability Matching

The matcher determines whether a station can test a part by checking in tiers — you can stop at any tier, from function-only down through accuracy and resolution:

1. **Function match** — instrument has same `MeasurementFunction` as requirement
2. **Direction match** — directions pair correctly (part OUTPUT ↔ instrument INPUT, and vice versa). A `bidir` instrument satisfies any part direction; a `bidir` part needs a `bidir` instrument.
3. **Parameter range containment** — instrument's parameter ranges contain required values
4. **Accuracy** — instrument accuracy must be better than required (condition-aware via [`SpecBand`](../../reference/data/models.md), the value-plus-condition record)
5. **Resolution** — instrument resolution must meet or exceed required

```python
from testerkit.matching.service import find_compatible_stations
from testerkit.store import get_part

# Load by id (`get_part` looks up `parts/<id>.yaml` from the project root).
# Use `load_part(Path(...))` when you have an explicit path on disk.
part = get_part("power_board")
matches = find_compatible_stations(part)   # takes the loaded Part object

for match in matches:
    print(f"{match.station_id}: {'Compatible' if match.compatible else 'Missing capabilities'}")
```

### Matching Algorithm

```python
# Part characteristic
char = part.characteristics["output_voltage"]
# function: dc_voltage, direction: OUTPUT

# Matching wraps characteristics into CapabilityRequirement
# Direction stays as-is (OUTPUT) — the matcher pairs the part's OUTPUT with the DMM's INPUT

# Station instrument provides:
# function: dc_voltage, direction: INPUT, signals: {voltage: {range: 0-1000V}}
# → Function match ✓, Direction pair (OUTPUT↔INPUT) ✓, Range contains 3.3V ✓
# → MATCH!
```

## MeasurementFunction

The `MeasurementFunction` enum provides fine-grained signal identification, aligned with IVI (Interchangeable Virtual Instrument Foundation) instrument class specifications:

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

Each function names one measurement precisely — a DMM measuring `dc_voltage` and a scope capturing `waveform` are separate functions.

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

Per IEEE 1641 (the test-vocabulary standard underlying ATML), waveform shapes are **characteristics of the signal**, not distinct signal types. Function generators should have a single `function: waveform` capability rather than separate capabilities for each shape.

## Typed Collections

Each capability organizes parameters into four semantic categories:

| Collection | Purpose | Examples |
|-----------|---------|----------|
| `signals` | What's being measured/sourced | voltage, current, resistance — has range, accuracy, resolution |
| `conditions` | Operating conditions affecting accuracy | frequency, temperature, load — range only, used for SpecBand matching |
| `controls` | User knobs that can be configured | attenuation, filter_type, range_select — range or options |
| `attributes` | Fixed facts about the capability | bandwidth, max_frequency — fixed value, no comparison needed |

### Why four separate collections?

Each collection has a well-defined purpose:

- **Signals** participate in matching and have accuracy/range specs.
- **Conditions** appear in SpecBand `when:` constraints but don't participate directly in matching logic.
- **Controls** are user-configurable but don't affect the fundamental capability.
- **Attributes** are hardware facts that may participate in matching (e.g. scope bandwidth must exceed signal frequency), but are never measured — they're just limits.

A dimension name can't appear in more than one of `signals` / `conditions` / `controls` — TesterKit won't load a capability with that overlap. A name in `attributes` doesn't collide with the others.

### Condition-Dependent Specs (SpecBand)

Instrument accuracy often varies with operating conditions. A DMM's AC voltage accuracy depends on the input frequency. A `SpecBand` captures this:

```yaml
signals:
  voltage:
    range: {min: 0.1, max: 750, unit: V}
    accuracy: {pct_reading: 0.07, pct_range: 0.02}  # default
    bands:
      - when:
          frequency: {min: 3, max: 5, unit: Hz}
        accuracy: {pct_reading: 0.35, pct_range: 0.03}
      - when:
          frequency: {min: 5, max: 300, unit: Hz}
        accuracy: {pct_reading: 0.07, pct_range: 0.02}
      - when:
          frequency: {min: 300, max: 300000, unit: Hz}
        accuracy: {pct_reading: 0.14, pct_range: 0.05}
conditions:
  frequency:
    range: {min: 3, max: 300000, unit: Hz}
```

The `when:` keys reference the flat union of `signals`, `conditions`, and `controls` on the parent capability — any sibling dimension name is valid. Multiple keys are ANDed (all must match). When no band matches, the top-level accuracy/resolution applies as a default.

### Canonical condition keys

`ConditionKey` is a shared vocabulary for the `conditions` dict (27 canonical keys derived from auditing 150+ instrument datasheets). It's not enforced at the model level — you can use any string — but matching across catalog entries is more reliable when authors converge on these names:

| Category | Keys |
|---|---|
| Universal | `frequency`, `temperature`, `humidity`, `calibration_interval` |
| Measurement config | `nplc`, `auto_zero`, `coupling`, `impedance`, `sense_mode`, `sample_rate`, `bandwidth`, `filter`, `gate_time`, `acquisition_mode`, `time_constant` |
| Signal | `signal_level`, `crest_factor` |
| Source / load | `load`, `input_voltage`, `voltage`, `current`, `duty_cycle`, `slew_rate`, `settling_time` |
| Sensor | `sensor`, `wavelength` |
| RF | `offset` |

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
from testerkit.matching.service import find_compatible_stations, check_station_compatibility

# Find all compatible stations (takes the loaded Part object)
matches = find_compatible_stations(part)

# Check specific station — takes id strings, returns dict | None
result = check_station_compatibility(part_id, station_id)
if result and not result["compatible"]:
    for cap in result["missing"]:
        print(f"Missing: {cap['direction']} {cap['function']}")
```

`find_compatible_stations(part)` takes a loaded `Part` object and returns a `list[StationMatch]`. `check_station_compatibility(part_id, station_id)` takes id strings and returns a `dict | None`; its `missing` value is a list of dicts shaped `{characteristic, function, direction}`.

### HTTP API

```bash
# Find all compatible stations
curl "http://localhost:8000/api/match?part_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?part_id=power_board&station_id=bench_1"
```

## Where this model comes from

The capability model draws on established test standards: signal-oriented thinking from IEEE 1641 (capabilities describe signals, not instruments; waveform shapes are parameters, not types), the comparator types and direction-pairing concept from ATML / IEEE 1671, and function names from the IVI Foundation instrument classes (DMM, Scope, FGen, DCPwr, RFSigGen). Parts and instruments use the same model, so matching pairs them directly — no translation step.

## Custom Instruments

When adding custom instruments, define their capabilities using the function-parameter model:

```yaml
# instruments/temp_logger.yaml
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
        range: {min: -200, max: 850, unit: "°C"}
    channels: ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
```

## The full picture

```
Instrument catalog — what an instrument CAN do
──────────────────────────────────────────────
catalog_entry:
  id: keysight_34461a
  type: dmm
  channels:
    "1": {terminals: [hi, lo], connector: binding_post, ground: shared}

  capabilities:
    - function: dc_voltage        ─┐
      direction: input             │  Capability
      signals:                     │  (shared base class)
        voltage:                   │
          range: {min: 0, max: 1000, unit: V}
          accuracy: {pct_reading: 0.0035}
          resolution: {digits: 6.5}

Part spec — what the UUT NEEDS tested
────────────────────────────────────────
id: power_board_v1
pins:
  VOUT: {name: "J1.3", net: "VOUT_3V3", role: signal}

characteristics:
  rail_3v3:
    function: dc_voltage          ─┐
    direction: output              │  PartCharacteristic
    pin: VOUT                      │  (extends Capability)
    signals:                       │
      voltage:                     │
        value: 3.3                 │
        unit: V                    │
    bands:                         │
      - when:                      │
          load: {min: 0, max: 1}   │
        accuracy: {pct_reading: 5.0}

Matching: dc_voltage OUTPUT ↔ dc_voltage INPUT
          3.3 V within 0–1000 V range
          0.0035% << 5% (instrument is more accurate)
          MATCH
```

## Next Steps

- [Fixtures](fixtures.md) — Mapping UUT pins to instruments
- [Architecture](../overview/architecture.md) — System data flow
- [Custom drivers](../../how-to/configuration/custom-drivers.md) — Creating custom drivers
