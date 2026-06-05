# Python Client

The `LitmusClient` provides a simple API for submitting test results from external tools — LabVIEW, TestStand, custom scripts, or any system that can call Python.

## Installation

```python
from litmus import LitmusClient
```

## Basic Usage

```python
from litmus import LitmusClient

# Create client (results saved to ./results by default)
client = LitmusClient()

# Start a test run
run = client.start_run(
    dut_serial="SN12345",
    station_id="bench_1",
    test_phase="production",
)

# Add measurements
with run.step("voltage_check") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)
    step.measure("vdd", 1.81, units="V", low=1.7, high=1.9)

# Save results
run.finish()
```

## API Reference

### LitmusClient

```python
client = LitmusClient(data_dir="results")
```

**Methods:**

| Method | Description |
|--------|-------------|
| `start_run(...)` | Start a new test run, returns `RunBuilder` |
| `list_runs(limit=50)` | List recent test runs |
| `get_run(run_id)` | Get a specific run by ID |
| `get_measurements(run_id)` | Get measurements for a run |

### RunBuilder

Returned by `client.start_run()`.

```python
run = client.start_run(
    dut_serial="SN12345",          # Required
    station_id="bench_1",          # Required
    dut_part_number="PCB-001",     # Optional
    dut_revision="A",              # Optional
    dut_lot_number="LOT2026",      # Optional
    station_type="production",     # Optional
    operator="Jane Doe",           # Optional
    test_phase="production",       # Optional
)
```

**Properties:**

| Property | Description |
|----------|-------------|
| `run.id` | UUID of the test run |

**Methods:**

| Method | Description |
|--------|-------------|
| `run.step(name, description=None)` | Create a test step (context manager) |
| `run.finish()` | Finalize and save the run |
| `run.abort(message=None)` | Abort without saving |

### StepBuilder

Returned by `run.step()` context manager.

```python
with run.step("voltage_check", "Verify all voltage rails") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)
```

**Methods:**

| Method | Description |
|--------|-------------|
| `step.measure(...)` | Record a measurement |
| `step.vector(**params)` | Create a test vector (context manager) |
| `step.fail(message=None)` | Mark step as failed |
| `step.skip(message=None)` | Mark step as skipped |

### Measurements

```python
step.measure(
    name="vcc",              # Measurement name
    value=3.31,              # Measured value
    units="V",               # Optional: units
    low=3.0,                 # Optional: low limit
    high=3.6,                # Optional: high limit
    nominal=3.3,             # Optional: nominal value
    comparator="GELE",       # Optional: comparison mode (default: GELE)
    spec_ref="SPEC-001",     # Optional: specification reference
)
```

**Comparators:**

| Comparator | Pass Condition |
|------------|----------------|
| `GELE` | low <= value <= high (default) |
| `EQ` | value == nominal |
| `NE` | value != nominal |
| `LT` | value < high |
| `LE` | value <= high |
| `GT` | value > low |
| `GE` | value >= low |

### VectorBuilder

Returned by `step.vector(**params)` context manager. Same `measure` / `fail` / `skip` surface as `StepBuilder`, scoped to one vector under the step.

```python
with run.step("voltage_sweep") as step:
    for voltage in [3.3, 5.0, 12.0]:
        with step.vector(input_voltage=voltage) as vec:
            output = measure_output(voltage)
            vec.measure("output_voltage", output, units="V")
```

**Methods:**

| Method | Description |
|---|---|
| `vec.measure(...)` | Record a measurement on this vector (same signature as `step.measure`). |
| `vec.fail(message=None)` | Mark this vector as failed; does not propagate to the parent step until the step ends. |
| `vec.skip(message=None)` | Mark this vector as skipped. |

`vec.fail` / `vec.skip` are the vector-scoped versions of the step methods — set the outcome on the active `TestVector` instead of the `TestStep`. Use them when an inner sweep iteration should be marked individually while the rest of the step proceeds.

## Complete Example

```python
from litmus import LitmusClient

def run_production_test(serial_number: str):
    client = LitmusClient(data_dir="./test_results")

    run = client.start_run(
        dut_serial=serial_number,
        station_id="production_line_1",
        operator="AutoTester",
        test_phase="production",
    )

    # Simple measurements
    with run.step("power_rails") as step:
        step.measure("vcc_3v3", read_voltage("VCC"), units="V", low=3.1, high=3.5)
        step.measure("vdd_1v8", read_voltage("VDD"), units="V", low=1.7, high=1.9)

    # Parametrized test
    with run.step("current_sweep") as step:
        for load_ma in [0, 100, 500, 1000]:
            set_load(load_ma)
            with step.vector(load_ma=load_ma) as vec:
                vec.measure("efficiency", calc_efficiency(), units="%", low=80)

    # Finish and save
    result = run.finish()
    print(f"Test complete: {result.outcome}")
    return result.outcome == "passed"
```

## Integration Patterns

### From LabVIEW

Call Python via LabVIEW's Python Node. Wrap `LitmusClient`'s chained-builder
API in a small helper:

```python
# litmus_labview.py
from litmus import LitmusClient

def submit_labview_run(serial, station, measurements):
    """measurements: list of dicts with name, value, low, high, units."""
    client = LitmusClient()
    run = client.start_run(dut_serial=serial, station_id=station, test_phase="production")
    with run.step("labview_results") as step:
        for m in measurements:
            step.measure(**m)
    return run.finish()
```

Then call `litmus_labview.submit_labview_run` from LabVIEW's Python Node.

### From TestStand

Use TestStand's Python adapter or call via subprocess:

```python
# wrapper.py - called from TestStand
import sys
from litmus import LitmusClient

def submit_teststand_results(serial, station, results_json):
    import json
    results = json.loads(results_json)

    client = LitmusClient()
    run = client.start_run(
        dut_serial=serial,
        station_id=station,
        test_phase="production",
    )

    for step_name, measurements in results.items():
        with run.step(step_name) as step:
            for m in measurements:
                step.measure(**m)

    run.finish()
```

### From Command Line

```python
#!/usr/bin/env python3
import sys
from litmus import LitmusClient

serial = sys.argv[1]
voltage = float(sys.argv[2])

client = LitmusClient()
run = client.start_run(
    dut_serial=serial,
    station_id="cli_test",
    test_phase="characterization",
)

with run.step("voltage") as step:
    step.measure("vcc", voltage, units="V", low=4.5, high=5.5)

result = run.finish()
sys.exit(0 if result.outcome == "passed" else 1)
```

## Querying Results

```python
client = LitmusClient()

# List recent runs — returns list[RunSummary] (Pydantic models, use attribute access)
for run in client.list_runs(limit=10):
    print(f"{str(run.test_run_id)[:8]}: {run.outcome}")

# Get specific run — returns RunSummary | None
run = client.get_run("abc12345")
print(run)

# Get measurements — returns list[dict] using parquet column names
measurements = client.get_measurements("abc12345")
for m in measurements:
    print(f"{m['measurement_name']}: {m['measurement_value']} {m['measurement_units']}")
```

## Next Steps

- [API Reference](api.md) — HTTP and MCP endpoints
- [Quick Start](../../tutorial/00-quickstart.md) — Getting started guide
