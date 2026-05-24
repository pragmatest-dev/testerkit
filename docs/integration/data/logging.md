# Logging integration

Send Litmus results onward to external systems — Python logging frameworks, databases, cloud storage. Litmus owns the parquet record; this page covers the bridges to other platforms.

For the underlying API to write into Litmus's store, see the [Python client reference](../../reference/runtime/client.md). For HTTP / MCP query endpoints, see [api.md](../../reference/runtime/api.md).

## Where the data already is

Results land in Parquet under `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet` regardless of which submission path you use (pytest plugin, `LitmusClient`, OpenHTF bridge — see [three-stores.md](../../concepts/data/three-stores.md) for the canonical layout and the `data_dir` resolution chain). The integration patterns below all read from that store and forward the data elsewhere.

For the on-write side, see:

- [Python client reference](../../reference/runtime/client.md) — `LitmusClient` API for submitting test runs from non-pytest sources
- [Submitting results from non-pytest sources](results-api.md) — when to use which submission path
- [Litmus fixtures](../../reference/pytest/fixtures.md) — the pytest plugin path (most projects)

## Python logging-framework bridge

Attach a `logging.Handler` that turns log records into step failures on the active run:

```python
import logging
from litmus.client import LitmusClient

class LitmusHandler(logging.Handler):
    """Forward warnings/errors to the active run as step failures."""

    def __init__(self, run):
        super().__init__()
        self.run = run
        self.step = None  # set by caller before emitting failing records

    def emit(self, record):
        if record.levelno >= logging.WARNING and self.step is not None:
            self.step.fail(record.getMessage())
```

Wire it up in the calling code:

```python
client = LitmusClient()
run = client.start_run(dut_serial="SN001", station_id="bench_1")
handler = LitmusHandler(run)
logging.getLogger("my_test").addHandler(handler)
```

## Sync to an external database

After a run finishes, push its summary + measurement rows into a SQL database:

```python
from litmus.client import LitmusClient

def sync_to_database(run_id: str, db_connection):
    """Mirror one Litmus run's summary + measurements into an external DB."""
    client = LitmusClient()
    run = client.get_run(run_id)              # RunSummary (Pydantic model)
    measurements = client.get_measurements(run_id)  # list[dict] keyed by parquet columns

    db_connection.execute(
        "INSERT INTO test_runs (id, serial, outcome) VALUES (?, ?, ?)",
        (run_id, run.dut_serial, run.outcome)
    )

    for m in measurements:
        db_connection.execute(
            "INSERT INTO measurements (run_id, name, value) VALUES (?, ?, ?)",
            (run_id, m["measurement_name"], m["measurement_value"])
        )
```

`run` is a Pydantic `RunSummary` — use attribute access. `measurements` is a list of dicts keyed by parquet column names (`measurement_name`, `measurement_value`, `measurement_units`, `measurement_outcome`, `limit_low`, `limit_high`, etc. — see [parquet-schema.md](../../reference/data/parquet-schema.md) for the full list).

## Upload a sealed run to cloud storage

Each run's parquet file is self-contained. Upload it as a single object:

```python
import boto3
from litmus.client import LitmusClient

def upload_results(run_id: str, bucket: str):
    """Upload the sealed run parquet to S3."""
    s3 = boto3.client("s3")
    client = LitmusClient()
    run = client.get_run(run_id)

    local_path = run.file_path                  # attribute on RunSummary
    s3_key = f"test_results/{run.dut_serial}/{run_id}.parquet"
    s3.upload_file(local_path, bucket, s3_key)
```

Litmus writes one parquet per run at `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet`. There is no separate `test_runs/`, `measurements/`, or `vectors/` directory — the multi-row schema (`record_type='run'` / `'step'` / `'measurement'`) lives inside the one file.

## Querying the existing store

For ad-hoc analysis (not external-system integration), prefer the canonical reader paths:

```python
import duckdb

# Cross-run query — DuckDB reads the parquet directly
duckdb.sql("""
    SELECT dut_serial, step_name, measurement_outcome, COUNT(*)
    FROM '<data_dir>/runs/**/*.parquet'
    GROUP BY dut_serial, step_name, measurement_outcome
""").show()
```

Or use `litmus runs` / `litmus show` / the HTTP API — see [results-api.md](results-api.md) for the routing.

## Best practices

1. **Don't block the test on external syncs.** Run database / cloud-storage forwarders out-of-band against finished runs, not inline with `run.finish()`.
2. **Use `run_id` as the join key everywhere.** It's the stable identifier across the parquet file, the event log, channel data, and any downstream system.
3. **Read with `union_by_name=true`** when querying across multiple runs — the schema is additive across litmus versions, so a query that uses this flag survives every release.
4. **Don't re-implement the schema downstream.** Mirror columns by name; let Litmus stay canonical for the data shape.

## See also

- [Python client reference](../../reference/runtime/client.md) — full `LitmusClient` API surface
- [Submitting results from non-pytest sources](results-api.md) — when to use which submission path
- [Parquet schema](../../reference/data/parquet-schema.md) — column-by-column reference
- [Three stores](../../concepts/data/three-stores.md) — on-disk layout, data_dir resolution, schema-evolution contract
- [HTTP / MCP API](../../reference/runtime/api.md) — REST + tool endpoints
