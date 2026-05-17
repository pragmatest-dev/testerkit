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
from litmus.client import LitmusClient

client = LitmusClient()

run = client.start_run(
    dut_serial="SN12345",
    station_id="any_station",
    test_phase="production",
)

with run.step("voltage_check") as step:
    step.measure("vcc", 3.31, units="V", low=3.0, high=3.6)

run.finish()
```

## API Reference

### LitmusClient

```python
client = LitmusClient(data_dir="results")
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
    dut_part_number="PCB-001",     # Optional
    dut_revision="A",              # Optional
    dut_lot_number="LOT-2026-05",  # Optional
    station_type="bench",          # Optional
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

Use LabVIEW's Python Node to call the Results API. Wrap `LitmusClient`'s
chained-builder API in a small helper:

```python
# litmus_labview.py
from litmus.client import LitmusClient

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

Or call via subprocess:

```python
# labview_wrapper.py
import sys
import json
from litmus.client import LitmusClient

def submit_from_labview(serial, station, results_json):
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

if __name__ == "__main__":
    submit_from_labview(sys.argv[1], sys.argv[2], sys.argv[3])
```

### From TestStand

Use TestStand's Python adapter:

```python
# teststand_adapter.py
from litmus.client import LitmusClient

def on_sequence_complete(context):
    """Called by TestStand when the sequence completes."""
    client = LitmusClient()

    run = client.start_run(
        dut_serial=context.dut_serial,
        station_id=context.station_name,
        test_phase="production",
    )

    for step in context.steps:
        with run.step(step.name) as s:
            for m in step.measurements:
                s.measure(
                    name=m.name,
                    value=m.value,
                    units=m.units,
                    low=m.limit_low,
                    high=m.limit_high,
                )

    run.finish()
```

### From Command Line

```python
#!/usr/bin/env python3
import sys
import json
from litmus.client import LitmusClient

serial = sys.argv[1]
results_file = sys.argv[2]

with open(results_file) as f:
    results = json.load(f)

client = LitmusClient()
run = client.start_run(
    dut_serial=serial,
    station_id="cli_test",
    test_phase="characterization",
)

for step_name, measurements in results["steps"].items():
    with run.step(step_name) as step:
        for m in measurements:
            step.measure(**m)

result = run.finish()
print(f"Run ID: {result.id}")
sys.exit(0 if result.outcome == "passed" else 1)
```

### Via HTTP API

For non-Python environments:

```bash
# Start a run — LaunchRequest body accepts product_id, dut_serial,
# station_id, test_path, operator, mock_instruments.
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "dut_serial": "SN12345",
    "station_id": "bench_1",
    "test_path": "tests/test_power.py",
    "operator": "Jane Doe"
  }'

# Returns: {"run_id": "abc123..."}
```

## Querying Results

### Python

```python
client = LitmusClient()

# List recent runs — returns list[RunSummary] (Pydantic, attribute access)
for run in client.list_runs(limit=10):
    print(f"{str(run.test_run_id)[:8]}: {run.outcome}")

# Get specific run — returns RunSummary | None
run = client.get_run("abc12345")

# Get measurements — returns list[dict] keyed by parquet column names
for m in client.get_measurements("abc12345"):
    print(f"{m['measurement_name']}: {m['measurement_value']} {m['measurement_units']}")
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

# Read measurements — runs are partitioned by date under <data_dir>/runs/
table = pq.read_table("data/runs")               # recursively reads all runs
df = table.to_pandas()
# Filter to measurement rows (the schema multiplexes step + measurement rows)
df = df[df["record_type"] == "measurement"]

# Filter by serial
board_data = df[df['dut_serial'] == 'SN12345']
```

## Data Schema

Results are stored in Parquet under `<data_dir>/runs/{date}/*.parquet`. Each
file holds one run's rows; every row carries a `record_type` discriminator
(`run`, `step`, or `measurement`) plus the denormalized run/DUT/station context.
See `src/litmus/data/schemas.py` for the canonical column list. The columns
most consumers reach for:

### Run-level columns (present on every row)

| Column | Type | Description |
|--------|------|-------------|
| run_id | string | Unique run ID |
| run_started_at | timestamp | Run start time |
| run_ended_at | timestamp | Run end time |
| dut_serial | string | DUT serial number |
| station_id | string | Station identifier |
| run_outcome | string | passed / failed / errored / skipped / done / terminated / aborted |

### Measurement-row columns (`record_type = 'measurement'`)

| Column | Type | Description |
|--------|------|-------------|
| run_id | string | Parent run ID |
| step_name | string | Test step name |
| measurement_name | string | Measurement name |
| measurement_value | float | Measured value |
| measurement_units | string | Unit of measure |
| limit_low | float | Low limit (if any) |
| limit_high | float | High limit (if any) |
| measurement_outcome | string | passed / failed / errored / skipped / done |

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
