# Step 7: Production Ready

**Goal:** Build a complete production test suite with full traceability.

## What You'll Build

A test suite with:
- Product spec linked to tests
- Pin-to-instrument mapping via fixtures
- Ordered test sequences
- Full measurement traceability
- Results stored for analysis

## Complete Project Structure

```
my_project/
├── specs/
│   └── power_board.yaml        # Product specification
├── stations/
│   ├── bench_1.yaml            # Production station
│   └── ci_station.yaml         # CI/CD station
├── fixtures/
│   └── power_board_fixture.yaml # Pin mapping
├── sequences/
│   └── production_test.yaml    # Test sequence
├── tests/
│   ├── config.yaml             # Test limits
│   ├── conftest.py             # pytest config
│   └── test_power_board.py     # Test code
└── results/                    # Output (gitignored)
```

## Step 1: Define the Product

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"
  revision: "A"
  datasheet: "docs/power_board_datasheet.pdf"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal
  GND:
    name: "J1.2"
    net: "GND"
    type: ground

characteristics:
  input_voltage:
    direction: input
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VIN]
    conditions:
      - nominal: 5.0
        tolerance_pct: 10

  output_voltage:
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5

test_requirements:
  verify_output:
    characteristic_ref: output_voltage
    guardband_pct: 10
    priority: 1
```

## Step 2: Configure the Station

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A, Position 1"

instruments:
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"

  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"

supported_phases:
  - production
  - debug
```

## Step 3: Create the Fixture

Map DUT pins to instruments:

```yaml
# fixtures/power_board_fixture.yaml
fixture:
  id: power_board_fixture
  name: "Power Board Test Fixture"
  product_id: power_board

points:
  VIN:
    dut_pin: VIN
    instrument: psu
    instrument_channel: "1"
  VOUT:
    dut_pin: VOUT
    instrument: dmm
  GND:
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

## Step 4: Define Test Sequence

```yaml
# sequences/production_test.yaml
sequence:
  id: power_board_production
  name: "Power Board Production Test"
  product_family: power_board
  test_phase: production
  required_fixture: power_board_fixture

steps:
  - name: input_power
    test: test_power_board.test_input_voltage
    description: "Verify input voltage applied correctly"

  - name: output_voltage
    test: test_power_board.test_output_voltage
    description: "Verify regulated output"
    retry:
      max_attempts: 2
      delay_seconds: 0.5
```

## Step 5: Configure Test Limits

```yaml
# tests/config.yaml
test_input_voltage:
  limits:
    input_voltage:
      low: 4.5
      high: 5.5
      nominal: 5.0
      units: V
      spec_ref: "input_voltage @ tolerance_pct=10"

test_output_voltage:
  vectors:
    expand: product
    load_percent: [0, 50, 100]
  limits:
    output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ guardband=10%"
```

## Step 6: Write the Tests

```python
# tests/test_power_board.py
from litmus.execution import litmus_test

@litmus_test
def test_input_voltage(vector, pins):
    """Verify input voltage is applied correctly."""
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()

    # Allow settling
    import time
    time.sleep(0.1)

    # Verify applied voltage
    # (Some PSUs can measure their own output)
    return pins["VIN"].measure_output_voltage()


@litmus_test
def test_output_voltage(vector, pins):
    """Verify output voltage at various loads."""
    # Input already powered from previous test

    # Configure load (if electronic load available)
    load_pct = vector.get("load_percent", 0)
    if "LOAD" in pins:
        pins["LOAD"].set_current(load_pct / 100.0)

    # Measure output
    voltage = pins["VOUT"].measure_voltage()

    return voltage
```

## Step 7: pytest Configuration

```python
# tests/conftest.py
import pytest

def pytest_addoption(parser):
    """Add Litmus command-line options."""
    parser.addoption(
        "--dut-serial",
        action="store",
        required=True,
        help="DUT serial number"
    )
    parser.addoption(
        "--station",
        action="store",
        default="default",
        help="Station ID"
    )
    parser.addoption(
        "--simulate",
        action="store_true",
        help="Run in simulation mode"
    )


@pytest.fixture
def dut_serial(request):
    """DUT serial number from command line."""
    return request.config.getoption("--dut-serial")


@pytest.fixture
def station_id(request):
    """Station ID from command line."""
    return request.config.getoption("--station")


@pytest.fixture
def simulate(request):
    """Whether to simulate instruments."""
    return request.config.getoption("--simulate")
```

## Running Production Tests

**With real hardware:**
```bash
pytest tests/ \
  --station=bench_1 \
  --dut-serial=SN12345 \
  --test-phase=production \
  -v
```

**In simulation:**
```bash
pytest tests/ \
  --station=bench_1 \
  --simulate \
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
from litmus import LitmusClient

client = LitmusClient()
runs = client.list_runs(limit=10)
for run in runs:
    print(f"{run['test_run_id'][:8]}: {run['outcome']}")
```

## What You've Built

A complete test system with:

| Component | Purpose |
|-----------|---------|
| Product spec | Documents what to test |
| Station config | Defines where to test |
| Fixture | Maps pins to instruments |
| Test sequence | Orders the tests |
| Test config | Configures limits and vectors |
| Test code | Implements the tests |
| Results | Stores measurements |

## Data Flow

```
Product Spec → Required Capabilities → Station Match
     ↓
Fixture → Pin Mapping → Instruments
     ↓
Test Config → Limits → Measurements
     ↓
Results → Parquet → Analysis
```

## Next Steps

You've completed the tutorial! Here's where to go next:

- [API Reference](../reference/api.md) — MCP tools and HTTP endpoints
- [Configuration Reference](../reference/configuration.md) — All YAML options
- [Writing Tests Guide](../guides/writing-tests.md) — Advanced patterns
- [Integration](../integration/overview.md) — Adopt Litmus with existing tests

## Congratulations!

You now have a foundation for production hardware testing with Litmus. The system scales from simple bench tests to complex multi-station production environments.
