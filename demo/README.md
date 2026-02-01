# Litmus Demo - Golden Example

This demo showcases the Litmus hardware test framework with a simulated power board.
It demonstrates **every major feature** of the framework.

## Quick Start

```bash
cd demo
pytest tests/test_power_board.py --station=demo_station_001 --mock-instruments -v
```

## Project Structure

```
demo/
├── products/
│   └── power_board/
│       └── spec.yaml           # Product specification (characteristics, limits)
├── stations/
│   └── demo_station_001.yaml   # Station config (instruments, addresses)
├── fixtures/
│   └── power_board_fixture.yaml  # Pin routing (DUT pin → instrument)
├── sequences/
│   └── power_board_smoke.yaml    # Test sequence (execution order, dialogs)
├── tests/
│   ├── conftest.py             # Instrument fixtures
│   ├── config.yaml             # Test configuration (vectors, limits)
│   ├── test_power_board.py     # @litmus_test decorator examples
│   └── test_pure_pytest.py     # Pure pytest with litmus_logger
└── results/                    # Output (Parquet files, gitignored)
```

## The 7 Project Folders

| Folder | Purpose | Key File |
|--------|---------|----------|
| `products/` | WHAT you're testing | `{id}/spec.yaml` |
| `stations/` | WHERE you test | `{id}.yaml` |
| `fixtures/` | HOW pins connect | `{id}.yaml` |
| `instruments/` | Custom drivers | `{type}.yaml` |
| `sequences/` | Test order | `{id}.yaml` |
| `tests/` | Test code | `test_*.py` + `config.yaml` |
| `results/` | Output | Parquet files |

## Three Approaches (Simple → Advanced)

### 1. `@litmus_test` Decorator (Recommended for Most Users)

Clean, declarative tests with configuration in YAML:

```python
# tests/test_power_board.py
@litmus_test
def test_output_voltage(vector, psu, dmm):
    vin = vector.get("vin", 5.0)
    psu.set_voltage(vin)
    psu.enable_output()
    return dmm.measure_dc_voltage()  # Framework checks limit
```

```yaml
# tests/config.yaml
test_output_voltage:
  vectors:
    - vin: 5.0
  limits:
    test_output_voltage:
      low: 3.2
      high: 3.4
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ 25C"
```

### 2. Pure Pytest with `litmus_logger`

Full control with manual logging:

```python
# tests/test_pure_pytest.py
def test_basic(psu, dmm, litmus_logger):
    psu.set_voltage(5.0)
    psu.enable_output()
    vout = dmm.measure_dc_voltage()

    litmus_logger.measure(
        name="output_voltage",
        value=vout,
        limit=Limit(low=3.2, high=3.4, units="V"),
        dut_pin="TP_VOUT",
    )
    assert vout >= 3.2
```

### 3. Test Architect Patterns (TestHarness, @measure, @litmus_step)

For test architects who need maximum control:

```python
# tests/test_architect.py

# @measure: Reusable measurement functions with embedded limits
@measure(name="output_voltage", limit=Limit(low=3.2, high=3.4, units="V"))
def measure_output_voltage(dmm):
    return dmm.measure_dc_voltage()

# @litmus_step: Track non-measurement operations
@litmus_step
def verify_dut_connection(psu):
    psu.set_voltage(0.1)
    current = psu.measure_current()
    assert current < 0.001, "DUT shorted!"

# TestHarness: Explicit vector control
def test_explicit_control(psu, dmm, litmus_logger):
    harness = TestHarness(
        config={"vectors": [{"load": 0.1}, {"load": 0.8}]},
        logger=litmus_logger,
    )
    for vector in harness.vectors:
        with harness.run_vector(vector):
            harness.measure("vout", dmm.measure_dc_voltage())
```

See `tests/test_architect.py` for complete examples.

## Vector Expansion Modes

### Explicit List
```yaml
vectors:
  - vin: 5.0
    load: 0.1
  - vin: 5.0
    load: 0.8
```

### Product (Cartesian)
```yaml
vectors:
  expand: product
  vin: [4.75, 5.0, 5.5]
  load: [0.1, 0.5, 0.8]
# Result: 9 vectors (3×3)
```

