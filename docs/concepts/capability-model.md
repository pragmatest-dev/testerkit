# The Capability Model

How Litmus describes what instruments can do and what products need tested.

## The Problem

A Keysight 34461A datasheet says it can measure "DC Voltage: 100mV to 1000V, 0.0035% + 0.0006% accuracy, 6.5-digit resolution." A product spec says "3.3V output, +/-5% tolerance." How do we connect these two worlds in a machine-readable way so the system can automatically determine whether a given instrument can test a given product?

We need a shared language that works for both sides.

## The Shared Language: Capability

Both instruments and products describe their electrical behavior using the same five-part structure:

```
Capability = Function + Direction + Signals + Conditions + Controls + Attributes
```

| Part | What it answers | Examples |
|------|----------------|----------|
| **Function** | What kind of signal? | `dc_voltage`, `ac_current`, `resistance`, `waveform`, `temperature` |
| **Direction** | Which way does signal flow? | `input` (measure/sink), `output` (source/drive), `bidir` (both) |
| **Signals** | What's being measured/sourced? | voltage range, accuracy, resolution |
| **Conditions** | Operating conditions affecting accuracy | frequency, temperature, load |
| **Controls** | User-settable parameters | current limit, heater power, sweep rate |
| **Attributes** | Fixed hardware facts | bandwidth, sample rate, ADC bits |

This is the `Capability` base class. Both `InstrumentCapability` and `ProductCharacteristic` extend it.

## Functions (MeasurementFunction enum)

The `function` field uses a flat enum of ~51 named signal types. These come from three sources:

| Source | What it contributed |
|--------|-------------------|
| **IVI Foundation** | Instrument class names: IVI-DMM, IVI-Scope, IVI-FGen, IVI-DCPwr, IVI-RFSigGen |
| **IEEE 1641** | Signal primitives: the idea that waveform shapes are parameters, not separate functions |
| **SCPI** | Naming conventions: `dc_voltage` not `DMM:VOLT:DC` |

Grouped by domain:

- **DC/AC basics:** `dc_voltage`, `ac_voltage`, `dc_current`, `ac_current`, `resistance`, `resistance_4w`, `capacitance`, `inductance`, `impedance`, `frequency`, `period`, `temperature`
- **Waveform:** `waveform` (shapes like sine/square are parameters, not separate functions)
- **Power:** `dc_power`, `ac_power`
- **RF:** `rf_power`, `rf_cw`, `s_parameters`, `spectrum`, `phase_noise`, `noise_figure`, `harmonics`
- **Time:** `time_interval`, `pulse_width`, `duty_cycle`, `rise_time`, `fall_time`, `phase`
- **Quality:** `thd`, `snr`, `gain`, `return_loss`, `insertion_loss`, `vswr`, `group_delay`
- **Digital:** `digital_pattern`, `digital_io`, `serial_data`
- **Other:** `diode`, `continuity`, `optical_power`, `wavelength`, `magnetic_field`, `position`, `charge`, `humidity`

Functions describe **what**, not **how**. Both a DMM and an oscilloscope can measure `dc_voltage` — the function is the same, the parameters differ (resolution digits vs bandwidth/sample rate).

## Direction

Direction always describes the hardware it's on:

| Direction | Meaning | Example |
|-----------|---------|---------|
| `input` | This device receives/sinks signal | DMM measures voltage |
| `output` | This device provides/sources signal | PSU outputs voltage |
| `bidir` | Both directions | SMU sources and measures |
| `transform` | Modifies signal in-path | Amplifier, filter |

Directions **pair** between products and instruments:
- Product `output` (DUT provides voltage) needs instrument `input` (DMM measures it)
- Product `input` (DUT consumes power) needs instrument `output` (PSU sources it)

## Typed Parameter Collections

Instead of a single `parameters` dict with role tags, capabilities now have four typed collections where keys are named signal quantities:

```yaml
signals:                         # What's being measured/sourced
  voltage:
    range: {min: 0, max: 1000, units: V}
    accuracy: {pct_reading: 0.07, pct_range: 0.02}
    resolution: {digits: 6.5}
    specs: [...]                 # Condition-dependent overrides

conditions:                      # Operating conditions affecting accuracy
  frequency:
    range: {min: 3, max: 300000, units: Hz}

controls:                        # User-settable parameters
  heater_power:
    range: {min: 0, max: 100, units: W}
    options: [...]               # Optional: discrete choices

attributes:                      # Fixed hardware facts
  bandwidth:
    value: 500000000
    units: Hz
  sample_rate:
    value: 2500000000
    units: Sa/s
```

