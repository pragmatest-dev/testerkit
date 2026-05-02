# Capability Schema Examples

Companion to `capability-schema.md`. These examples show correct YAML for common patterns.
Study `capability-schema.md` "What goes WHERE" decision tree FIRST, then use these examples.

## 1. Accuracy by frequency band → SpecBands

```yaml
# Inventory: AC voltage accuracy varies by frequency
#   20-100 Hz: ±0.1% rdg + 0.02% range
#   100-20kHz: ±0.05% rdg + 0.01% range
#   20-100kHz: ±0.2% rdg + 0.05% range
- function: ac_voltage
  direction: input
  channels: [input]
  signals:
    voltage:
      range: {min: 0, max: 750, units: V}
      accuracy: {pct_reading: 0.05, pct_range: 0.01}  # best-case default
      resolution: {digits: 6.5}
      bands:
        - when:
            frequency: {min: 20, max: 100, units: Hz}
          accuracy: {pct_reading: 0.1, pct_range: 0.02}
        - when:
            frequency: {min: 100, max: 20000, units: Hz}
          accuracy: {pct_reading: 0.05, pct_range: 0.01}
        - when:
            frequency: {min: 20000, max: 100000, units: Hz}
          accuracy: {pct_reading: 0.2, pct_range: 0.05}
  conditions:
    frequency:
      range: {min: 20, max: 100000, units: Hz}
```

## 2. Multi-row performance table → SpecBands (NEVER flat attributes)

Any table where a value varies by a condition MUST become SpecBands on a signal.

```yaml
# Inventory: reading rate varies by frequency band and acquisition mode
#   Single, 20-100 Hz: 14 rdgs/s
#   Single, 100-1kHz: 24 rdgs/s
#   Automatic, 400Hz-20kHz: 6.6 rdgs/s
#
# WRONG — flat attributes:
#   reading_rate_single_20hz: {value: 14}
#   reading_rate_auto_400hz: {value: 6.6}
#
# RIGHT — SpecBands on a signal:
  signals:
    reading_rate:
      range: {min: 5.5, max: 28, units: readings/s}
      bands:
        - when:
            acquisition_mode: {min: 0, max: 0}
            fundamental_frequency: {min: 20, max: 100, units: Hz}
          value: 14
        - when:
            acquisition_mode: {min: 0, max: 0}
            fundamental_frequency: {min: 100, max: 1000, units: Hz}
          value: 24
        - when:
            acquisition_mode: {min: 1, max: 1}
            fundamental_frequency: {min: 400, max: 20000, units: Hz}
          value: 6.6
  controls:
    acquisition_mode:
      options: ["single", "automatic"]
```

Same pattern applies to sweep time, settling time, or ANY table with rows:

```yaml
# Inventory: Frequency sweep time varies by number of frequencies
#   5 freqs: 0.2 s, 30 freqs: 1.1 s, 100 freqs: 3.5 s, 200 freqs: 6.9 s
#
# WRONG — flat attributes:
#   sweep_time_5_freq: {value: 0.2, units: s}
#   sweep_time_30_freq: {value: 1.1, units: s}
#
# RIGHT — SpecBands:
  signals:
    sweep_time:
      range: {min: 0.2, max: 6.9, units: s}
      bands:
        - when: {num_frequencies: {min: 5, max: 5}}
          value: 0.2
        - when: {num_frequencies: {min: 30, max: 30}}
          value: 1.1
        - when: {num_frequencies: {min: 100, max: 100}}
          value: 3.5
        - when: {num_frequencies: {min: 200, max: 200}}
          value: 6.9
  controls:
    num_frequencies:
      range: {min: 5, max: 200}
```

## 3. Dual-unit values → two attributes

```yaml
# Inventory: "Residual distortion: 0.004% or −87 dB"
# These are alternate representations of the same fixed hardware floor.
# Create BOTH as attributes:
attributes:
  residual_distortion_pct: {value: 0.004, units: pct}
  residual_distortion_dB: {value: -87, units: dB}
```

## 4. Accuracy with units different from signal

