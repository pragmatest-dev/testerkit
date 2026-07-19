# Logging integration

Send TesterKit results onward to external systems — Python logging frameworks, databases, cloud storage. TesterKit owns the parquet record; this page covers the bridges to other platforms.

For the underlying API to write into TesterKit's store, see the [Python client reference](../../reference/runtime/client.md). For HTTP / MCP query endpoints, see [api.md](../../reference/runtime/api.md).

## Where the data already is

Results land in parquet under `<data_dir>/runs/{date}/{timestamp}_{run_id8}_{serial}.parquet` (or `{timestamp}_{run_id8}.parquet` when there is no serial), regardless of which submission path you use — pytest plugin, `TesterKitClient`, or the OpenHTF bridge. See [data-stores.md](../../concepts/data/data-stores.md) for the canonical layout and the `data_dir` resolution chain. The integration patterns below read from that store and forward data elsewhere.

For the on-write side, see:

- [Python client reference](../../reference/runtime/client.md) — `TesterKitClient` API for submitting test runs from non-pytest sources
- [Submitting results from non-pytest sources](results-api.md) — when to use which submission path
- [TesterKit fixtures](../../reference/pytest/fixtures.md) — the pytest plugin path (most projects)

## Python logging-framework bridge

Attach a `logging.Handler` that turns log records into step failures on the active step:

```python
import logging
from testerkit import TesterKitClient

class TesterKitHandler(logging.Handler):
    """Forward warning/error log records to the active step as a failure."""
    def __init__(self, step):
        super().__init__()
        self.step = step

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.step.fail(record.getMessage())

client = TesterKitClient()
run = client.start_run(uut_serial="SN001", station_id="bench_1")
log = logging.getLogger("my_test")

with run.step("power_on") as step:
    log.addHandler(TesterKitHandler(step))
    log.warning("rail sagged to 2.9 V")   # -> step.fail(...)

run.finish()
```

`run.step()` is a context manager that yields a `StepBuilder`. The handler is scoped to the step — create a new one (or call `removeHandler`) for subsequent steps if your logger is long-lived.

## Sync to an external database

After a run finishes, push its summary and measurement rows into a SQL database:

```python
from testerkit import TesterKitClient

def sync_to_database(run_id: str, db_connection):
    """Mirror one TesterKit run's summary + measurements into an external DB."""
    client = TesterKitClient()
    run = client.get_run(run_id)              # RunSummary | None
    measurements = client.get_measurements(run_id)  # list[dict]

    db_connection.execute(
        "INSERT INTO test_runs (run_id, serial, outcome) VALUES (?, ?, ?)",
        (run.test_run_id, run.uut_serial_number, run.outcome)
    )

    for m in measurements:
        db_connection.execute(
            "INSERT INTO measurements (run_id, name, value) VALUES (?, ?, ?)",
            (run.test_run_id, m["measurement_name"], m["measurement_value"])
        )
```

`run` is a `RunSummary` — use attribute access (`run.test_run_id`, `run.uut_serial_number`, `run.outcome`). `measurements` is a list of dicts with the columns `get_measurements()` returns (`measurement_name`, `measurement_value`, `measurement_unit`, `measurement_outcome`, `limit_low`, `limit_high` — see [parquet-schema.md](../../reference/data/parquet-schema.md) for the full list).

## Upload sealed runs to cloud storage

Each run's parquet file is self-contained. The natural integration pattern is to mirror the runs directory to a bucket — one object per parquet file:

```python
import pathlib
import boto3
from testerkit import TesterKitClient

def upload_runs(data_dir: str, bucket: str, prefix: str = "test_results"):
    """Upload all sealed run parquets to S3, preserving the date-partitioned layout."""
    s3 = boto3.client("s3")
    runs_dir = pathlib.Path(data_dir) / "runs"

    for parquet_file in sorted(runs_dir.glob("**/*.parquet")):
        # Preserve: runs/{date}/{timestamp}_{run_id8}_{serial}.parquet
        relative = parquet_file.relative_to(runs_dir)
        s3_key = f"{prefix}/{relative}"
        s3.upload_file(str(parquet_file), bucket, s3_key)
```

TesterKit writes one self-contained parquet per run — no separate `test_runs/`, `measurements/`, or `vectors/` directories; upload each file as a single object. The schema is documented in [parquet-schema.md](../../reference/data/parquet-schema.md).

## Querying the existing store

For ad-hoc analysis, prefer the canonical reader surfaces first:

- `testerkit runs` — tabular view of recent runs in the terminal
- `testerkit show <run_id>` — per-run detail, with `-f html/pdf/json/csv` export
- HTTP `GET /api/runs` — machine-readable; see [api.md](../../reference/runtime/api.md)

For cross-run queries not covered by those surfaces, DuckDB can read the parquet files directly. This couples your query to the on-disk layout — treat it as an escape hatch:

```python
import duckdb

duckdb.sql("""
    SELECT uut_serial_number, step_name, measurement_outcome, COUNT(*)
    FROM read_parquet('<data_dir>/runs/**/*.parquet', union_by_name=true)
    GROUP BY uut_serial_number, step_name, measurement_outcome
""").show()
```

## Best practices

1. **Don't block the test on external syncs.** Run database or cloud-storage forwarders out-of-band against finished runs, not inline with `run.finish()`.
2. **Use `run_id` as the join key everywhere.** It is the stable identifier across the parquet file, the event log, channel data, and any downstream system.
3. **Read with `union_by_name=true`** when querying across multiple runs — the schema is additive across TesterKit versions, so this flag survives every release.
4. **Don't re-implement the schema downstream.** Mirror columns by name; let TesterKit stay canonical for the data shape.

## See also

- [Python client reference](../../reference/runtime/client.md) — full `TesterKitClient` API surface
- [Submitting results from non-pytest sources](results-api.md) — when to use which submission path
- [Parquet schema](../../reference/data/parquet-schema.md) — column-by-column reference
- [Data stores](../../concepts/data/data-stores.md) — on-disk layout, data_dir resolution, schema-evolution contract
- [HTTP / MCP API](../../reference/runtime/api.md) — REST + tool endpoints
