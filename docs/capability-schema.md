# Capability Schema Reference

Source of truth: `litmus/config/models.py`

## Capability Structure

Every catalog YAML capability has these typed parameter dicts:

```yaml
- function: dc_voltage          # MeasurementFunction enum
  direction: input              # input | output | bidir | transform
  channels: ["ch1", "ch2"]      # or range syntax "ai[0:7]"
  signals: { ... }              # What's measured/sourced
  conditions: { ... }           # What affects accuracy
  controls: { ... }             # User-adjustable knobs
  attributes: { ... }           # Fixed hardware facts
```

### signals — the primary measurement/source dimensions

Each signal has range, accuracy, resolution, and condition-dependent overrides (specs/SpecBands).

```yaml
signals:
  voltage:
    range: {min: -10, max: 10, units: V}
    accuracy: {pct_reading: 0.05, pct_range: 0.01, absolute: 0.001}
    resolution: {digits: 6.5}           # OR {bits: 16} OR {value: 0.001, units: V}
    specs:                               # SpecBand overrides — USE THESE
      - when:
          frequency: {min: 3, max: 5, units: Hz}
        accuracy: {pct_reading: 0.35, pct_range: 0.03}
      - when:
          nplc: {min: 10, max: 100}
        accuracy: {pct_reading: 0.01, pct_range: 0.005}
```

**AccuracySpec fields:** `pct_reading`, `pct_range`, `absolute`, `units` (optional — only needed when absolute accuracy units differ from signal units, e.g., accuracy in dB on a percent-range signal)

**ResolutionSpec fields:** `bits`, `digits`, `value`, `units`

**SpecBand `when` keys** MUST reference a sibling name from signals, conditions, or controls on the same capability. Unknown keys cause warnings; duplicate names across categories cause errors.

**SpecBand `when` values** support several match types. Range values inherit units from the referenced condition/control at load time, so units are optional in `when` clauses:

| YAML value | Type | Match logic |
|------------|------|-------------|
| `{min: 20, max: 300}` | RangeSpec | Range containment (`min <= val <= max`) |
| `"SLOW"` | string | Exact equality |
| `50` | float | Exact equality |
| `true` | bool | Exact equality |
| `[50, 600, "HiZ"]` | list | Membership (`val in list`) |

```yaml
specs:
  - when:
      rate: "SLOW"                              # string match
      frequency: {min: 20, max: 300}             # range match (units inherited from condition)
    accuracy: {pct_reading: 0.10}
  - when:
      output_impedance: 50                      # scalar float match
    range: {min: 0, max: 2, units: Vrms}
  - when:
      output_impedance: [50, 600]               # list membership match
    accuracy: {pct_reading: 0.3}
```

### conditions — operating conditions that affect accuracy

Range only. No accuracy of their own. Feed SpecBand lookup.

```yaml
conditions:
  frequency:
    range: {min: 3, max: 300000, units: Hz}
  temperature:
    range: {min: 18, max: 28, units: degC}
```

### controls — user-configurable knobs

Discrete options OR continuous range. Can be referenced in SpecBand `when`.

```yaml
controls:
  coupling:
    options: ["AC", "DC", "GND"]
    default: "DC"
  v_per_div:
    range: {min: 0.001, max: 10, units: V/div}
  nplc:
    range: {min: 0.02, max: 100}
    default: 1
  impedance:
    options: ["50ohm", "1Mohm"]
    default: "1Mohm"
```

### attributes — fixed hardware facts (not adjustable)

Attributes can have condition-dependent overrides via `specs`, same pattern as signals.
Use this when a fixed fact varies by operating condition (e.g., test current by range).

```yaml
attributes:
  sample_rate:
    value: 5000000000        # scalar: use 'value'
    units: Sa/s
  operating_temperature:
    range: {min: 0, max: 55, units: degC}   # min/max: use 'range'
  test_current:
    value: 0.001
    units: A
    specs:
      - when: {range: 100}
        value: 0.001
      - when: {range: 10000}
        value: 0.0001
```

Attributes support either `value` (scalar) or `range` (min/max), never both.

## MeasurementFunction — use the MOST SPECIFIC value

Common mistakes to avoid:

