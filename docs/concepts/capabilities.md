# Capabilities

**Capabilities** describe what instruments can do and what products need. The capability system enables automatic matching between products and stations.

## What Is a Capability?

A capability has three core dimensions:

| Dimension | Values | Description |
|-----------|--------|-------------|
| `direction` | input, output, bidir | Does it measure or source? |
| `domain` | voltage, current, resistance, frequency, time, digital | Physical quantity |
| `signal_types` | dc, ac, pulse, sine, square, pwm | Signal characteristics |

### Example: DMM Capabilities

```yaml
# litmus/instruments/library/dmm.yaml
name: Digital Multimeter
type: dmm

capabilities:
  - name: voltage_dc
    direction: input      # Instrument measures (receives signal)
    domain: voltage
    signal_types: [dc]

  - name: current_dc
    direction: input
    domain: current
    signal_types: [dc]
```

### Example: Power Supply Capabilities

```yaml
# litmus/instruments/library/power_supply.yaml
name: DC Power Supply
type: power_supply

capabilities:
  - name: voltage_dc
    direction: output     # Instrument sources (provides signal)
    domain: voltage
    signal_types: [dc]

  - name: current_dc
    direction: output
    domain: current
    signal_types: [dc]
```

## Direction Flip

The key insight is that **directions flip** between products and instruments:

```
Product Characteristic          Required Instrument Capability
─────────────────────          ────────────────────────────────
output_voltage (OUTPUT)   →    voltage_dc (INPUT) — need to measure
input_voltage (INPUT)     →    voltage_dc (OUTPUT) — need to source
```

### Why This Works

When a product **outputs** voltage, the instrument needs to **input** (measure) that voltage.

When a product **inputs** power, the instrument needs to **output** (source) that power.

```
Product (DUT)                    Instrument
────────────                     ──────────

output_voltage ────signal───►    DMM (measures)
   (OUTPUT)                      (INPUT)

                  ◄───power────  PSU (sources)
input_voltage                    (OUTPUT)
   (INPUT)
```

## Capability Matching

The matcher determines whether a station can test a product:

```python
from litmus.matching.service import find_compatible_stations, load_product_by_id

product = load_product_by_id("power_board")
matches = find_compatible_stations(product)

for match in matches:
    print(f"{match.station_id}: {'Compatible' if match.compatible else 'Missing capabilities'}")
```

### Matching Algorithm

1. **Extract requirements** from product characteristics
2. **Flip directions** (DUT output → instrument input)
3. **Compare** against station capabilities
4. **Report** match or list missing capabilities

```python
# Product characteristic
char = product.characteristics["output_voltage"]
# direction: OUTPUT, domain: VOLTAGE, signal_types: [DC]

# Convert to requirement
req = char.to_capability_requirement()
# direction: INPUT, domain: VOLTAGE, signal_types: [DC]
# (direction flipped!)

# Check station
station_caps = station.get_capabilities()
# Station DMM provides: direction: INPUT, domain: VOLTAGE, signal_types: [DC]
# → MATCH!
```

## Capability Dimensions

### Direction

| Value | Instrument Behavior | Example |
|-------|---------------------|---------|
| `input` | Measures/receives signal | DMM measuring voltage |
| `output` | Sources/provides signal | PSU outputting voltage |
| `bidir` | Both measures and sources | SMU (source-measure unit) |

### Domain

| Value | Physical Quantity |
|-------|-------------------|
| `voltage` | Electrical potential (V) |
| `current` | Electrical current (A) |
| `resistance` | Resistance (Ω) |
| `power` | Power (W) |
| `frequency` | Frequency (Hz) |
| `time` | Time measurements (s) |
| `digital` | Logic levels, protocols |
| `temperature` | Temperature (°C) |

### Signal Types

| Value | Description |
|-------|-------------|
| `dc` | Direct current / static |
| `ac` | Alternating current |
| `pulse` | Pulsed signals |
| `sine` | Sinusoidal waveforms |
| `square` | Square waves |
| `pwm` | Pulse-width modulation |
| `transient` | Transient responses |

## Additional Capability Fields

Capabilities can include performance specifications:

```yaml
capabilities:
  - name: voltage_dc
    direction: input
    domain: voltage
    signal_types: [dc]
    channels:
      count: 4
      simultaneous: true
      naming: "CH{n}"
    range:
      min: 0.001
      max: 1000
      units: V
    resolution: 0.000001
    accuracy_pct: 0.02
```

### Channel Specification

For multi-channel instruments:

```yaml
channels:
  count: 4              # Number of channels
  simultaneous: true    # Can measure all at once
  naming: "CH{n}"       # Pattern: CH1, CH2, CH3, CH4
  # OR
  labels: ["A", "B"]    # Explicit channel names
```

### Range and Accuracy

```yaml
range:
  min: 0.001            # Minimum measurable/sourceable value
  max: 1000             # Maximum value
  units: V              # Units
resolution: 0.000001    # Smallest distinguishable change
accuracy_pct: 0.02      # Accuracy as percentage
```

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

### MCP Tools

```
find_compatible_stations(product_id="power_board")
check_station_compatibility(product_id="power_board", station_id="bench_1")
```

## Instrument Library

Capabilities are defined in the instrument library (`litmus/instruments/library/`):

```
instruments/library/
├── dmm.yaml           # Digital multimeter
├── scope.yaml         # Oscilloscope
├── power_supply.yaml  # DC power supply
├── eload.yaml         # Electronic load
├── funcgen.yaml       # Function generator
└── smu.yaml           # Source-measure unit
```

Each file defines the capabilities that instrument type provides.

## Custom Instruments

When adding custom instruments, define their capabilities:

```yaml
# my_custom_instrument.yaml
name: Custom Temperature Logger
type: temp_logger

capabilities:
  - name: temperature_rtd
    direction: input
    domain: temperature
    signal_types: [dc]
    channels:
      count: 8
      naming: "T{n}"
    range:
      min: -200
      max: 850
      units: "°C"
```

## Next Steps

- [Fixtures](fixtures.md) — Mapping DUT pins to instruments
- [Architecture](architecture.md) — System data flow
- [Adding Instruments](../guides/adding-instruments.md) — Creating custom drivers
