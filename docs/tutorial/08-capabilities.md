# Step 8: Capability Matching

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

### Example

A power converter:
- **input_voltage** (direction: INPUT) → DUT receives power → need PSU (OUTPUT)
- **output_voltage** (direction: OUTPUT) → DUT provides voltage → need DMM (INPUT)

## How Matching Works

**Product spec defines requirements:**
```yaml
# products/power_board/spec.yaml
characteristics:
  input_voltage:
    direction: input       # DUT needs input voltage
    domain: voltage
    signal_types: [dc]

  output_voltage:
    direction: output      # DUT outputs voltage
    domain: voltage
    signal_types: [dc]
```

**Station provides capabilities:**
```yaml
# stations/bench_1.yaml
instruments:
  psu:
    type: power_supply    # Provides OUTPUT voltage
  dmm:
    type: dmm             # Provides INPUT voltage
```

**Match result:** bench_1 CAN test power_board ✓

## Try It: Using the Matcher

### Python API

```python
from litmus.matching.service import (
    find_compatible_stations,
    check_station_compatibility,
    load_product_by_id,
)

# Load product
product = load_product_by_id("power_board")

# Find all compatible stations
matches = find_compatible_stations(product)

for match in matches:
    if match.compatible:
        print(f"✓ {match.station_id} can test {product.id}")
    else:
        print(f"✗ {match.station_id} missing: {match.missing_capabilities}")
```

### CLI (via MCP)

```bash
# In a Python session or via MCP
litmus_match(product_id="power_board")
```

### HTTP API

```bash
# Find compatible stations
curl "http://localhost:8000/api/match?product_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?product_id=power_board&station_id=bench_1"
```

## Hands-On Exercise

Create files to see matching in action:

**1. Create a product spec:**
```yaml
# products/my_product/spec.yaml
product:
  id: my_product
  name: "My Test Product"

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V

  output_current:
    direction: output
    domain: current
    signal_types: [dc]
    units: A
```

**2. Create two stations:**

```yaml
# stations/station_a.yaml
station:
  id: station_a
  name: "Station A - DMM only"

instruments:
  dmm:
    type: dmm
    resource: "SIM::DMM"
```

```yaml
# stations/station_b.yaml
station:
  id: station_b
  name: "Station B - DMM + Clamp meter"

instruments:
  dmm:
    type: dmm
    resource: "SIM::DMM"
  clamp:
    type: current_clamp
    resource: "SIM::CLAMP"
```

**3. Run the matcher:**

```python
from litmus.matching.service import find_compatible_stations, load_product_by_id

product = load_product_by_id("my_product")
matches = find_compatible_stations(product)

for m in matches:
    print(f"{m.station_id}: compatible={m.compatible}")
    if not m.compatible:
        print(f"  Missing: {[c.domain for c in m.missing_capabilities]}")
```

Expected output:
```
station_a: compatible=False
  Missing: ['current']
station_b: compatible=True
```

Station A can't test the product because it lacks current measurement.

## Capability Dimensions

The matcher compares these dimensions:

| Dimension | Values | Example |
|-----------|--------|---------|
| Direction | input, output, bidir | DMM is input (measures) |
| Domain | voltage, current, resistance, frequency, time, digital | DMM measures voltage |
| Signal Types | dc, ac, pulse, sine, square, pwm | DC voltage measurement |

All dimensions must match for compatibility.

## Handling Missing Capabilities

When matching fails, you get actionable information:

```python
result = check_station_compatibility("my_product", "station_a")

if not result.compatible:
    for cap in result.missing_capabilities:
        print(f"Need: {cap.direction} {cap.domain}")
        print(f"For characteristic: {cap.characteristic_name}")
```

Output:
```
Need: INPUT current
For characteristic: output_current
```

This tells you: add a current measurement instrument to test this product.

## Benefits of Capability Matching

1. **Automatic validation** — Can't accidentally run tests on wrong station
2. **Station flexibility** — Tests portable between compatible stations
3. **Clear requirements** — Know exactly what instruments you need
4. **Planning support** — Design stations for new products

## What You Learned

- How capabilities enable product-station matching
- The direction flip between products and instruments
- Using the matcher API (Python, CLI, HTTP)
- Interpreting missing capability results

## Next Step

Put it all together for production-ready testing.

[Step 9: Production Ready →](09-production.md)
