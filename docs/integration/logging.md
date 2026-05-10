# Logging Integration

Integrate Litmus logging and data collection into existing test infrastructure.

## Overview

Litmus provides:
- Structured measurement logging to Parquet
- Test run tracking with metadata
- Query API for results
- Export capabilities

This guide shows how to integrate Litmus logging without changing your test framework.

## Quick Start

```python
from litmus import LitmusClient

client = LitmusClient()

run = client.start_run(
    dut_serial="SN12345",
    station_id="my_station",
    test_sequence_id="my_test",
)

with run.step("measurement_step") as step:
    step.measure("voltage", 3.31, units="V", low=3.0, high=3.6)

run.finish()
```

## Logging Approaches

### Approach 1: Explicit Logging

Log specific measurements:

```python
from litmus import LitmusClient

def run_test(dut_serial: str):
    client = LitmusClient()

    run = client.start_run(
        dut_serial=dut_serial,
        station_id="bench_1",
        test_sequence_id="voltage_test",
    )

    # Your existing test code
    voltage = measure_voltage()
    current = measure_current()

    # Log to Litmus
    with run.step("measurements") as step:
        step.measure("voltage", voltage, units="V", low=3.0, high=3.6)
        step.measure("current", current, units="A", low=0, high=1.0)

    run.finish()
```

### Approach 2: Context Manager

Automatic cleanup on errors:

```python
from litmus import LitmusClient

def run_test(dut_serial: str):
    client = LitmusClient()

    with client.run(
        dut_serial=dut_serial,
        station_id="bench_1",
        test_sequence_id="voltage_test",
    ) as run:
        with run.step("measurements") as step:
            voltage = measure_voltage()
            step.measure("voltage", voltage, units="V", low=3.0, high=3.6)
    # Automatically finishes (or aborts on exception)
```

### Approach 3: Decorator Pattern

Wrap existing functions:

```python
from functools import wraps
from litmus import LitmusClient

def log_to_litmus(test_name: str):
    """Decorator to log test results to Litmus."""
    def decorator(func):
        @wraps(func)
        def wrapper(dut_serial: str, *args, **kwargs):
            client = LitmusClient()
            run = client.start_run(
                dut_serial=dut_serial,
                station_id="default",
                test_sequence_id=test_name,
            )

            try:
                result = func(dut_serial, run, *args, **kwargs)
                run.finish()
                return result
            except Exception as e:
                run.abort(str(e))
                raise

        return wrapper
    return decorator

@log_to_litmus("voltage_test")
def test_voltage(dut_serial: str, run):
    """Test with automatic logging."""
    voltage = measure_voltage()

    with run.step("voltage_check") as step:
        step.measure("voltage", voltage, units="V", low=3.0, high=3.6)

    return voltage
```

## Data Storage

### Default Location

Results are stored in Parquet files with self-describing filenames:

```
results/runs/{date}/
├── {timestamp}_{serial}.parquet     # With serial (production)
├── {timestamp}_{serial}_ref/        # External data for above (waveforms, images)
├── {timestamp}.parquet              # Without serial (dev/debug)
└── {timestamp}_ref/                 # External data for above
```

All timestamps are UTC for consistent cross-timezone analysis.

### Custom Location

```bash
pytest tests/ --data-dir=/path/to/results
```

### Environment Variable

```bash
export LITMUS_RESULTS_DIR=/shared/test_results
```

## Querying Results

### Pandas

```python
import pandas as pd

# Load a specific run
df = pd.read_parquet("results/runs/2026-01-30/20260130T143025Z_SN001.parquet")

# Filter by test
vout = df[df["step_name"] == "test_output_voltage"]
print(vout[["value", "outcome", "in_vin"]])

# Load all runs
df_all = pd.read_parquet("results/runs/**/*.parquet")
print(df_all.groupby("step_name")["outcome"].value_counts())
```

### DuckDB

```python
import duckdb

# Query across all runs
duckdb.sql("""
    SELECT dut_serial, step_name, outcome, COUNT(*)
    FROM 'results/runs/**/*.parquet'
    GROUP BY dut_serial, step_name, outcome
""").show()
```

### CLI

```bash
litmus runs                  # List recent runs
litmus show <run_id>         # Show run details
```

## Metadata

### Run Metadata

```python
run = client.start_run(
    dut_serial="SN12345",
    station_id="bench_1",
    test_sequence_id="production_test",
    # Optional metadata
    dut_part_number="PCB-001",
    dut_revision="A",
    dut_lot_number="LOT2026-01",
    operator="Jane Doe",
    test_phase="production",
)
```

### Custom Metadata

```python
run.add_metadata(
    firmware_version="1.2.3",
    calibration_date="2026-01-15",
    temperature_c=25.0,
)
```

### Measurement Metadata

```python
step.measure(
    name="voltage",
    value=3.31,
    units="V",
    low=3.0,
    high=3.6,
    spec_ref="SPEC-001",
    dut_pin="J1.3",
    instrument_channel="CH1",
)
```

## Integration Patterns

### With Logging Framework

```python
import logging
from litmus import LitmusClient

logger = logging.getLogger(__name__)

class LitmusHandler(logging.Handler):
    """Send log records to Litmus."""

    def __init__(self, run):
        super().__init__()
        self.run = run
        self.step = None

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            # Log errors/warnings as step failures
            if self.step:
                self.step.fail(record.getMessage())
```

### With Database

```python
from litmus import LitmusClient

def sync_to_database(run_id: str, db_connection):
    """Sync Litmus results to external database."""
    client = LitmusClient()

    run = client.get_run(run_id)
    measurements = client.get_measurements(run_id)

    db_connection.execute(
        "INSERT INTO test_runs (id, serial, outcome) VALUES (?, ?, ?)",
        (run_id, run['dut_serial'], run['outcome'])
    )

    for m in measurements:
        db_connection.execute(
            "INSERT INTO measurements (run_id, name, value) VALUES (?, ?, ?)",
            (run_id, m['measurement_name'], m['value'])
        )
```

### With Cloud Storage

```python
import boto3
from litmus import LitmusClient

def upload_results(run_id: str, bucket: str):
    """Upload results to S3."""
    s3 = boto3.client('s3')

    client = LitmusClient()
    run = client.get_run(run_id)

    # Upload Parquet files
    for table in ['test_runs', 'measurements', 'vectors']:
        local_path = f"results/{table}/{run_id}.parquet"
        s3_key = f"test_results/{run['dut_serial']}/{run_id}/{table}.parquet"
        s3.upload_file(local_path, bucket, s3_key)
```

## Performance Considerations

### Batch Measurements

```python
# Slower: Individual calls
for i, value in enumerate(values):
    step.measure(f"sample_{i}", value)

# Faster: Batch
measurements = [
    {"name": f"sample_{i}", "value": v}
    for i, v in enumerate(values)
]
step.measure_batch(measurements)
```

### Async Logging

```python
import asyncio
from litmus import AsyncLitmusClient

async def log_results():
    client = AsyncLitmusClient()

    async with client.run(...) as run:
        async with run.step("measurements") as step:
            await step.measure("voltage", 3.31)
```

## Best Practices

1. **Log at the right granularity** — Not every variable, just key measurements
2. **Include metadata** — Serial numbers, timestamps, conditions
3. **Use consistent naming** — Same measurement names across tests
4. **Handle errors gracefully** — Abort runs on failure
5. **Don't block on logging** — Use async for high-speed tests

## Next Steps

- [Results API](results-api.md) — Full API reference
- [Test Harness](harness.md) — Measurement tracking
- [Python Client](../reference/client.md) — Detailed client API