| Wrong | Right | Why |
|-------|-------|-----|
| `dc_voltage` for heater output | `heater_power` | Dedicated enum exists |
| `dc_current` for sensor excitation | `excitation_current` | Dedicated enum exists |
| `dc_voltage` for trigger I/O | `trigger` | Dedicated enum exists |
| Only `waveform` on a scope | Also add `dc_voltage`, `ac_voltage`, `frequency`, `rise_time`, `fall_time`, `pulse_width`, `duty_cycle`, `phase` | Scopes measure all of these |
| `dc_voltage` for 10 MHz ref | `reference_clock` | Dedicated enum exists |

Full enum list: read `MeasurementFunction` in `litmus/config/models.py`

## Board-Level Attributes (`catalog_entry.attributes`)

Device-wide facts that don't belong to any single capability go on `catalog_entry.attributes`:

```yaml
catalog_entry:
  id: ni_pxie_6341
  # ... other fields ...
  attributes:
    operating_temperature: {range: {min: 0, max: 55, units: degC}}
    storage_temperature: {range: {min: -40, max: 71, units: degC}}
    weight: {value: 157, units: g}
    warmup_time: {value: 15, units: min}
    calibration_interval: {value: 2, units: yr}
    max_working_voltage: {value: 11, units: V}
    power_3v3: {value: 1.6, units: W}
    pollution_degree: {value: 2}
    max_altitude: {value: 2000, units: m}
```

Common board-level attributes: `operating_temperature`, `storage_temperature`, `operating_humidity`, `storage_humidity` (use `range`), `weight`, `dimension_*`, `warmup_time`, `calibration_interval`, `pollution_degree`, `max_altitude`, `power_*`, `usb_bus_speed`, `max_working_voltage`.

**Do NOT put these on a single capability's attributes** — they describe the whole device.

## Channel Topology

Every channel referenced in capabilities MUST exist in `catalog_entry.channels`:

```yaml
channels:
  "ch1":
    label: "Channel 1"
    terminals: [signal]          # TerminalRole enum: hi, lo, sense_hi, sense_lo, guard, signal, trigger
    connector: bnc               # ConnectorType enum
    ground: shared               # GroundTopology: floating, shared, earth
```

## What goes WHERE — decision tree

| Datasheet spec | Schema location |
|----------------|----------------|
| Voltage/current/power range | `signals.X.range` |
| Accuracy (±% rdg + % range + offset) | `signals.X.accuracy` |
| Accuracy that varies by frequency/range/mode | `signals.X.specs[]` (SpecBand) |
| Display digits, ADC bits, resolution value | `signals.X.resolution` |
| Frequency range, bandwidth, temperature range | `conditions.X.range` |
| Coupling, impedance, NPLC, sensitivity, filter | `controls.X` |
| Sample rate, memory depth, input noise | `attributes.X` (on the capability) |
| Weight, operating temp, warmup, cal interval | `catalog_entry.attributes.X` (board-level) |
| Connector type, terminal layout | `channels.X` (ChannelTopology) |

## Same quantity, different roles

The same physical quantity can be a signal, condition, control, or attribute depending on its **role** in the capability. Ask: **"If I remove this, does the capability still make sense?"** If NO → signal. If YES → supporting role.

| Quantity | Signal | Condition | Control | Attribute |
|----------|--------|-----------|---------|-----------|
| **Frequency** | `function: frequency` (counter), `reference_clock`, `rf_cw/rf_sweep` (carrier) | Affects accuracy of AC measurements | `function: waveform` (user dials freq, output is voltage) | Fixed bandwidth, sample rate |
| **Voltage** | DMM, PSU, scope waveform | Input voltage affects output accuracy | — | Max input voltage, trigger threshold |
| **Current** | DMM, SMU, electronic load | Load current derates PSU output | — | Max output current limit |
| **Temperature** | `function: temperature` (thermometer, probe, controller readback) | Operating range for guaranteed specs | Setpoint on temperature controller | — |
| **Power** | `rf_power`, `dc_power`, power meter | — | — | Max dissipation rating |
| **Phase** | `function: phase` (lock-in, VNA) | — | — | Orthogonality error |
| **Impedance** | `function: impedance` (LCR meter) | — | User-selectable (50Ω/1MΩ) | Fixed output impedance |

## Comments policy

- **3-line header max**: instrument name, PDF source, model variants
- **No spec data in comments** — if it's in the datasheet, encode it in the schema
- Page references go in the header comment only
