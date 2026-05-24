# Catalog schema reference

Authoritative shape of a `catalog/<vendor>/<model>.yaml` entry — fields, validation rules, and a decision tree for where each datasheet spec lands.

For worked recipes (one per recurring datasheet shape), see the [catalog cookbook](catalog-cookbook.md).

**Status:** Frozen at `CATALOG_SCHEMA_VERSION = "1.0"` for the 0.1.0 release. Schema evolution within `1.0` is additive only — new optional fields and new enum values are allowed; renames, removals, and type narrowing require a version bump.

For the full Pydantic model surface backing every field below, see [models reference](models.md#catalog-entry--litmusmodelscatalog).

## File shape

A catalog YAML file is a single instrument-model document. Every field below sits at the root of the document. **There is no `catalog_entry:` wrapper** — the file *is* the entry. Filename stem and the document's `id:` must match.

```yaml
id: generic_psu
manufacturer: Generic
model: "PSU"
name: "Generic DC Power Supply"
description: "Programmable DC power supply for sourcing voltage and current to DUT"
type: psu                      # dmm | psu | scope | fgen | smu | eload | …
interfaces: [usb, lan, gpib]   # optional — supported control interfaces
form_factor: bench             # optional — bench | pxi | modular | …
driver: pymeasure.instruments.keysight.KeysightE36312A  # optional default driver
scaffold: false                # true = approximate entry, needs human review
base: null                     # optional — variant inheritance (see Variants below)
channels:                      # dict[name, ChannelTopology]
  "CH1":
    terminals: [hi, lo]
    connector: binding_post
    ground: floating
attributes:                    # dict[name, Attribute] — board-level facts
  weight: {value: 157, units: g}
capabilities:                  # list[InstrumentCapability]
  - function: dc_voltage
    direction: output
    channels: ["CH1"]
    signals: { ... }
```

Field reference — `InstrumentCatalogEntry`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | yes | Filename stem; checked at load |
| `manufacturer` | `str` | yes | |
| `model` | `str` | yes | |
| `name` | `str \| None` | no | Defaults to `"{manufacturer} {model}"` |
| `description` | `str \| None` | no | |
| `type` | `str` | yes | Loose convention: `dmm`, `psu`, `scope`, `fgen`, `smu`, `eload`, … |
| `base` | `str \| None` | no | Sibling catalog stem to inherit from (see Variants) |
| `scaffold` | `bool` | no | `true` marks an approximate entry that needs verification |
| `driver` | `str \| None` | no | Dotted path used as default when a station omits `driver:` |
| `interfaces` | `list[str]` | no | |
| `form_factor` | `str \| None` | no | |
| `channels` | `dict[str, ChannelTopology]` | no | List every channel that the capabilities below reference. Not enforced at load time — typos resolve to an empty channel and quietly fail at runtime. |
| `attributes` | `dict[str, Attribute]` | no | Board-level facts that don't belong to a single capability |
| `capabilities` | `list[InstrumentCapability]` | no | The measurement and source functions this model can do |

Unknown root-level keys are rejected at load time.

## Decision tree — where does this datasheet spec go?

| Datasheet spec | Schema location |
|---|---|
| Voltage / current / power range | `capabilities[].signals.X.range` |
| Accuracy (±% rdg + % range + offset) | `capabilities[].signals.X.accuracy` |
| Accuracy that varies by frequency / range / mode | `capabilities[].signals.X.bands[]` (`SpecBand`) |
| Display digits, ADC bits, resolution value | `capabilities[].signals.X.resolution` |
| Frequency / temperature / humidity envelope | `capabilities[].conditions.X.range` |
| Coupling, impedance, NPLC, filter, sense mode | `capabilities[].controls.X` |
| Sample rate, memory depth, input noise | `capabilities[].attributes.X` (per capability) |
| Weight, warmup time, calibration interval, max altitude | `attributes.X` at the root (board-level) |
| Connector type, terminal layout | `channels.X` ([ChannelTopology](#channel-topology)) |
| Option codes / SKU variants | Separate file with `base:` referencing the parent (see [Variants](#variants-option-codes)) |
| `function:` value (`dc_voltage`, `waveform`, …) | [MeasurementFunction enum](#measurementfunction-enum) |

## Capabilities

Every entry in `capabilities[]` is an `InstrumentCapability` — a measurement or stimulus function with typed parameter dicts.

```yaml
capabilities:
  - function: dc_voltage         # MeasurementFunction enum — see below
    direction: input             # input | output | bidir | transform
    channels: ["1", "2"]         # explicit list or range syntax "ai[0:7]"
    readback: false              # optional — true marks a built-in meter rather than the primary measurement
    units: V                     # optional fallback when every signal shares units
    signals: { ... }             # what's being measured / sourced
    conditions: { ... }          # what affects the spec but the user doesn't dial
    controls: { ... }            # user-adjustable knobs
    attributes: { ... }          # fixed hardware facts for this capability
    bands: [ ... ]               # capability-wide conditional overrides
```

The four parameter dicts (`signals`, `conditions`, `controls`, `attributes`) are mutually exclusive: a single name may not appear in `signals` and `conditions`, `signals` and `controls`, or `conditions` and `controls`. (Names in `attributes` are not cross-checked against the other three — `attributes` describes inherent facts, while the other three describe operating parameters.)

The capability-level `units:` field is informational. Consumers fall back to it only when the signal's own units aren't set; the model itself accepts whatever you write.

### signals — measured or sourced dimensions

Each `Signal` carries a `range`, optional `accuracy` and `resolution`, and optional `bands` for condition-dependent overrides.

```yaml
signals:
  voltage:
    range:      {min: -10, max: 10, units: V}
    accuracy:   {pct_reading: 0.05, pct_range: 0.01, absolute: 0.001}
    resolution: {digits: 6.5}                  # OR {bits: 16} OR {value: 0.001, units: V}
    bands:                                     # apply only when the when-clause matches
      - when:     {frequency: {min: 3, max: 5, units: Hz}}
        accuracy: {pct_reading: 0.35, pct_range: 0.03}
      - when:     {nplc: {min: 10, max: 100}}
        accuracy: {pct_reading: 0.01, pct_range: 0.005}
```

| Field | Type |
|---|---|
| `range` | `RangeSpec` |
| `accuracy` | `AccuracySpec` |
| `resolution` | `ResolutionSpec` |
| `value` | `float` (product-side scalar — instruments use `range`) |
| `units` | `str` |
| `bands` | `list[SpecBand]` |
| `qualifier` | `SpecQualifier` |

**`AccuracySpec`** — `pct_reading`, `pct_range`, `absolute`, `units` (optional; only when the absolute term's units differ from the signal's, e.g. dB on a percent-range signal).

**`ResolutionSpec`** — `bits`, `digits`, `value`, `units`. Pick one of `bits` / `digits` / `value` to describe how the instrument quantizes — combining them isn't validated but isn't meaningful either.

### conditions — operating conditions that affect accuracy

Continuous (`range`) or discrete (`options`), with optional `bands` for nested overrides. Conditions are *not* user-set per measurement — they describe the operating envelope under which signals were characterized.

```yaml
conditions:
  frequency:
    range: {min: 3, max: 300000, units: Hz}
  temperature:
    range: {min: 18, max: 28, units: degC}
  calibration_interval:
    options: ["24_hour", "90_day", "1_year", "2_year"]
```

### controls — user-configurable knobs

Discrete `options` or continuous `range`. Can carry `default`, `resolution` (step size), and `bands`. Reference these from `SpecBand` `when:` clauses.

```yaml
controls:
  coupling:
    options: ["AC", "DC", "GND"]
    default: "DC"
  v_per_div:
    range:      {min: 0.001, max: 10, units: V/div}
    resolution: {value: 0.001, units: V/div}
  power:
    range: {min: -20, max: 20, units: dBm}
    bands:
      - when:  {frequency: {min: 250000, max: 3200000000}}
        range: {min: -20, max: 25, units: dBm}
```

### attributes (per-capability) — fixed hardware facts

Capability-scoped facts that don't change with operating point — bandwidth, sample rate, input noise. May carry `bands` for facts that *do* vary with condition (e.g. test current that depends on resistance range).

```yaml
attributes:
  sample_rate:
    value: 5000000000
    units: Sa/s
  scpi_version:
    value: "1997.0"
  test_current:
    value: 0.001
    units: A
    bands:
      - when:  {range: 100}
        value: 0.001
      - when:  {range: 10000}
        value: 0.0001
```

Each `Attribute` must carry exactly one of `value` (numeric, string, or bool), `range` (min/max), or `options` (enumerated list) — *or* it may carry only `bands` when every value is condition-dependent. Combining more than one of `value` / `range` / `options` raises a validation error at load time.

### Board-level `attributes:` (file root)

Device-wide facts that don't belong to any single capability live at the root, *not* nested under a capability:

```yaml
attributes:
  operating_temperature: {range: {min: 0, max: 55, units: degC}}
  storage_temperature:   {range: {min: -40, max: 71, units: degC}}
  weight:                {value: 157, units: g}
  warmup_time:           {value: 15, units: min}
  calibration_interval:  {value: 2, units: yr}
  max_working_voltage:   {value: 11, units: V}
  pollution_degree:      {value: 2}
  max_altitude:          {value: 2000, units: m}
```

Conventional names (not enforced, but consistent across vendors): `operating_temperature`, `storage_temperature`, `operating_humidity`, `storage_humidity` (use `range`); `weight`, `dimension_*`, `warmup_time`, `calibration_interval`, `pollution_degree`, `max_altitude`, `power_*`, `usb_bus_speed`, `max_working_voltage`.

## `SpecBand` — condition-dependent overrides

A `SpecBand` says "at this operating point, here are the specs." Any field set on the band overrides the parent default; any field left `None` inherits.

```yaml
bands:
  - when:
      rate: "SLOW"                              # string match
      frequency: {min: 20, max: 300}            # range match (units inherited from sibling)
    accuracy: {pct_reading: 0.10}
  - when:
      output_impedance: 50                      # scalar float match
    range: {min: 0, max: 2, units: Vrms}
  - when:
      output_impedance: [50, 600]               # list membership
    accuracy: {pct_reading: 0.3}
  - when:
      frequency: {value: 100000000, units: Hz}  # point with explicit units
    accuracy: {pct_reading: 0.05}
```

**`when:` keys** must reference a name in `signals`, `conditions`, or `controls` on the *same capability*. Unknown keys raise `ValueError` and the file fails to load.

**`when:` values** — pick by what you need to express:

| YAML shape | Match logic |
|---|---|
| `{min: 20, max: 300}` | `RangeSpec` — `min <= val <= max` |
| `{value: 100e6, units: Hz}` | `PointSpec` — exact equality with explicit units |
| `{values: [50, 600], units: ohm}` | `ListSpec` — membership with explicit units |
| `"SLOW"` | bare string — exact equality |
| `50` | bare scalar — exact equality |
| `true` | bare bool — exact equality |
| `[50, 600, "HiZ"]` | bare list — membership |

When a `RangeSpec` / `PointSpec` / `ListSpec` `when:` value omits `units:`, the validator copies them from the sibling whose `range.units` the key references. Bare scalars and lists carry no units — only use them when the sibling units are unambiguous.

Multiple `when:` keys are ANDed (every clause must match). An empty `when: {}` is unconditional and always applies.

## `qualifier` — calibration confidence

`Signal`, `Attribute`, and `SpecBand` carry an optional `qualifier:` indicating how trusted the value is. There is no implied default — omit it when unknown; setting it carries the meaning below.

| Value | Meaning |
|---|---|
| `guaranteed` | Warranted specification, tested and traceable |
| `typical` | Measured across representative units, not warranted |
| `nominal` | Design target, not individually tested |
| `supplemental` | Informational, not warranted |


```yaml
signals:
  voltage:
    range:     {min: -10, max: 10, units: V}
    qualifier: guaranteed
    bands:
      - when:      {frequency: {min: 3, max: 5, units: Hz}}
        accuracy:  {pct_reading: 0.35}
        qualifier: typical
```

## `MeasurementFunction` enum {#measurementfunction-enum}

Pick the **most specific** value the datasheet supports. See [`MeasurementFunction` in the models reference](models.md#enum-measurementfunction) for the full set (50+ values across DC, AC, RF, optical, environmental, motion).

Common mistakes:

| Wrong | Right | Why |
|---|---|---|
| `dc_voltage` for heater output | `heater_power` | Dedicated enum exists |
| `dc_current` for sensor excitation | `excitation_current` | Dedicated enum exists |
| `dc_voltage` for trigger I/O | `trigger` | Dedicated enum exists |
| Only `waveform` on a scope | Plus `dc_voltage`, `ac_voltage`, `frequency`, `rise_time`, `fall_time`, `pulse_width`, `duty_cycle`, `phase` | Scopes measure all of these |
| `dc_voltage` for 10 MHz ref | `reference_clock` | Dedicated enum exists |

## Channel topology {#channel-topology}

Every channel referenced from `capabilities[].channels` must have a matching entry in the file-root `channels:` dict.

```yaml
channels:
  "ch1":
    label: "Channel 1"                   # optional display name
    terminals: [hi, lo, sense_hi, sense_lo]
    connector: bnc                       # ConnectorType enum
    connector_pin:                       # optional — terminal-role → pin number / name
      hi: 1
      lo: 2
    ground: shared                       # GroundTopology: floating, shared, earth
    optional: false                      # true = not present on all configurations
```

Allowed enum values (see [enums in the models reference](models.md#enum-terminalrole) for the full lists):

- **terminals** — `hi`, `lo`, `sense_hi`, `sense_lo`, `guard`, `ground`, `signal`, `trigger`, plus the four-wire impedance roles `hcur`, `hpot`, `lcur`, `lpot`.
- **connector** — physical connector at the chassis: `binding_post`, `banana`, `bnc`, `sma`, `smb`, `triax`, `terminal_block`, `screw_terminal`, `dsub`, `d_sub_9`, `d_sub_15`, `vhdci`, `phoenix`, `tekvpi`, `pxi`, `spring`, `probe`, `apc_3.5`, `type_n`, `k_2.4mm`, `v_1.85mm`, `proprietary`. (Bus / control interfaces — `usb`, `lan`, `gpib` — belong in the file-root `interfaces:` field, NOT in `connector:`.)
- **ground** — `floating`, `shared`, `earth`.

## Variants (option codes) {#variants-option-codes}

Hardware option codes ("Opt. 521", "Option S20") that change SKU and behavior live in their **own catalog file** that points back at the base with `base:`. The loader merges the variant on top of the base at load time.

```yaml
# catalog/keysight/n5183b.yaml — the base
id: keysight_n5183b
manufacturer: Keysight
model: "N5183B"
type: fgen
capabilities:
  - function: rf_cw
    direction: output
    signals:
      power: {range: {min: -20, max: 20, units: dBm}}
```

```yaml
# catalog/keysight/n5183b_opt_521.yaml — the +Option 521 variant
id: keysight_n5183b_opt_521
base: keysight_n5183b                # ← merge on top of this catalog entry
model: "N5183B-521"
capabilities:
  - function: rf_cw
    direction: output
    signals:
      power: {range: {min: -20, max: 25, units: dBm}}   # higher output, same shape
```

`base:` resolution searches the same directory first, then the catalog root. Circular inheritance and missing-base references raise `ValueError` at load time.

**What `base:` merges and what it replaces:** capabilities (matched by `(function, direction)` key) deep-merge — variant entries only need to declare the deltas inside matching signals/conditions/controls/attributes. Root-level dicts (`channels:`, `interfaces:`, `attributes:`) and root-level scalars (`type:`, `model:`, etc.) are *replaced wholesale* by the variant if present, *inherited* from the base otherwise. So a variant that needs one extra channel must redeclare all the channels, not just the new one.

## Same quantity, different roles

The same physical quantity can be a signal, condition, control, or attribute depending on its role. The decision question: **"If I remove this, does the capability still make sense?"** No → `signal`. Yes → one of the supporting three.

| Quantity | Signal | Condition | Control | Attribute |
|---|---|---|---|---|
| **Frequency** | `function: frequency` (counter), `reference_clock`, `rf_cw` / `rf_sweep` carrier | Affects accuracy of AC measurements | `function: waveform` (user dials freq, output is voltage) | Fixed bandwidth, sample rate |
| **Voltage** | DMM, PSU, scope waveform | Input voltage affects output accuracy | — | Max input voltage, trigger threshold |
| **Current** | DMM, SMU, electronic load | Load current derates PSU output | — | Max output current limit |
| **Temperature** | `function: temperature` (probe, controller readback) | Operating range for guaranteed specs | Setpoint on temperature controller | — |
| **Power** | `rf_power`, `dc_power`, power meter | — | — | Max dissipation rating |
| **Phase** | `function: phase` (lock-in, VNA) | — | — | Orthogonality error |
| **Impedance** | `function: impedance` (LCR meter) | — | User-selectable (50Ω/1MΩ) | Fixed output impedance |

## Validating a catalog entry

Load the file through the store — if it parses and validates, the entry is well-formed:

```bash
uv run python -c "
from pathlib import Path
from litmus.store import load_catalog_entry
entry = load_catalog_entry(Path('catalog/keysight/n5183b.yaml'))
print(f'OK: {entry.manufacturer} {entry.model} — {len(entry.capabilities)} capability/-ies')
"
```

The loader raises with the offending field path on type / shape mismatches (extra keys, wrong type, missing required field), and on the semantic checks: unknown `SpecBand` `when:` keys, or a name appearing in more than one of `signals` / `conditions` / `controls` on the same capability.

## Comments policy

No comments in catalog YAML. All metadata belongs in the schema — extend the model rather than dropping a `#` note.

## See also

- [Catalog cookbook](catalog-cookbook.md) — worked recipes per datasheet shape
- [Capabilities](../concepts/configuration/capabilities.md) — what the catalog enables (matching, profile selection)
- [Tutorial: capabilities](../tutorial/08-capabilities.md) — introduces the schema in context
- [Models reference](models.md) — full Pydantic model surface
