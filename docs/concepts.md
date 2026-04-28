# Core Concepts

Litmus organizes hardware testing around five key concepts: **Products**, **Pins & Channels**, **Stations**, **Capabilities**, and **Matching**.

## Products

A **Product** is what you're testing — a PCB, module, or device. Product specs define:

- **Characteristics** — Electrical properties (voltage rails, currents, signals)
- **Test Requirements** — Which characteristics to test and how

```yaml
# products/power_board.yaml
id: power_board
name: "5V to 3.3V Converter"
revision: "A"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal

characteristics:
  input_voltage:
    direction: input       # DUT receives this
    function: dc_voltage
    units: V
    pins: [VIN]            # Which pin this characteristic applies to
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}

  output_voltage:
    direction: output      # DUT provides this
    function: dc_voltage
    units: V
    pins: [VOUT]
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}

bands:
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
| `bidir` | DUT both receives and provides | Instrument must do both (e.g., SMU) |

## Pins & Channels

**Pins** represent physical connection points on the DUT (connectors, test points, pads). **Channels** are logical measurement/source points on instruments.

### Pins

Define the physical interface of your product:

```yaml
pins:
  VIN:
    name: "J1.1"           # Physical designator
    net: "VIN_5V"          # Schematic net name
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal
  SDA:
    name: "J2.1"
    net: "I2C_SDA"
    type: signal
```

Pin types: `signal`, `power`, `ground`, `nc` (no connect)

### Multiple Characteristics Per Pin

A single pin can have multiple characteristics (e.g., DC voltage and AC ripple):

```yaml
pins:
  VOUT:
    name: "J1.3"
    type: signal

characteristics:
  output_voltage:
    pins: [VOUT]
    direction: output
    function: dc_voltage
    units: V

  output_ripple:
    pins: [VOUT]           # Same pin, different measurement
    direction: output
    function: ac_voltage
    units: mV
```

### Signal Groups (Buses)

Group related signals for protocols like I2C, SPI, or UART:

```yaml
pins:
  SDA:
    name: "J2.1"
    type: signal
  SCL:
    name: "J2.2"
    type: signal

signal_groups:
  i2c_main:
    protocol: i2c
    signals:
      - pin: SDA
        role: data
      - pin: SCL
        role: clock
    parameters:
      frequency: 400000
```

### Minimal Spec

The simplest spec that works:

```yaml
id: minimal_board
name: "Minimal Example"

pins:
  VOUT:
    name: "J1.1"

characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    bands:
      - value: 5.0
        accuracy: {pct_reading: 10}
```

## Stations

A **Station** is where you test — a bench with instruments. Station configs define:

- **Instruments** — What's connected and how to reach it
- **Capabilities** — What the station can measure/source

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Production Bench 1"
location: "Lab A"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
```

### Mock Mode

For development without hardware, use `--mock-instruments`:

```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

Configure mock values in the station:

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
      current: 0.1
```

## Fixtures (Optional)

**Fixtures** define pin-to-instrument mappings, bridging product pins to station instruments. They're optional — you can test without them.

### When to Use Fixtures

| Approach | When to Use |
|----------|-------------|
| **--mock-instruments** | Development, CI, unit tests |
| **Direct fixtures** | Simple benches, quick prototyping |
| **Pin mapping (fixtures)** | Production, complex routing, compliance |

### Fixture Configuration

```yaml
# fixtures/power_board_fixture.yaml
id: power_board_fixture
product_id: power_board

connections:
  VIN:
    dut_pin: VIN
    instrument: psu
    instrument_channel: "1"
  VOUT:
    dut_pin: VOUT
    instrument: dmm
```

### Using the `pins` Fixture

With a fixture configured, tests can access instruments by DUT pin name:

```python
def test_output(pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    voltage = pins["VOUT"].measure_voltage()
    assert float(voltage) > 3.0
```

This decouples test code from station wiring — the same test runs on different stations with different fixtures.

## Capabilities

**Capabilities** describe what instruments can do. They're defined in catalog entries under `catalog/`.

```yaml
# catalog/generic/dmm.yaml
id: generic_dmm
name: Digital Multimeter
type: dmm

capabilities:
  - name: voltage_dc
    direction: input      # Instrument measures (receives signal)
    function: dc_voltage

  - name: current_dc
    direction: input
    function: dc_current
```

### Capability Dimensions

| Dimension | Values | Description |
|-----------|--------|-------------|
| `direction` | input, output, bidir | Does it measure or source? |
| `function` | dc_voltage, ac_voltage, dc_current, ac_current, resistance, frequency, ... | Measurement function |

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
from litmus.matching.service import find_compatible_stations

matches = find_compatible_stations(product_id="power_board")

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

## Spec-Driven Testing

The **ProductContext** bridges product specs and test execution, enabling:
- Automatic limit derivation from characteristics
- Channel traceability in measurements
- Guardband application for manufacturing margin

### Using ProductContext

```python
from litmus.products import ProductContext

# Load spec
spec = ProductContext.from_file("products/power_board.yaml")

# Get limit for characteristic at conditions
limit = spec.get_limit("output_voltage", temperature=25, load=0.1)
# Returns: Limit(low=3.135, high=3.465, spec_ref="Section 7.2 @ ...")

# Get pin info for traceability
pin_info = spec.get_pin_info("output_voltage")
# Returns: {dut_pin: "J1.3", net: "VOUT_3V3", ...}
```

### In pytest-native tests

The `verify` fixture is the pytest-native interface — limits resolve from the active `product_context` automatically. Load a spec with `--product=<id>` (looks up `products/<id>.yaml`) or `--spec=<path>` (explicit path):

```python
import pytest

@pytest.mark.parametrize("load", [0.1, 1.0])
@pytest.mark.parametrize("temperature", [25, 85])
def test_output_voltage(temperature, load, context, dmm, verify, chamber, eload):
    chamber.set_temperature(temperature)
    eload.set_current(load)
    verify("output_voltage", dmm.measure_dc_voltage())
```

`verify(name, v)` resolves the limit at current conditions, records the measurement with pin + `spec_ref` traceability, and raises `AssertionError` on fail. See the [spec-driven testing guide](guides/spec-driven-testing.md) for details.

## Next Steps

- [Configuration Reference](reference/configuration.md) — Detailed YAML schemas
- [pytest-native reference](reference/pytest-native.md) — the three-fixture card (`context` / `verify` / `logger`)
- [Writing Tests](guides/writing-tests.md) — end-to-end patterns