### Nested Loops
```yaml
vectors:
  expand: nested
  loops:
    - name: temperature
      values: [25, 85]      # Outer (slow)
    - name: load
      values: [0.1, 0.5]    # Inner (fast)
# Result: 4 vectors, temperature changes first
```

### Range
```yaml
vectors:
  expand: range
  name: vin
  start: 4.5
  stop: 6.0
  step: 0.5
# Result: [4.5, 5.0, 5.5, 6.0]
```

## Return Patterns

### Single Value
```python
@litmus_test
def test_voltage(vector, dmm):
    return dmm.measure_dc_voltage()
```

### Dict (Multiple Measurements)
```python
@litmus_test
def test_power(vector, psu, dmm):
    return {
        "input_power": psu.measure_voltage() * psu.measure_current(),
        "output_voltage": dmm.measure_dc_voltage(),
    }
```

### Yield (Streaming)
```python
@litmus_test
def test_burn_in(vector, dmm):
    for i in range(10):
        yield {"voltage": dmm.measure_dc_voltage()}
        time.sleep(60)
```

## Advanced Patterns

### Waveform Capture (Pattern 11)

Capture and analyze oscilloscope waveforms:

```python
@litmus_test
def test_output_ripple(vector, psu, eload, scope):
    psu.set_voltage(5.0)
    psu.enable_output()
    eload.set_current(0.5)
    eload.enable()

    waveform = scope.fetch_waveform("CH1")  # Returns Waveform(t0, dt, Y, attrs)
    ripple = max(waveform.Y) - min(waveform.Y)
    return ripple  # mV
```

### Callable Limits (Pattern 12)

Dynamic limits based on test conditions:

```yaml
# config.yaml
test_output_voltage_temp:
  vectors:
    expand: product
    temperature: [-40, 25, 85]
  limits:
    test_output_voltage_temp:
      callable: |
        temp = ctx.get_in("temperature")
        if temp < 0:
          return Limit(low=3.15, high=3.45, units="V")
        elif temp < 50:
          return Limit(low=3.25, high=3.35, units="V")
        else:
          return Limit(low=3.10, high=3.50, units="V")
```

### Context Traceability (Pattern 13)

Record inputs and observations for full traceability:

```python
@litmus_test
def test_efficiency_with_context(vector, psu, dmm, eload, context):
    # Record commanded values (→ in_* columns in Parquet)
    context.configure("vin", vector["vin"])
    context.configure("load", vector["load_current"])

    # Record observations (→ out_* columns in Parquet)
    context.observe("ambient_temp", 24.5)
    context.observe("dut_temp", 42.3)

    # Measurements (→ limit checked, stored)
    pin = psu.measure_voltage() * psu.measure_current()
    pout = dmm.measure_dc_voltage() * vector["load_current"]

    return {"input_power": pin, "output_power": pout, "efficiency": pout/pin * 100}
```

## Change Detection

Optimize slow operations by detecting when parameters change:

```python
@litmus_test
def test_temp_sweep(vector, psu, dmm):
    if vector.changed("temperature"):
        # Only runs when temperature changes
        set_chamber_temperature(vector["temperature"])

    psu.set_voltage(vector["vin"])
    return dmm.measure_dc_voltage()
```

## Retry Configuration

```yaml
test_flaky_measurement:
  retry:
    max_attempts: 3
    delay_seconds: 0.5
  limits:
    ...
```

## Limit Comparators (IEEE 1671)

| Comparator | Meaning |
|------------|---------|
| `GELE` | low ≤ value ≤ high (default) |
| `LE` | value ≤ high (upper only) |
| `GE` | value ≥ low (lower only) |
| `EQ` | value == nominal |
| `LT` | value < high |
| `GT` | value > low |

## Traceability

Every measurement records the complete signal path:

```python
measurement = Measurement(
    name="output_voltage",
    value=3.3,
    units="V",
    # Traceability chain:
    dut_pin="TP_VOUT",              # DUT connection
    fixture_point="vout_measure",    # Fixture junction
    instrument_name="dmm",           # Station instrument
    instrument_channel="1",          # Physical channel
    spec_ref="output_voltage @ 25C", # Spec reference
)
```