### Typed Collection Fields

**Signals** — what's being measured or sourced:
| Field | Type | Description |
|-------|------|-------------|
| `range` | `{min, max, units}` | Value range (e.g., 0 to 1000 V) |
| `accuracy` | `{pct_reading, pct_range, absolute, units}` | How close to true value |
| `resolution` | `{digits, value, units}` | Smallest distinguishable change |
| `specs` | `list[SpecBand]` | Condition-dependent specs (see below) |

**Conditions** — operating parameters that affect other parameters' accuracy:
| Field | Type | Description |
|-------|------|-------------|
| `range` | `{min, max, units}` | Range of the condition |

**Controls** — user-settable knobs that affect behavior:
| Field | Type | Description |
|-------|------|-------------|
| `range` | `{min, max, units}` | Settable range |
| `options` | `list[str]` | Discrete choices (optional) |

**Attributes** — fixed hardware specifications:
| Field | Type | Description |
|-------|------|-------------|
| `value` | `float` | Fixed single value |
| `units` | `string` | Units for the value |

### Examples: Typed Collections in Action

```yaml
# Oscilloscope
- function: waveform
  direction: input
  signals:
    voltage:                    # the signal being captured
      range: {min: -5, max: 5, units: V}
      accuracy: {pct_range: 1}
  attributes:
    bandwidth:                  # fixed hardware limit
      value: 500000000
      units: Hz
    sample_rate:                # fixed hardware limit
      value: 2500000000
      units: Sa/s
    resolution:                 # ADC bits
      value: 10
      units: bits

# Temperature controller
- function: temperature
  direction: bidir
  signals:
    temperature:                # what's being sensed/controlled
      range: {min: 0.3, max: 1500, units: K}
      accuracy: {absolute: 0.1, units: K}
      resolution: {value: 0.001, units: K}
  controls:
    heater_power:               # user sets this
      range: {min: 0, max: 100, units: W}

# AC voltage measurement
- function: ac_voltage
  direction: input
  signals:
    voltage:                    # the signal being measured
      range: {min: 0.1, max: 750, units: V}
      accuracy: {pct_reading: 0.07, pct_range: 0.02}
  conditions:
    frequency:                  # affects accuracy
      range: {min: 3, max: 300000, units: Hz}
```

### Why Separate Typed Collections?

The four-collection approach is clearer than role tags because each collection has a well-defined purpose:
- **Signals** participate in matching and have accuracy/range specs
- **Conditions** appear in SpecBand condition constraints but don't participate directly in matching logic
- **Controls** are user-configurable but don't affect the fundamental capability
- **Attributes** are hardware facts that may participate in matching (e.g., scope bandwidth must exceed signal frequency), but are never measured — they're just limits

## Condition-Dependent Specs (SpecBand)

Real instruments don't have a single accuracy — it varies with operating conditions. A DMM's AC voltage accuracy at 10Hz is much worse than at 1kHz. SpecBand captures this:

```yaml
- function: ac_voltage
  direction: input
  signals:
    voltage:
      range: {min: 0.1, max: 750, units: V}
      accuracy: {pct_reading: 0.07, pct_range: 0.02}  # default/best-case
      specs:                                            # condition-dependent overrides
        - conditions:
            frequency: {min: 3, max: 5, units: Hz}
          accuracy: {pct_reading: 0.35, pct_range: 0.03}
        - conditions:
            frequency: {min: 5, max: 300, units: Hz}
          accuracy: {pct_reading: 0.07, pct_range: 0.02}
        - conditions:
            frequency: {min: 300, max: 300000, units: Hz}
          accuracy: {pct_reading: 0.14, pct_range: 0.05}
  conditions:
    frequency:
      range: {min: 3, max: 300000, units: Hz}
```

Each SpecBand says: "when these conditions are met, use this accuracy instead of the default."

### Condition Keys (ConditionKey enum)

The keys in `SpecBand.conditions` are strings. We provide a `ConditionKey` enum as a **shared vocabulary** (30 canonical keys), but it's not enforced at the model level — you can use any string. The canonical keys were derived from auditing 150+ instrument datasheets:

