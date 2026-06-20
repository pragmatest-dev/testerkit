# Step 8: Capability Matching

**Goal:** Understand how Litmus matches parts to compatible stations.

## The Problem

You have:
- Multiple parts with different test requirements
- Multiple stations with different instruments

How do you know which station can test which part?

## The Solution: Capabilities

Every part characteristic implies a required capability:

```
Part: output_voltage (function: dc_voltage, direction: OUTPUT)
         ↓
Required: dc_voltage measurement capability (direction: INPUT)
         ↓
Station: DMM provides dc_voltage INPUT
         ↓
Match!
```

## Direction Flip

The key insight: **directions flip** between parts and instruments.

| Part Direction | Instrument Direction | Why |
|-------------------|---------------------|-----|
| OUTPUT (UUT provides) | INPUT (measure) | Need to measure what UUT outputs |
| INPUT (UUT receives) | OUTPUT (source) | Need to source what UUT needs |

### Example

A power converter:
- **input_voltage** (direction: INPUT) → UUT receives power → need PSU with dc_voltage OUTPUT
- **output_voltage** (direction: OUTPUT) → UUT provides voltage → need DMM with dc_voltage INPUT

## How Matching Works

**Part spec defines requirements:**
```yaml
# parts/power_board.yaml
characteristics:
  input_voltage:
    function: dc_voltage
    direction: input       # UUT needs input voltage
    unit: V

  output_voltage:
    function: dc_voltage
    direction: output      # UUT outputs voltage
    unit: V
```

**Station provides capabilities:** (`catalog_ref` points at an entry in the instrument catalog — `catalog/*.yaml` — that declares this instrument model's full capability shape. See [reference/catalog-schema](../reference/catalog/schema.md).)
```yaml
# stations/bench_1.yaml
instruments:
  psu:
    type: psu    # Provides dc_voltage OUTPUT
    catalog_ref: keysight_e36312a
  dmm:
    type: dmm             # Provides dc_voltage INPUT
    catalog_ref: keysight_34461a
```

**Match result:** bench_1 CAN test power_board ✓

## Tiered Matching

The matcher checks up to five tiers, controlled by `MatchDepth` (an enum naming how deep the match check should go):

1. **Function match** — Same `MeasurementFunction` (e.g., `dc_voltage`)
2. **Direction match** — Directions pair (OUTPUT↔INPUT, BIDIR satisfies both)
3. **Parameter range** — Instrument's range contains the required value (default depth)
4. **Accuracy** — Instrument accuracy ≤ required (condition-aware via [`SpecBand`](../reference/data/models.md), the value-plus-condition record)
5. **Resolution** — Instrument resolution ≥ required

Most use cases stop at range (tier 3). Use `MatchDepth.ACCURACY` or `MatchDepth.RESOLUTION` when you need tighter validation — for example, checking that a DMM's accuracy at a specific frequency band meets your part's requirements.

## Try It: Using the Matcher

### Python API

```python
from litmus.matching.service import (
    find_compatible_stations,
    check_station_compatibility,
)
from litmus.store import get_part

# Load part by id (looks up parts/<id>.yaml from the project root)
part = get_part("power_board")

# Find all compatible stations (takes the loaded Part object)
matches = find_compatible_stations(part)

for match in matches:
    if match.compatible:
        print(f"✓ {match.station_id} can test {part.id}")
    else:
        print(f"✗ {match.station_id} missing: {match.missing}")
```

### HTTP API

```bash
# Find compatible stations
curl "http://localhost:8000/api/match?part_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?part_id=power_board&station_id=bench_1"
```

## Hands-On Exercise

Create files to see matching in action:

**1. Create a part spec:**
```yaml
# parts/my_part.yaml
id: my_part
name: "My Test Part"

characteristics:
  output_voltage:
    function: dc_voltage
    direction: output
    unit: V
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}

  output_current:
    function: dc_current
    direction: output
    unit: A
    bands:
      - value: 0.5
        accuracy: {pct_reading: 10}
```

**2. Create two stations:**

```yaml
# stations/station_a.yaml — DMM only
id: station_a
name: "Station A - DMM only"
instruments:
  dmm:
    type: dmm
    mock: true
    catalog_ref: generic_dmm
```

```yaml
# stations/station_b.yaml — DMM + current clamp
id: station_b
name: "Station B - DMM + Clamp meter"
instruments:
  dmm:
    type: dmm
    mock: true
    catalog_ref: generic_dmm
  clamp:
    type: current_clamp
    mock: true
    catalog_ref: generic_current_clamp
```

**3. Run the matcher:**

Station A can measure dc_voltage but not dc_current → Missing capabilities.
Station B can measure both → Compatible.

## MeasurementFunction vs. Domain+SignalType

The old model used `domain: voltage` + `signal_types: [dc]`. The new model uses `function: dc_voltage`. This matters because:

| Old Model | Problem |
|-----------|---------|
| DMM: `domain: voltage, signal_types: [dc], direction: input` | |
| Scope: `domain: voltage, signal_types: [dc], direction: input` | Same capability! |

Both matched any "dc voltage input" requirement, even though they're fundamentally different instruments.

| New Model | No Confusion |
|-----------|-------------|
| DMM: `function: dc_voltage, direction: input` | Precision measurement |
| Scope: `function: waveform, direction: input` | Time-domain capture |

The scope's `waveform` function won't match a `dc_voltage` requirement.

## Handling Missing Capabilities

When matching fails, you get actionable information:

```python
result = check_station_compatibility("my_part", "station_a")

if result and not result["compatible"]:
    for cap in result["missing"]:
        print(f"Need: {cap['direction']} {cap['function']}")
```

`check_station_compatibility(part_id, station_id)` takes ID strings (not loaded objects) and returns a `dict | None`. The `missing` value is a list of dicts shaped `{characteristic, function, direction}`.

Output:
```
Need: INPUT dc_current
```

This tells you: add a current measurement instrument to test this part.

## Benefits of Capability Matching

1. **Automatic validation** — Can't accidentally run tests on wrong station
2. **Station flexibility** — Tests portable between compatible stations
3. **Clear requirements** — Know exactly what instruments you need
4. **Planning support** — Design stations for new parts
5. **Fine-grained** — DMM vs. scope vs. SMU distinguished automatically

## What You Learned

- How `MeasurementFunction` provides fine-grained capability identification
- The direction flip between parts and instruments
- Tiered matching: function → direction → range → accuracy → resolution
- Using the matcher API (Python, HTTP)
- Interpreting missing capability results

## Continue

Put it all together for production-ready testing.

← [Step 7: Real Instruments](07-real-instruments.md)  |  [Step 9: Production Ready →](09-production.md)