## Running Tests

```bash
# Basic run with simulation
pytest tests/test_power_board.py --station=demo_station_001 --mock-instruments -v

# Run specific test
pytest tests/test_power_board.py::test_load_sweep --station=demo_station_001 --mock-instruments -v

# With DUT serial number (for production)
pytest tests/ --station=demo_station_001 --dut-serial=DPB001-0001 --mock-instruments -v

# With custom results directory
pytest tests/ --results-dir=./my_results --mock-instruments -v

# Pure pytest examples
pytest tests/test_pure_pytest.py --station=demo_station_001 --mock-instruments -v
```

## Querying Results

Results are saved with self-describing filenames using UTC timestamps:
- `results/runs/{date}/{timestamp}_{serial}.parquet` (with serial)
- `results/runs/{date}/{timestamp}.parquet` (without serial)

```python
import pandas as pd

# Load a run (filename tells you when and which DUT)
df = pd.read_parquet("results/runs/2026-01-31/20260131T143025Z_SN001.parquet")

# Filter to specific test
vout = df[df["step_name"] == "test_output_voltage"]
print(vout[["value", "outcome", "in_vin"]])

# Aggregate across all runs
df_all = pd.read_parquet("results/runs/**/*.parquet")
print(df_all.groupby("step_name")["outcome"].value_counts())
```

Or use DuckDB for SQL queries:

```python
import duckdb

duckdb.sql("""
    SELECT step_name, outcome, COUNT(*)
    FROM 'results/runs/**/*.parquet'
    GROUP BY step_name, outcome
""").show()
```

## What's Demonstrated

| Feature | File | Pattern |
|---------|------|---------|
| Product spec | `products/power_board/spec.yaml` | Characteristics, conditions, limits |
| Station config | `stations/demo_station_001.yaml` | Instruments, simulation |
| Fixture routing | `fixtures/power_board_fixture.yaml` | DUT pin → instrument |
| Test sequence | `sequences/power_board_smoke.yaml` | Ordered execution |
| Single vector | `config.yaml` | `test_output_voltage_no_load` |
| Retry | `config.yaml` | `test_output_voltage_full_load` |
| Explicit list | `config.yaml` | `test_load_regulation` |
| Product expansion | `config.yaml` | `test_load_sweep` |
| Nested loops | `config.yaml` | `test_temp_load_matrix` |
| Range expansion | `config.yaml` | `test_line_regulation` |
| Dict return | `test_power_board.py` | `test_power_analysis` |
| Yield streaming | `test_power_board.py` | `test_stability_over_time` |
| Change detection | `test_power_board.py` | `test_load_sweep` |
| One-sided limit | `config.yaml` | `test_quiescent_current` |
| Waveform capture | `test_power_board.py` | `test_output_ripple` |
| Callable limits | `config.yaml` | `test_output_voltage_temp` |
| Context API | `test_power_board.py` | `test_efficiency_with_context` |
| Pure pytest | `test_pure_pytest.py` | Manual litmus_logger |
| @measure decorator | `test_architect.py` | Reusable measurement functions |
| @litmus_step | `test_architect.py` | Non-measurement step tracking |
| TestHarness direct | `test_architect.py` | Explicit vector control |
| Spec-driven limits | `test_architect.py` | SpecContext integration |

## Simulation Mode

All instruments support simulation:

```yaml
# Station config
instruments:
  dmm:
    type: "dmm"
    resource: "TCPIP::192.168.1.102::INSTR"
    simulate: true
    sim_config:
      voltage: 3.3
```

Simulation is also enabled by:
- `--mock-instruments` pytest flag
- `LITMUS_MOCK_INSTRUMENTS=1` environment variable (set by UI)

## Next Steps

1. **Explore the files** - Read each YAML to understand the data model
2. **Run tests** - See how vectors expand and limits are checked
3. **Modify limits** - Change `config.yaml` and re-run
4. **Add a test** - Create a new test following the patterns
5. **Query results** - Explore the Parquet output