```yaml
# When the datasheet specifies accuracy in DIFFERENT units than the signal range,
# add `units:` to AccuracySpec. This applies to ANY measurement where accuracy
# is expressed in a different unit system than the signal itself.
#
# Common cases:
#   - Distortion in % but accuracy in dB
#   - Power in W but accuracy in dBm
#   - Gain in V/V but accuracy in dB
#
# Example: signal range in percent, accuracy specified as ±0.8 dB
signals:
  distortion:
    range: {min: 0, max: 100, units: pct}
    accuracy: {absolute: 0.8, units: dB}
    resolution: {value: 0.0001, units: pct}

# Example: signal in watts, accuracy in dBm
signals:
  power:
    range: {min: 0, max: 10, units: W}
    accuracy: {absolute: 0.5, units: dBm}
```

## 5. Use typed models — NEVER flatten structured values into attributes

The schema has typed models: `AccuracySpec`, `ResolutionSpec`, `RangeSpec`. If an inventory value fits one of these, use it — don't store it as a flat `Attribute`.

```yaml
# Inventory: "Frequency Accuracy: ±0.01% of reading"
# This is an AccuracySpec (pct_reading). NEVER flatten it.
#
# WRONG — flat attribute:
attributes:
  frequency_accuracy_pct_reading: {value: 0.01, units: pct}
#
# RIGHT — if a frequency signal exists on this capability:
signals:
  frequency:
    accuracy: {pct_reading: 0.01}
#
# RIGHT — if no frequency signal exists (subsystem spec), keep as attribute
# but the name must NOT encode the accuracy type:
attributes:
  frequency_accuracy: {value: 0.01, units: pct_reading}
```

```yaml
# Inventory: "Resolution: 6.5 digits"
# This is a ResolutionSpec. NEVER flatten it.
#
# WRONG:
attributes:
  resolution_digits: {value: 6.5}
#
# RIGHT:
signals:
  voltage:
    resolution: {digits: 6.5}
```

## 6. Ranges → conditions (NOT flat attribute pairs)

```yaml
# Inventory: "Harmonic Frequency Range: 40 Hz–50 kHz"
# This is a range that bounds where the instrument operates.
# Schema: "Frequency range, bandwidth" → conditions.X.range
#
# WRONG — flat attributes:
#   harmonic_frequency_min: {value: 40, units: Hz}
#   harmonic_frequency_max: {value: 50000, units: Hz}
#
# RIGHT — condition:
conditions:
  harmonic_frequency:
    range: {min: 40, max: 50000, units: Hz}
```

## 7. Shared controls — follow inventory "Applies To" EXACTLY

```yaml
# The inventory's USER-SELECTABLE SETTINGS has an "Applies To" column.
# That column is GROUND TRUTH. Put each control ONLY on the listed capabilities.
# Do NOT guess or infer — the inventory agent read the datasheet, you didn't.
#
# Example inventory USER-SELECTABLE SETTINGS:
#   range → All: cap_A, cap_B, cap_C
#   filter_type → cap_B, cap_C only
#   averaging_count → cap_A only
#
# RIGHT — each control appears ONLY where inventory says:

- function: cap_A
  controls:
    range:
      options: [0.1, 1, 10, 100]
      units: V
    averaging_count:       # cap_A only per inventory
      range: {min: 1, max: 100}

- function: cap_B
  controls:
    range:
      options: [0.1, 1, 10, 100]
      units: V
    filter_type:           # cap_B and cap_C only per inventory
      options: ["none", "lowpass", "highpass"]

- function: cap_C
  controls:
    range:
      options: [0.1, 1, 10, 100]
      units: V
    filter_type:           # cap_B and cap_C only per inventory
      options: ["none", "lowpass", "highpass"]

# WRONG — putting filter_type on cap_A (inventory doesn't list it)
# WRONG — putting averaging_count on cap_B/cap_C (inventory doesn't list them)
```

## 8. Shared attributes on ALL applicable capabilities

```yaml
# Input impedance applies to ALL measurement capabilities on the same input.
# It's a capability-level attribute (not catalog_entry.attributes) because
# different capabilities on the same instrument can have different impedances.
- function: thd
  attributes:
    input_impedance: {value: 1000000, units: ohm}
    input_capacitance: {value: 100, units: pF}

- function: ac_voltage
  attributes:
    input_impedance: {value: 1000000, units: ohm}  # repeated
    input_capacitance: {value: 100, units: pF}      # repeated
```

