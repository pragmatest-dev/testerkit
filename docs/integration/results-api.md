# Results API Integration

Use Litmus as a unified results storage system for any test source — LabVIEW, TestStand, custom scripts, or legacy systems.

## Overview

The Results API lets you:
- Store test results from any source
- View all results in a unified UI
- Query results programmatically
- Export to Parquet for analytics

## Quick Start

```python
from litmus import LitmusClient

client = LitmusClient()

run = client.start_run(
    dut_serial="SN12345",
    station_id="any_station",
    test_sequence_id="my_test",
)

with run.step("voltage_check") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)

run.finish()
```

## API Reference

### LitmusClient

```python
client = LitmusClient(results_dir="results")
```

| Method | Description |
|--------|-------------|
| `start_run(...)` | Start a new test run |
| `list_runs(limit=50)` | List recent runs |
| `get_run(run_id)` | Get run by ID |
| `get_measurements(run_id)` | Get measurements for a run |

### RunBuilder

```python
run = client.start_run(
    dut_serial="SN12345",          # Required
    station_id="bench_1",          # Required
    test_sequence_id="power_test", # Required
    dut_part_number="PCB-001",     # Optional
    dut_revision="A",              # Optional
    operator="Jane Doe",           # Optional
    test_phase="production",       # Optional
)
```

| Method | Description |
|--------|-------------|
| `run.step(name)` | Create a test step (context manager) |
| `run.finish()` | Finalize and save the run |
| `run.abort(message)` | Abort without saving |

### StepBuilder

```python
with run.step("voltage_check") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)
```

| Method | Description |
|--------|-------------|
| `step.measure(...)` | Record a measurement |
| `step.vector(**params)` | Create a test vector |
| `step.fail(message)` | Mark step as failed |
| `step.skip(message)` | Mark step as skipped |

### Measurements

```python
step.measure(
    name="vcc",              # Measurement name
    value=3.31,              # Measured value
    units="V",               # Optional: units
    low=3.0,                 # Optional: low limit
    high=3.6,                # Optional: high limit
    nominal=3.3,             # Optional: nominal value
    comparator="GELE",       # Optional: comparison mode
    spec_ref="SPEC-001",     # Optional: spec reference
)
```

## Integration Patterns

### From LabVIEW

Use LabVIEW's Python Node to call the Results API:

```
Python Node
├── Module: litmus
├── Function: submit_result
└── Inputs: serial, station, measurements[]
```

Or call via subprocess:

```python
# labview_wrapper.py
import sys
import json
from litmus import LitmusClient

def submit_from_labview(serial, station, results_json):
    results = json.loads(results_json)

    client = LitmusClient()
    run = client.start_run(
        dut_serial=serial,
        station_id=station,
        test_sequence_id="labview_test",
    )

    for step_name, measurements in results.items():
        with run.step(step_name) as step:
            for m in measurements:
                step.measure(**m)

    run.finish()

if __name__ == "__main__":
    submit_from_labview(sys.argv[1], sys.argv[2], sys.argv[3])
```

### From TestStand

Use TestStand's Python adapter:

```python
# teststand_adapter.py
from litmus import LitmusClient

def on_sequence_complete(context):
    """Called by TestStand when sequence completes."""
    client = LitmusClient()

    run = client.start_run(
        dut_serial=context.dut_serial,
        station_id=context.station_name,
        test_sequence_id=context.sequence_name,
    )

    for step in context.steps:
        with run.step(step.name) as s:
            for m in step.measurements:
                s.measure(
                    name=m.name,
                    value=m.value,
                    units=m.units,
                    low=m.low_limit,
                    high=m.high_limit,
                )

    run.finish()
```

### From Command Line

```python
#!/usr/bin/env python3
import sys
import json
from litmus import LitmusClient

serial = sys.argv[1]
results_file = sys.argv[2]

with open(results_file) as f:
    results = json.load(f)

client = LitmusClient()
run = client.start_run(
    dut_serial=serial,
    station_id="cli_test",
    test_sequence_id="imported",
)

for step_name, measurements in results["steps"].items():
    with run.step(step_name) as step:
        for m in measurements:
            step.measure(**m)

result = run.finish()
print(f"Run ID: {result.id}")
sys.exit(0 if result.outcome == "pass" else 1)
```

### Via HTTP API

For non-Python environments:

```bash
# Start a run
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "dut_serial": "SN12345",
    "station_id": "bench_1",
    "test_sequence_id": "imported"
  }'

# Returns: {"run_id": "abc123..."}
```

## Querying Results

### Python

```python
client = LitmusClient()

# List recent runs
for run in client.list_runs(limit=10):
    print(f"{run['test_run_id'][:8]}: {run['outcome']}")

# Get specific run
run = client.get_run("abc12345")

# Get measurements
for m in client.get_measurements("abc12345"):
    print(f"{m['measurement_name']}: {m['value']} {m['units']}")
```

### CLI

```bash
litmus runs                  # List recent runs
litmus show <run_id>         # Show run details
```

### HTTP API

```bash
curl http://localhost:8000/api/runs
curl http://localhost:8000/api/runs/abc12345
curl http://localhost:8000/api/runs/abc12345/measurements
```

### Raw Parquet

```python
import pyarrow.parquet as pq
import pandas as pd

# Read measurements
table = pq.read_table("results/measurements")
df = table.to_pandas()

# Filter by serial
board_data = df[df['dut_serial'] == 'SN12345']
```

## Data Schema

Results are stored in Parquet format:

### test_runs table

| Column | Type | Description |
|--------|------|-------------|
| test_run_id | string | Unique run ID |
| started_at | timestamp | Run start time |
| ended_at | timestamp | Run end time |
| dut_serial | string | DUT serial number |
| station_id | string | Station identifier |
| outcome | string | PASS, FAIL, ERROR, ABORTED |

### measurements table

| Column | Type | Description |
|--------|------|-------------|
| test_run_id | string | Parent run ID |
| step_name | string | Test step name |
| measurement_name | string | Measurement name |
| value | decimal | Measured value |
| units | string | Unit of measure |
| low_limit | decimal | Low limit (if any) |
| high_limit | decimal | High limit (if any) |
| outcome | string | PASS, FAIL |

## Benefits

- **Unified view** — All results in one place
- **Tool-agnostic** — Works with any test source
- **Analytics-ready** — Parquet format for data analysis
- **Low effort** — Minimal code changes required
- **Incremental** — Add more integration over time

## Next Steps

- [Test Harness](harness.md) — Add measurement tracking to existing tests
- [API Reference](../reference/api.md) — Full HTTP and MCP API docs
- [Python Client](../reference/client.md) — Detailed client API
