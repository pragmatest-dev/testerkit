# Step 9: Production Ready

**Goal:** Build a complete production test suite with fixtures, sequences, and full traceability.

## What You'll Build

A production-ready test suite with:
- Pin-to-instrument mapping (fixtures)
- Ordered test execution (sequences)
- Full signal traceability

## Complete Project Structure

```
my_project/
├── products/                       # WHAT you're testing
│   └── power_board/
│       └── spec.yaml
├── stations/                       # WHERE you test
│   └── bench_1.yaml
├── fixtures/                       # HOW pins connect to instruments
│   └── power_board_fixture.yaml
├── sequences/                      # Test execution order
│   └── production_test.yaml
├── tests/                          # Test code + configuration
│   ├── config.yaml
│   ├── conftest.py
│   └── test_power_board.py
└── results/                        # Output (gitignored)
```

## The Fixture: Pin-to-Instrument Mapping

A fixture maps DUT pins to station instruments:

```yaml
# fixtures/power_board_fixture.yaml
fixture:
  id: power_board_fixture
  name: "Power Board Test Fixture"
  product_id: power_board

points:
  vin_supply:
    dut_pin: VIN              # From product spec
    instrument: psu           # From station config
    instrument_channel: "1"

  vout_measure:
    dut_pin: VOUT
    instrument: dmm

  gnd_supply:
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

## The pins Fixture

With a fixture config, you can access instruments via pin names:

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, pins):
    """Access instruments by DUT pin name."""
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()

    voltage = pins["VOUT"].measure_voltage()

    return voltage
```

Run with fixture config:
```bash
pytest tests/ \
  --station-config=stations/bench_1.yaml \
  --fixture-config=fixtures/power_board_fixture.yaml \
  --dut-serial=SN001
```

## Why Use pins Instead of instruments?

| `instruments["dmm"]` | `pins["VOUT"]` |
|---------------------|----------------|
| Station-centric | DUT-centric |
| "Use the DMM" | "Measure VOUT" |
| Changes if station changes | Stable across stations |
| No traceability | Full traceability |

The `pins` approach provides:
- **Abstraction** — Test code doesn't know which instrument measures VOUT
- **Portability** — Same test works on stations with different instruments
- **Traceability** — Measurements linked to DUT pins

## Test Sequences

Define test execution order:

```yaml
# sequences/production_test.yaml
sequence:
  id: power_board_production
  name: "Power Board Production Test"
  product_family: power_board
  test_phase: production
  required_fixture: power_board_fixture

steps:
  - name: verify_input
    test: test_power_board.test_input_voltage
    description: "Verify input power"

  - name: output_no_load
    test: test_power_board.test_output_voltage
    description: "Output at no load"
    skip_on: [verify_input]     # Skip if verify_input failed

  - name: output_loaded
    test: test_power_board.test_load_sweep
    description: "Output under load"
    skip_on: [output_no_load]
    retry:
      max_attempts: 2
```

## Sequence Features

### skip_on: Dependency-Based Skipping

```yaml
steps:
  - name: power_on
    test: test_power_board.test_power_on

  - name: measure_output
    test: test_power_board.test_output
    skip_on: [power_on]    # Skip if power_on failed
```

### retry: Per-Step Retry

```yaml
steps:
  - name: flaky_test
    test: test_power_board.test_margin
    retry:
      max_attempts: 3
      delay_seconds: 0.5
```

### dialog: Operator Prompts

```yaml
steps:
  - name: visual_inspection
    dialog:
      type: confirm
      message: "Verify LED is GREEN"
      title: "Visual Check"
```

## Complete Example

**products/power_board/spec.yaml:**
```yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

pins:
  VIN: {name: "J1.1", type: power}
  VOUT: {name: "J1.3", type: signal}
  GND: {name: "J1.2", type: ground}

characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    specs:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

**stations/bench_1.yaml:**
```yaml
station:
  id: bench_1
  name: "Production Bench 1"

instruments:
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
    mock_config: {voltage: 5.0}
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config: {voltage: 3.31}
```

**fixtures/power_board_fixture.yaml:**
```yaml
fixture:
  id: power_board_fixture
  product_id: power_board

points:
  vin_supply:
    dut_pin: VIN
    instrument: psu
  vout_measure:
    dut_pin: VOUT
    instrument: dmm
```

**tests/config.yaml:**
```yaml
test_output_voltage:
  vectors:
    expand: product
    load_percent: [0, 50, 100]
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      units: V
      spec_ref: "output_voltage @ tolerance=5%"
```

**tests/test_power_board.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(context, pins):
    """Verify input voltage."""
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    return pins["VIN"].measure_voltage()

@litmus_test
def test_output_voltage(context, pins):
    """Verify output at various loads."""
    return pins["VOUT"].measure_voltage()
```

## Running Production Tests

```bash
pytest tests/ \
  --station-config=stations/bench_1.yaml \
  --fixture-config=fixtures/power_board_fixture.yaml \
  --dut-serial=SN12345 \
  --operator="Jane Doe" \
  -v
```

With simulation:
```bash
pytest tests/ \
  --station-config=stations/bench_1.yaml \
  --fixture-config=fixtures/power_board_fixture.yaml \
  --mock-instruments \
  --dut-serial=SIM001 \
  -v
```

## Viewing Results

### CLI

```bash
litmus runs                    # List recent runs
litmus show <run_id>           # Show run details
```

### Operator UI

```bash
litmus serve
# Open http://localhost:8000
```

### Programmatic

```python
import pyarrow.parquet as pq

# Read measurements
table = pq.read_table("results/measurements")
for row in table.to_pylist():
    print(f"{row['measurement_name']}: {row['value']} {row['units']}")
```

## Full Traceability

Every measurement now traces back through the chain:

```
Measurement: output_voltage = 3.31V PASS
    ↓
DUT Pin: VOUT (from fixture)
    ↓
Fixture Point: vout_measure
    ↓
Instrument: dmm
    ↓
Station: bench_1
    ↓
Limit: 3.135-3.465V
    ↓
Spec: output_voltage @ tolerance=5%
```

## What You've Built

| Component | File | Purpose |
|-----------|------|---------|
| Product spec | `products/power_board/spec.yaml` | What to test |
| Station | `stations/bench_1.yaml` | Where to test |
| Fixture | `fixtures/power_board_fixture.yaml` | Pin-to-instrument mapping |
| Sequence | `sequences/production_test.yaml` | Test order |
| Config | `tests/config.yaml` | Vectors and limits |
| Tests | `tests/test_power_board.py` | Test code |

## What You Learned

- Fixture configuration for pin-to-instrument mapping
- The `pins` fixture for DUT-centric testing
- Test sequences for ordered execution
- Full traceability from spec to measurement

## Congratulations!

You've completed the tutorial. You now have a foundation for production hardware testing with Litmus.

## Next Steps

- [API Reference](../reference/api.md) — MCP tools and HTTP endpoints
- [Configuration Reference](../reference/configuration.md) — All YAML options
- [pytest Plugin Guide](../reference/pytest-plugin.md) — Full decorator parameters
- [Test Harness Integration](../integration/harness.md) — Advanced patterns
