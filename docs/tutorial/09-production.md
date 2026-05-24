# Step 9: Production Ready

**Goal:** Build a complete production test class with fixtures, sidecar configuration, and full traceability.

## What You'll Build

A production-ready test class with:
- Pin-to-instrument mapping (fixtures)
- Ordered test execution (pytest class methods, in definition order)
- Per-test limits, mocks, sweeps, and retries (sidecar YAML)
- Full signal traceability

## Complete Project Structure

```
my_project/
├── products/                       # WHAT you're testing
│   └── power_board.yaml
├── stations/                       # WHERE you test
│   └── bench_1.yaml
├── fixtures/                       # HOW pins connect to instruments
│   └── power_board_fixture.yaml
├── tests/                          # Test code + sidecar
│   ├── conftest.py
│   ├── test_power_board.py         # Test class — execution order = method order
│   └── test_power_board.yaml       # Sidecar — limits, sweeps, mocks per method
└── results/                        # Output (gitignored)
```

## The Fixture: Pin-to-Instrument Mapping

A fixture maps DUT pins to station instruments:

```yaml
# fixtures/power_board_fixture.yaml
id: power_board_fixture
name: "Power Board Test Fixture"
product_id: power_board

connections:
  vin_supply:
    name: vin_supply          # Required — matches the dict key
    dut_pin: VIN              # From product spec
    instrument: psu           # From station config
    instrument_channel: "1"

  vout_measure:
    name: vout_measure
    dut_pin: VOUT
    instrument: dmm

  gnd_supply:
    name: gnd_supply
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

## The pins Fixture

With a fixture config, you can access instruments via pin names. The [`pins`](../reference/pytest/fixtures.md#pins-session) *fixture* is a dict keyed by product-pin name returning the instrument routed to that pin by the active fixture YAML — distinct from the `pins:` block in the product YAML, which declares the pin set itself ([concepts/products](../concepts/configuration/products.md)):

```python
def test_output_voltage(pins, logger):
    """Access instruments by DUT pin name."""
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()

    voltage = pins["VOUT"].measure_dc_voltage()

    logger.measure("output_voltage", voltage)
```

Run with fixture config:
```bash
pytest tests/ \
  --station=stations/bench_1.yaml \
  --fixture=fixtures/power_board_fixture.yaml \
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

## The Production Test Class

A test class groups related test methods that run in definition order. Each method gets its own row in the run, with its own limits, sweeps, mocks, and retries from the sidecar.

```python
# tests/test_power_board.py
class TestPowerBoardProduction:
    """Production test for power_board — runs in method order."""

    def test_input_voltage(self, pins, verify):
        pins["VIN"].set_voltage(5.0)
        pins["VIN"].enable_output()
        verify("input_voltage", pins["VIN"].measure_voltage())

    def test_output_voltage(self, pins, verify):
        verify("output_voltage", pins["VOUT"].measure_dc_voltage())

    def test_load_sweep(self, pins, verify, load_percent):
        # load_percent is parametrized via the sidecar's sweeps:
        verify("output_voltage", pins["VOUT"].measure_dc_voltage())
```

```yaml
# tests/test_power_board.yaml — sidecar
limits:
  input_voltage:
    low: 4.5
    high: 5.5
    nominal: 5.0
    units: V
  output_voltage:
    low: 3.135
    high: 3.465
    units: V

mocks:
  - target: psu.measure_voltage
    return_value: 5.0
  - target: dmm.measure_dc_voltage
    return_value: 3.31

tests:
  TestPowerBoardProduction:
    tests:
      test_load_sweep:
        sweeps:
          - load_percent: [0, 50, 100]
        retry:
          max_retries: 2
```

The sidecar mirrors pytest's node-id structure (the `path::Class::method` identifier pytest assigns each test). Top-level keys (`limits`, `mocks`) apply file-wide. The recursive `tests:` tree lets you scope per-class and per-method overrides.

## Sidecar Features

### retry: Per-Test Retry on Failure

```yaml
tests:
  TestPowerBoardProduction:
    tests:
      test_margin:
        retry:
          max_retries: 2
          delay: 0.5
          on: [AssertionError]  # only retry on this exception name
```

### prompts: Operator Prompts

```yaml
prompts:
  visual_inspection:
    message: "Verify LED is GREEN"
    prompt_type: confirm
    timeout_seconds: 30
```

