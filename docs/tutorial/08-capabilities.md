# Step 8: Capability Matching

**Goal:** Understand how TesterKit matches parts to compatible stations.

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

The rule: **directions flip** between parts and instruments.

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

The matcher checks each requirement in tiers:

1. **Function match** — Same `MeasurementFunction` (e.g., `dc_voltage`)
2. **Direction match** — Directions pair (OUTPUT↔INPUT, BIDIR satisfies both)
3. **Parameter range** — Instrument's range contains the required value

The matcher functions and the `/api/match` endpoint check through range. Two finer tiers — **accuracy** (instrument accuracy ≤ required, checked per condition) and **resolution** (instrument resolution ≥ required) — are part of the capability model and come into play when recommending instruments from the catalog.

## Try It: Using the Matcher

### Python API

```python
from testerkit.matching.service import (
    find_compatible_stations,
    check_station_compatibility,
)
from testerkit.store import get_part

# Load the power_board part spec
part = get_part("power_board")

# Find every station that can test it
matches = find_compatible_stations(part)

for match in matches:
    if match.compatible:
        print(f"✓ {match.station_id} can test {part.id}")
    else:
        print(f"✗ {match.station_id} missing: {match.match_result.missing}")
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

## Functions Are Specific

Each capability names a specific `function`, so similar-looking instruments don't get confused. A DMM declares `function: dc_voltage`; a scope declares `function: waveform`. Both are "voltage, input" instruments, but a precision DC-voltage requirement matches only the DMM — the scope's `waveform` function won't match a `dc_voltage` requirement, and vice versa.

## Handling Missing Capabilities

When matching fails, you get actionable information:

```python
result = check_station_compatibility("my_part", "station_a")

if result and not result["compatible"]:
    for cap in result["missing"]:
        print(f"Need: {cap['direction']} {cap['function']}")
```

`check_station_compatibility(part_id, station_id)` takes the part and station IDs (not loaded objects). If the station can't test the part, each entry under `missing` names the unmet requirement — its characteristic, function, and direction.

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
