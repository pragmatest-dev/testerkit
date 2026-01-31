# Step 6: Capability Matching

**Goal:** Understand how Litmus matches products to compatible stations.

## The Problem

You have:
- Multiple products with different test requirements
- Multiple stations with different instruments

How do you know which station can test which product?

## The Solution: Capabilities

Every product characteristic implies a required capability:

```
Product: output_voltage (direction: OUTPUT)
         ↓
Required: voltage measurement capability (direction: INPUT)
         ↓
Station: DMM provides voltage measurement
         ↓
Match!
```

## Direction Flip

The key insight: **directions flip** between products and instruments.

| Product Direction | Instrument Direction | Why |
|-------------------|---------------------|-----|
| OUTPUT (DUT provides) | INPUT (measure) | Need to measure what DUT outputs |
| INPUT (DUT receives) | OUTPUT (source) | Need to source what DUT needs |

## Example: Power Converter

**Product spec:**
```yaml
# specs/power_board.yaml
characteristics:
  input_voltage:
    direction: input       # DUT receives power
    domain: voltage
    signal_types: [dc]

  output_voltage:
    direction: output      # DUT provides regulated voltage
    domain: voltage
    signal_types: [dc]
```

**Required capabilities:**
```
input_voltage (INPUT)  → Need OUTPUT capability (power supply)
output_voltage (OUTPUT) → Need INPUT capability (DMM)
```

**Station:**
```yaml
# stations/bench_1.yaml
instruments:
  psu:
    type: power_supply    # Provides OUTPUT voltage capability
  dmm:
    type: dmm             # Provides INPUT voltage capability
```

**Result:** bench_1 CAN test power_board ✓

## Using the Matcher

### Python API

```python
from litmus.matching.service import find_compatible_stations, load_product_by_id

product = load_product_by_id("power_board")
matches = find_compatible_stations(product)

for match in matches:
    if match.compatible:
        print(f"✓ {match.station_id} can test {product.id}")
    else:
        print(f"✗ {match.station_id} missing: {match.missing_capabilities}")
```

### HTTP API

```bash
# Find all compatible stations
curl "http://localhost:8000/api/match?product_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?product_id=power_board&station_id=bench_1"
```

### MCP Tools

```
find_compatible_stations(product_id="power_board")
check_station_compatibility(product_id="power_board", station_id="bench_1")
```

## What Gets Compared

The matcher compares:

1. **Direction** (flipped)
2. **Domain** (voltage, current, etc.)
3. **Signal types** (dc, ac, etc.)

```python
# Product characteristic
char = product.characteristics["output_voltage"]
# direction: OUTPUT, domain: VOLTAGE, signal_types: [DC]

# Converted to requirement
req = char.to_capability_requirement()
# direction: INPUT, domain: VOLTAGE, signal_types: [DC]

# Station instrument provides
cap = station.instruments["dmm"].capabilities[0]
# direction: INPUT, domain: VOLTAGE, signal_types: [DC]

# Match!
```

## Capability Dimensions

| Dimension | Values |
|-----------|--------|
| Direction | input, output, bidir |
| Domain | voltage, current, resistance, frequency, time, digital |
| Signal types | dc, ac, pulse, sine, square, pwm |

## Handling Missing Capabilities

When a station can't test a product, the matcher tells you why:

```python
result = check_station_compatibility(product, station)
if not result.compatible:
    for missing in result.missing_capabilities:
        print(f"Missing: {missing.domain} {missing.direction}")
        print(f"  Required for: {missing.characteristic_name}")
```

Example output:
```
Missing: current INPUT
  Required for: output_current
```

This tells you: the station needs a current measurement capability to test the product's output current characteristic.

## Selecting a Station

In your test configuration:

```yaml
# tests/config.yaml
test_power_board:
  station: bench_1  # Explicit station selection
```

Or let the matcher suggest:

```python
from litmus.matching.service import find_compatible_stations

matches = find_compatible_stations(product)
compatible_stations = [m.station_id for m in matches if m.compatible]
```

## Benefits of Capability Matching

1. **Automatic validation** — Can't accidentally run tests on wrong station
2. **Station flexibility** — Tests portable between compatible stations
3. **Clear requirements** — Know exactly what instruments you need
4. **Planning support** — Suggest instruments for new products

## Real-World Workflow

1. **Define product spec** with characteristics
2. **Run matcher** to find compatible stations
3. **If no match:** See what's missing, add instruments
4. **Select station** for test run
5. **Test executes** with correct instruments

## What You Learned

- How capabilities enable product-station matching
- The direction flip between products and instruments
- Using the matcher API
- Interpreting missing capability results

## Next Step

Put it all together for production-ready testing.

[Step 7: Production Ready →](07-production.md)