Reference the prompt from a test method via the [`prompt()`](../reference/pytest/fixtures.md#prompt-function) fixture (Litmus's operator-prompt helper for paused interactions).

### Ordering across files

A test class runs its methods in definition order. To order tests across multiple files, name the files so pytest collects them in the desired order (`test_01_power.py`, `test_02_thermal.py`) or filter via a profile (see [Profiles](../how-to/execution/profiles.md)).

## Complete Example

**products/power_board.yaml:**
```yaml
id: power_board
name: "5V to 3.3V Converter"

pins:
  VIN: {name: "J1.1", role: power}
  VOUT: {name: "J1.3", role: signal}
  GND: {name: "J1.2", role: ground}

characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

**stations/bench_1.yaml:**
```yaml
id: bench_1
name: "Production Bench 1"

instruments:
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config: {measure_voltage: 5.0}
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config: {measure_dc_voltage: 3.31}
```

**fixtures/power_board_fixture.yaml:**
```yaml
id: power_board_fixture
product_id: power_board

connections:
  vin_supply:
    name: vin_supply
    dut_pin: VIN
    instrument: psu
  vout_measure:
    name: vout_measure
    dut_pin: VOUT
    instrument: dmm
```

**tests/test_power_board.py:**
```python
class TestPowerBoardProduction:
    def test_input_voltage(self, pins, verify):
        pins["VIN"].set_voltage(5.0)
        pins["VIN"].enable_output()
        verify("input_voltage", pins["VIN"].measure_voltage())

    def test_output_voltage(self, pins, verify):
        verify("output_voltage", pins["VOUT"].measure_dc_voltage())
```

## Running Production Tests

```bash
pytest tests/ \
  --station=stations/bench_1.yaml \
  --fixture=fixtures/power_board_fixture.yaml \
  --dut-serial=SN12345 \
  --operator="Jane Doe" \
  -v
```

With simulation:
```bash
pytest tests/ \
  --station=stations/bench_1.yaml \
  --fixture=fixtures/power_board_fixture.yaml \
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

# Read all run parquets under the date-partitioned runs directory
table = pq.read_table("data/runs")                # recurses into date subdirs
rows = table.to_pylist()
# Filter to measurement rows (vs. step rows)
for row in (r for r in rows if r["record_type"] == "measurement"):
    print(f"{row['measurement_name']}: {row['measurement_value']} {row['measurement_units']}")
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
| Product spec | `products/power_board.yaml` | What to test |
| Station | `stations/bench_1.yaml` | Where to test |
| Fixture | `fixtures/power_board_fixture.yaml` | Pin-to-instrument mapping |
| Test class | `tests/test_power_board.py` | Test code, methods run in definition order |
| Sidecar | `tests/test_power_board.yaml` | Limits, sweeps, mocks, retries per method |

## What You Learned

- Fixture configuration for pin-to-instrument mapping
- The `pins` fixture for DUT-centric testing
- Pytest classes as the unit of ordered execution
- Sidecar YAML for per-test limits, sweeps, mocks, and retries
- Full traceability from spec to measurement

## Sharing data across projects: `litmus data promote`

`litmus init --starter` ships your project with a `data_dir: data` override in `litmus.yaml`. Runs land in the project-local `data/` folder so your tutorial / mock-instrument exploration doesn't pollute the global store every other project on this machine will share.

When you're ready to share data across projects and benches — typically once you have real hardware wired up and you want operator-UI access from any directory — run:

```bash
litmus data promote
```

This:

- Walks your project-local `data/runs/runs/*.parquet`
- **Skips** runs that match starter sentinels (`product_id: example_product`, `dut_serial: STARTER001`, etc.) — the throwaway scaffolding you ran while learning
- Copies the rest into the global store (`~/.local/share/litmus/data/` on Linux; platformdirs equivalents on Mac/Windows)
- Removes the `data_dir:` override from your `litmus.yaml` so future runs go straight to the global store

Add `--dry-run` to preview without writing. Add `--include-starter` to bring the scaffolding runs along too if you happened to capture something worth keeping.

The local `data/` directory stays in place after promote (the sandbox is still readable if you ever need it). When you're certain, `rm -rf data` to clean up.

## Congratulations!

You've completed the tutorial. You now have a foundation for production hardware testing with Litmus.

← [Step 8: Capability Matching](08-capabilities.md)  |  [Step 10: Live Monitoring →](10-live-monitoring.md)

## Next Steps

- [API Reference](../reference/runtime/api.md) — MCP tools and HTTP endpoints
- [Configuration Reference](../reference/configuration.md) — All YAML options
- [Litmus fixtures](../reference/pytest/fixtures.md) — all 20 fixtures the plugin exposes
- [Litmus markers](../reference/pytest/markers.md) — the seven `litmus_*` markers and their sidecar equivalents
- [pytest-native Reference](../reference/overview/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Test Harness Integration](../integration/configuration/harness.md) — Advanced patterns
