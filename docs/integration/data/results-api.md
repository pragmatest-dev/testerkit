# Submitting results from non-pytest sources

When the test isn't a pytest function — LabVIEW, TestStand, a custom script, a legacy framework — use the Python `TesterKitClient` to write results into the same store every TesterKit runner writes to. The same `testerkit runs`, `testerkit serve`, and DuckDB queries see them.

## Submit a result

```python
from testerkit import TesterKitClient

client = TesterKitClient()
run = client.start_run(uut_serial="SN12345", station_id="bench_1", test_phase="production")
with run.step("voltage_check") as step:
    step.measure("vcc", 3.31, unit="V", low=3.0, high=3.6)
run.finish()
```

Wrap this in a one-file CLI to call it from a toolchain that can't run Python inline (LabVIEW Python Node, TestStand Python adapter, or `subprocess`).

## Canonical reference

The full API surface — `TesterKitClient`, `RunBuilder`, `StepBuilder`, `VectorBuilder`, and the LabVIEW / TestStand / CLI integration patterns — lives on the [Python client reference](../../reference/runtime/client.md). This page is the integration-level entry point; that page is the API.

## When to use which path

| You have | Use |
|---|---|
| Python code with the results in hand | [`TesterKitClient`](../../reference/runtime/client.md) — chained builder, writes directly to the store |
| pytest tests | The pytest plugin, NOT this — see [TesterKit fixtures](../../reference/pytest/fixtures.md) |
| Shell script or non-Python toolchain | Wrap the Python client in a one-file CLI; call it via subprocess |
| LabVIEW | [LabVIEW pattern in `client.md`](../../reference/runtime/client.md#from-labview) — Python Node call |
| TestStand | [TestStand pattern in `client.md`](../../reference/runtime/client.md#from-teststand) — Python adapter |

## HTTP API caveat

`POST /api/runs` does NOT accept submitted results — it launches a pytest subprocess against a `test_path`. To submit results from a non-Python source, wrap the `TesterKitClient` snippet above in a one-file CLI and call it from your toolchain. A direct HTTP results-submission endpoint is not currently available.

## Querying results

Once results are in the store, query through any of:

- `client.list_runs()` / `client.get_run()` / `client.get_measurements()` — see [client.md](../../reference/runtime/client.md#querying-results)
- CLI: `testerkit runs`, `testerkit show <run_id>` — see [cli.md](../../reference/cli.md)
- HTTP: `GET /api/runs`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/measurements` — see [api.md](../../reference/runtime/api.md)
- Raw parquet via DuckDB / pandas / Polars — see [parquet-schema.md](../../reference/data/parquet-schema.md) for columns and [data-stores.md](../../concepts/data/data-stores.md) for the on-disk layout

## See also

- [Python client reference](../../reference/runtime/client.md) — full API, integration patterns, examples
- [Logging integration](logging.md) — sending results onward to external systems (S3, databases, Python logging)
- [Parquet schema](../../reference/data/parquet-schema.md) — column-by-column reference for the stored data
- [Data stores](../../concepts/data/data-stores.md) — where the data lives, data_dir resolution, schema-evolution contract
