# Output Formats

Litmus produces test data through a fixed pipeline — three on-disk stores plus two CLI commands for post-hoc rendering and conversion.

## What's always on

```
test execution
    └→ events/   (Arrow IPC + DuckDB index — typed events, durable WAL)
    └→ runs/     (sealed per-run Parquet — analysis-ready, lakehouse-readable)
    └→ channels/ (Arrow IPC — time-series instrument samples)
    └→ files/    (captured artifacts — images, video, vendor files)
```

These four stores are the platform. They're populated automatically by every test run; there's no configuration knob to disable them. See [Data stores](../../concepts/data/data-stores.md) and [Event Log](../../concepts/data/event-log.md) for the data model.

## Reading runs back

Two CLI commands cover the post-hoc rendering and format-conversion paths:

### `litmus show <run_id> -f <format> [-o <path>]`

Renders a stored run for human consumption. Reads the run's parquet, formats output, writes to disk.

```bash
litmus show abc123                 # terminal pretty-print
litmus show abc123 -f html         # HTML report
litmus show abc123 -f pdf -o out/  # PDF (requires `litmus-test[pdf]`)
litmus show abc123 -f csv          # CSV
litmus show abc123 -f json         # JSON
```

### `litmus export <run_id> -f <format> [-o <dir>]`

Converts a stored run to industry data formats by replaying its events through the format converter.

```bash
litmus export abc123 -f csv                            # CSV (built-in)
litmus export abc123 -f json                           # JSON (built-in)
litmus export abc123 -f stdf -o exports/stdf/          # STDF (requires [stdf])
litmus export abc123 -f hdf5 -o exports/hdf5/          # HDF5 (requires [hdf5])
litmus export abc123 -f tdms -o exports/tdms/          # TDMS (requires [tdms])
litmus export abc123 -f mdf4 -o exports/mdf4/          # MDF4 (requires [mdf4])
```

## Available formats

| Format | Command | Library | Install | Industry |
|---|---|---|---|---|
| html | `litmus show` | jinja2 | *(built-in)* | Universal |
| pdf | `litmus show` | weasyprint | `pip install litmus-test[pdf]` | Universal |
| csv | both | stdlib | *(built-in)* | Universal |
| json | both | stdlib | *(built-in)* | Universal |
| stdf | `litmus export` | Semi-ATE-STDF | `pip install litmus-test[stdf]` | Semiconductor |
| hdf5 | `litmus export` | h5py | `pip install litmus-test[hdf5]` | Scientific |
| tdms | `litmus export` | npTDMS | `pip install litmus-test[tdms]` | NI/LabVIEW |
| mdf4 | `litmus export` | asammdf | `pip install litmus-test[mdf4]` | Automotive |

Bundle: `pip install litmus-test[all-exporters]` installs `stdf`, `hdf5`, `tdms`, `mdf4` together.

## Cloud destinations (S3, Snowflake, lakehouse)

For shipping data to cloud destinations or lakehouse formats (Snowflake, Delta, Iceberg), Litmus does not ship a built-in transport in the current release — design with real requirements is deferred to a future release. The parquet files in `runs/` are the contract. Consumers run their own pipeline:

- **DuckDB / Polars / Pandas:** read directly from `data/runs/{date}/*.parquet`. Rows are typed by `record_type` (`run` / `step` / `vector`); measurements are nested under the vector rows, so `UNNEST(measurements)` to flatten them.
- **Snowflake / Databricks / Trino-Iceberg:** copy parquets to your storage layer and ingest with a `record_type`-keyed split (`run` / `step` / `vector`), unnesting the vector rows' `measurements` list into your measurement fact table.

Canonical recipes — see [Lakehouse Import](../../integration/data/lakehouse-import.md).

## Subscribers — internal mechanism

The `EventSubscriber` class in `litmus.data.event_log` powers the exporter subscribers (`CsvSubscriber`, `JsonSubscriber`, `Hdf5Subscriber`, `Mdf4Subscriber`, `StdfSubscriber`) and the `litmus export` replay path. The canonical run parquet is produced by `materialize_run_to_parquet()` rather than a subscriber — it runs from the runs daemon's `AccumulatorPool` on `RunEnded`. This is not a public extension protocol: third-party packages should not register subscribers via entry points or any other mechanism. The set of supported formats is fixed by the package and surfaced through `litmus show` / `litmus export`.


## See also

**Related quadrants:**

- [Concepts → Data](../../concepts/data/index.md) — concepts entry point for this category
- [How-to → Data](../../how-to/data/index.md) — how-to entry point for this category
- [Integration → Data](../../integration/data/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