| Category | Keys |
|----------|------|
| **Universal** | `frequency`, `temperature`, `humidity`, `calibration_interval` |
| **Measurement config** | `nplc`, `auto_zero`, `coupling`, `impedance`, `sense_mode`, `sample_rate`, `bandwidth`, `filter`, `gate_time`, `acquisition_mode`, `time_constant` |
| **Signal** | `signal_level`, `crest_factor` |
| **Source/load** | `load`, `input_voltage`, `voltage`, `current`, `duty_cycle`, `slew_rate`, `settling_time` |
| **Sensor** | `sensor`, `wavelength` |
| **RF** | `offset` |

## How Products Use the Same Model

A product characteristic is a capability with physical interface info added:

```yaml
# Product: DC-DC converter board
characteristics:
  rail_3v3:
    function: dc_voltage       # same function enum
    direction: output          # DUT provides this signal
    pin: VOUT                  # which physical pin
    signals:
      voltage:                 # same signal structure
        value: 3.3
        units: V
    specs:
      - conditions:
          load: {min: 0, max: 0.5, units: A}
          temperature: {min: 25, max: 25, units: degC}
        value: 3.3
        accuracy: {pct_reading: 3.0}    # +/-3% = 3.201V to 3.399V
      - conditions:
          load: {min: 0, max: 1.0, units: A}
          temperature: {min: -40, max: 85, units: degC}
        value: 3.3
        accuracy: {pct_reading: 5.0}    # +/-5% over full range
```

The matching engine can now compare:
- Product needs: `dc_voltage`, `output`, 3.3V, +/-5%
- Instrument has: `dc_voltage`, `input`, 0-1000V, 0.0035% accuracy
- Match: function matches, directions pair (output<->input), range contains 3.3V, accuracy is better

## Lineage: Where This Came From

The model draws from three standards, taking the best ideas from each:

| Standard | What we took | What we didn't take |
|----------|-------------|-------------------|
| **IEEE 1641** (Signal & Test Definition) | Signal-oriented thinking: capabilities describe signals, not instruments. Waveform shapes are parameters, not types. | The full signal grammar/composition model (too complex for our needs today) |
| **ATML/IEEE 1671** (Test Description) | Comparator types (GELE, EQ, etc.), UUT characteristics with conditions, direction pairing concept | XML schema, verbose specification structure |
| **IVI Foundation** (Instrument Classes) | Function names from instrument class specs (DMM, Scope, FGen, DCPwr, RFSigGen) | Driver API patterns (we use PyVISA/PyMeasure instead) |

Key design decisions:
- **Flat function enum** instead of IEEE 1641's signal grammar — simpler, covers 95% of real use cases
- **Same model for products and instruments** instead of ATML's separate UUT/instrument schemas — enables direct matching
- **Structured conditions** instead of freeform text — machine-parseable for automated matching
- **Typed parameter collections** instead of role tags — four focused dicts (signals, conditions, controls, attributes) are clearer than one dict with role metadata

## The Full Picture

```
Instrument Catalog (what instruments CAN do)
─────────────────────────────────────────────
catalog_entry:
  id: keysight_34461a
  type: dmm
  channels:
    "1": {terminals: [hi, lo], connector: binding_post, ground: shared}

capabilities:
  - function: dc_voltage        ─┐
    direction: input             │  Capability
    signals:                      │  (shared base class)
      voltage:                   │
        range: {min: 0, max: 1000, units: V}
        accuracy: {pct_reading: 0.0035}
        resolution: {digits: 6.5}

Product Spec (what the DUT NEEDS tested)
────────────────────────────────────────
product:
  id: power_board_v1
  pins:
    VOUT: {name: "J1.3", net: "VOUT_3V3", role: signal}

characteristics:
  rail_3v3:
    function: dc_voltage        ─┐
    direction: output            │  ProductCharacteristic
    pin: VOUT                    │  (extends Capability)
    signals:                      │
      voltage:                   │
        value: 3.3               │
        units: V                 │
    specs:                       │
      - conditions:              │
          load: {min: 0, max: 1} │
        accuracy: {pct_reading: 5.0}

Matching: dc_voltage OUTPUT <-> dc_voltage INPUT
             3.3V within 0-1000V range
             0.0035% << 5% (instrument is more accurate)
             MATCH!
```
