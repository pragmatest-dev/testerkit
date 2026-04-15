# Output Formats

Litmus has a multi-layer output architecture: the **Event Log** captures all test activity as typed events, **ParquetSubscriber** materializes analysis-ready Parquet files, **Exporters** convert results to industry file formats, and **Transports** ship files to remote destinations.

## Default Data Pipeline (zero-config)

```
test execution → EventLog (typed events) → ParquetSubscriber → Parquet (per-run) ← DuckDB (query) ← HTML/PDF (reports)
                      │
                      └→ Arrow IPC files (crash-safe) + DuckDB via Flight (live queries)
```

The event log, Parquet materialization, and DuckDB queryability are always-on and not configurable. The `outputs` list in `litmus.yaml` controls **additional** outputs. See [Event Log Architecture](concepts/event-log.md) and [Three Stores](concepts/three-stores.md) for details.

## Configuration

Add an `outputs` list to `litmus.yaml`:

```yaml
# litmus.yaml
name: my_project
outputs:
  # Human-readable reports
  - format: html
  - format: pdf

  # Machine-readable exports
  - format: csv
  - format: stdf
    output_dir: results/stdf/

  # Export + ship to remote
  - format: csv
    transport: s3
    bucket: my-results
    prefix: csv/

  # Ship Parquet directly (no format conversion)
  - transport: snowflake
    account_env: SNOWFLAKE_ACCOUNT
```

### Output entry fields

| Field | Description |
|---|---|
| `format` | Exporter or report format (html, pdf, csv, json, stdf, hdf5, tdms, mdf4, atml) |
| `transport` | Transport to ship the file (file, s3, gcs, azure, sftp) |
| `output_dir` | Override default output directory |
| `template` | Jinja2 template name (for html/pdf reports) |
| *(extra keys)* | Collected into `OutputConfig.extras` dict (bucket, prefix, dsn_env, etc.) |

### Default output directories

| Format | Default path |
|---|---|
| html, pdf | `reports/` |
| csv, json, stdf, hdf5, tdms, mdf4, atml | `results/exports/{format}/` |

## CLI Commands

### Export a stored run

```bash
# Export to CSV
litmus export abc123 -f csv

# Export to STDF with custom output dir
litmus export abc123 -f stdf -o results/stdf/

# Export and ship via transport
litmus export abc123 -f csv --transport s3
```

### Convert a Parquet file directly

```bash
# File-to-file conversion (no test session needed)
litmus convert results/runs/2026-03-04/abc123.parquet -f csv
litmus convert foo.parquet -f stdf -o /shared/stdf/
```

## Available Formats

| Format | Library | Install | Industry |
|---|---|---|---|
| csv | stdlib | *(built-in)* | Universal |
| json | stdlib | *(built-in)* | Universal |
| stdf | Semi-ATE-STDF | `pip install litmus-test[stdf]` | Semiconductor |
| hdf5 | h5py | `pip install litmus-test[hdf5]` | Scientific, waveform |
| tdms | npTDMS | `pip install litmus-test[tdms]` | NI/LabVIEW |
| mdf4 | asammdf | `pip install litmus-test[mdf4]` | Automotive |
| atml | lxml | *(stdlib XML)* | Aerospace/defense |

## Available Transports

| Transport | Library | Install |
|---|---|---|
| file | stdlib shutil | *(built-in)* |
| s3 | boto3 | `pip install litmus-test[s3]` *(planned)* |
| gcs | google-cloud-storage | `pip install litmus-test[gcs]` *(planned)* |
| azure | azure-storage-blob | `pip install litmus-test[azure]` *(planned)* |
| sftp | paramiko | `pip install litmus-test[sftp]` *(planned)* |

## Event Subscribers (Real-Time Processing)

The event log dispatches typed events to subscribers in real time. This replaces the earlier `StreamingDestination` protocol. Any class implementing the `EventSubscriber` protocol receives events as they are emitted.

**Lifecycle:** `open()` → `on_event(event)` × N → `close()`

- Subscribers declare which `event_types` they handle
- `on_event()` receives typed Pydantic event models (not raw dicts)
- Built-in subscribers: `ParquetSubscriber` (materializes Parquet), `SessionSubscriber` (tracks sessions)

See [Subscribing to Events](guides/subscribing-to-events.md) for implementation details and the `EventSubscriber` protocol.

## Bundles

```bash
pip install litmus-test[all-exporters]   # stdf + hdf5 + tdms + mdf4
```