## 9. Board-level vs capability-level attributes

```yaml
# Device-wide facts → catalog_entry.attributes (ONE place, not on capabilities)
# Capability-specific facts → capability attributes (repeated on each applicable cap)
#
# Board-level (catalog_entry.attributes) — use range for min/max, value for scalars:
catalog_entry:
  attributes:
    operating_temperature: {range: {min: 0, max: 55, units: degC}}
    storage_temperature: {range: {min: -40, max: 71, units: degC}}
    weight: {value: 157, units: g}
    warmup_time: {value: 15, units: min}
#
# WRONG — _min/_max suffix pairs:
#   operating_temp_min: {value: 0, units: degC}
#   operating_temp_max: {value: 55, units: degC}
#
# Capability-level (on each capability):
#   input_impedance, input_capacitance, sample_rate, bandwidth
#   residual_distortion (specific to distortion measurement)
```

## 10. Conditional attributes — use specs, NOT name-encoded keys

```yaml
# Inventory: Test current depends on resistance range
#   100 Ω range: 1 mA
#   1 kΩ range: 1 mA
#   10 kΩ range: 100 µA
#   100 kΩ range: 10 µA
#   1 MΩ range: 5 µA
#   10 MΩ range: 500 nA
#
# WRONG — name-encoded antipattern:
#   test_current_100ohm: {value: 0.001, units: A}
#   test_current_10kohm: {value: 0.0001, units: A}
#   test_current_1mohm: {value: 0.000005, units: A}
#
# RIGHT — conditional attribute with bands:
attributes:
  test_current:
    value: 0.001              # default / best-case
    units: A
    bands:
      - when: {range: 100}
        value: 0.001
      - when: {range: 1000}
        value: 0.001
      - when: {range: 10000}
        value: 0.0001
      - when: {range: 100000}
        value: 0.00001
      - when: {range: 1000000}
        value: 0.000005
      - when: {range: 10000000}
        value: 0.0000005
```

The `when` keys reference siblings (signals, conditions, or controls) on the same capability — same rules as signal SpecBands.

## 11. Comments — never put spec data in comments

```yaml
# WRONG — spec value hidden in a comment:
accuracy: {absolute: 1.5}  # ±1.5 dB, 100 Hz–20 kHz

# RIGHT — frequency range in conditions, not comment:
accuracy: {absolute: 1.5}
conditions:
  fundamental_frequency:
    range: {min: 100, max: 20000, units: Hz}
```

## 12. Condition ranges must match the inventory

```yaml
# Inventory says THD+N accuracy applies to "100 Hz to 20 kHz"
# WRONG:
conditions:
  fundamental_frequency:
    range: {min: 20, max: 20000, units: Hz}  # 20 Hz is wrong!

# RIGHT:
conditions:
  fundamental_frequency:
    range: {min: 100, max: 20000, units: Hz}  # matches inventory
```

## 13. Resolution — match signal units

```yaml
# Inventory: "Resolution: 0.0001% or 0.00001 dB"
# Signal uses dB → use dB form:
signals:
  distortion:
    range: {min: -120, max: 0, units: dB}
    resolution: {value: 0.00001, units: dB}

# Signal uses pct → use pct form:
signals:
  distortion:
    range: {min: 0, max: 100, units: pct}
    resolution: {value: 0.0001, units: pct}
```

## 14. Redundant SpecBands — don't repeat top-level accuracy

```yaml
# If there's only ONE accuracy spec across the whole frequency range,
# just use the top-level accuracy. Do NOT create a SpecBand that
# duplicates it.
#
# WRONG — vacuous SpecBand:
signals:
  distortion:
    accuracy: {absolute: 0.8}
    bands:
      - when:
          fundamental_frequency: {min: 20, max: 20000, units: Hz}
        accuracy: {absolute: 0.8}  # same as top-level!

# RIGHT — just use top-level, no SpecBand needed:
signals:
  distortion:
    accuracy: {absolute: 0.8}
    # No specs[] needed — accuracy doesn't vary
```
