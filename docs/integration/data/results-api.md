# Submitting results from non-pytest sources

When the test isn't a pytest function — LabVIEW, TestStand, a custom script, a legacy framework — use the Python `LitmusClient` to push results into the same store the pytest plugin writes to. The same `litmus runs`, `litmus serve`, and DuckDB queries see them.

## Canonical reference

The full API surface — `LitmusClient`, `RunBuilder`, `StepBuilder`, `VectorBuilder`, every method signature, the LabVIEW / TestStand / CLI integration patterns — lives on the [Python client reference](../../reference/runtime/client.md). This page is the integration-level entry point; that page is the API.

## When to use which path

| You have | Use |
|---|---|
| Python code with the results in hand | [`LitmusClient`](../../reference/runtime/client.md) — chained builder, writes directly to the store |
| pytest tests | The pytest plugin, NOT this — see [Litmus fixtures](../../reference/pytest/fixtures.md) |
| Shell script or non-Python toolchain | Wrap the Python client in a one-file CLI; call it via subprocess |
| LabVIEW | [LabVIEW pattern in `client.md`](../../reference/runtime/client.md#from-labview) — Python Node call |
| TestStand | [TestStand pattern in `client.md`](../../reference/runtime/client.md#from-teststand) — Python adapter |

## HTTP API caveat

`POST /api/runs` does NOT accept submitted results. It launches a pytest subprocess against a `test_path`. For results submission from non-Python sources, the supported path is the Python `LitmusClient` (wrapped behind a thin CLI or subprocess if needed). An HTTP results-submission endpoint is not currently shipped — see the open follow-up.

## Querying results

Once results are in the store, query through any of:

- `client.list_runs()` / `client.get_run()` / `client.get_measurements()` — see [client.md](../../reference/runtime/client.md#querying-results)
- CLI: `litmus runs`, `litmus show <run_id>` — see [cli.md](../../reference/cli.md)
- HTTP: `GET /api/runs`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/measurements` — see [api.md](../../reference/runtime/api.md)
- Raw parquet via DuckDB / pandas / Polars — see [parquet-schema.md](../../reference/data/parquet-schema.md) for columns and [data-stores.md](../../concepts/data/data-stores.md) for the on-disk layout

## See also

- [Python client reference](../../reference/runtime/client.md) — full API, integration patterns, examples
- [Logging integration](logging.md) — sending results onward to external systems (S3, databases, Python logging)
- [Parquet schema](../../reference/data/parquet-schema.md) — column-by-column reference for the stored data
- [Data stores](../../concepts/data/data-stores.md) — where the data lives, data_dir resolution, schema-evolution contract
