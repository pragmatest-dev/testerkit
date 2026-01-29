# Core Concepts

Litmus organizes hardware testing around four key concepts: **Products**, **Stations**, **Capabilities**, and **Matching**.

## Products

A **Product** is what you're testing — a PCB, module, or device. Product specs define:

- **Characteristics** — Electrical properties (voltage rails, currents, signals)
- **Test Requirements** — Which characteristics to test and how

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"
  revision: "A"

characteristics:
  input_voltage:
    direction: input       # DUT receives this
    domain: voltage
    signal_types: [dc]
    units: V
    conditions:
      - nominal: 5.0
        tolerance_pct: 10

  output_voltage:
    direction: output      # DUT provides this
    domain: voltage
    signal_types: [dc]
    units: V
    conditions:
      - nominal: 3.3
        tolerance_pct: 5

test_requirements:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10      # Tighten limits by 10%
    priority: 1
```

### Direction Matters

The `direction` field describes the DUT's perspective:

| Direction | Meaning | Instrument Needs |
|-----------|---------|------------------|
| `input` | DUT receives power/signal | Instrument must **source** |
| `output` | DUT provides power/signal | Instrument must **measure** |

## Stations

A **Station** is where you test — a bench with instruments. Station configs define:

- **Instruments** — What's connected and how to reach it
- **Capabilities** — What the station can measure/source

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
```

### Simulated Mode

For development without hardware:

```yaml
instruments:
  dmm:
    type: dmm
    resource: "SIM::DMM"
    simulated: true
    sim_values:
      voltage: 3.31
      current: 0.1
```

## Capabilities

**Capabilities** describe what instruments can do. They're defined in the instrument library (`litmus/instruments/library/`).

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

### Capability Dimensions

| Dimension | Values | Description |
|-----------|--------|-------------|
| `direction` | input, output, bidir | Does it measure or source? |
| `domain` | voltage, current, resistance, frequency, time, digital | Physical quantity |
| `signal_types` | dc, ac, pulse, sine, square, pwm | Signal characteristics |

## Matching

**Matching** connects products to stations. The matcher answers: "Can this station test this product?"

### How Matching Works

1. **Extract Requirements** — Product characteristics become capability requirements
2. **Direction Flip** — DUT output → Instrument input (measure), DUT input → Instrument output (source)
3. **Find Matches** — Compare requirements against station capabilities

```
Product Characteristic          Required Capability
─────────────────────          ───────────────────
output_voltage (OUTPUT)   →    voltage_dc (INPUT) — need to measure
input_voltage (INPUT)     →    voltage_dc (OUTPUT) — need to source
```

### Using the Matcher

```python
from litmus.matching.service import find_compatible_stations, load_product_by_id

product = load_product_by_id("power_board")
matches = find_compatible_stations(product)

for match in matches:
    print(f"{match.station_id}: {'Compatible' if match.compatible else 'Missing capabilities'}")
```

### CLI

```bash
litmus setup show  # Lists available MCP tools including matching
```

### HTTP API

```bash
# Find all compatible stations
curl http://localhost:8000/api/match?product_id=power_board

# Check specific station
curl http://localhost:8000/api/match?product_id=power_board&station_id=bench_1
```

## Data Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Product   │     │   Station   │     │ Instrument  │
│    Spec     │     │   Config    │     │   Library   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────┐
│              Capability Matcher                      │
│  "Can bench_1 test power_board?"                    │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   pytest    │────▶│   Results   │────▶│   Parquet   │
│   Runner    │     │   (TestRun) │     │   Storage   │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Next Steps

- [Configuration Reference](configuration.md) — Detailed YAML schemas
- [pytest Plugin Guide](pytest-plugin.md) — Writing tests with `@litmus_test`
