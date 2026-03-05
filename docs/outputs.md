# Output Formats

Litmus has a three-layer output architecture: **Exporters** convert test results to industry file formats, **Transports** ship files to remote destinations, and **Streaming Destinations** write per-measurement in real time.

## Default Data Pipeline (zero-config)

```
test execution → JSONL (live streaming) → Parquet (session end) ← DuckDB (query) ← HTML/PDF (reports)
```

Parquet, JSONL journaling, and DuckDB queryability are always-on and not configurable. The `outputs` list in `litmus.yaml` controls **additional** outputs.

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
| stdf | Semi-ATE-STDF | `pip install litmus[stdf]` | Semiconductor |
| hdf5 | h5py | `pip install litmus[hdf5]` | Scientific, waveform |
| tdms | npTDMS | `pip install litmus[tdms]` | NI/LabVIEW |
| mdf4 | asammdf | `pip install litmus[mdf4]` | Automotive |
| atml | lxml | *(stdlib XML)* | Aerospace/defense |

## Available Transports

| Transport | Library | Install |
|---|---|---|
| file | stdlib shutil | *(built-in)* |
| s3 | boto3 | `pip install litmus[s3]` *(planned)* |
| gcs | google-cloud-storage | `pip install litmus[gcs]` *(planned)* |
| azure | azure-storage-blob | `pip install litmus[azure]` *(planned)* |
| sftp | paramiko | `pip install litmus[sftp]` *(planned)* |

## Streaming Destinations

Streaming destinations receive each measurement as a typed `MeasurementRow` model in real time, rather than waiting for the full `TestRun` at session end. Use streaming for formats that write incrementally or for database inserts. Any class implementing the `StreamingDestination` protocol can be wired in — see [Writing Custom Outputs](custom-outputs.md).

**Lifecycle:** `open(config, test_run)` → `append_row(row)` × N → `mark_run_boundary(run_id)` → `close()`

- `open()` receives the `OutputConfig` and the `TestRun` with run-level context (DUT serial, station, operator), so destinations can write run-level headers before any measurements arrive.
- `append_row()` receives a `MeasurementRow` with ~30 typed fields plus namespaced dynamic columns (`inputs`, `outputs`, `instruments`, `custom`). Call `row.to_flat_dict()` at the write boundary.
- Streaming destinations work with or without journaling enabled.

See [Writing Custom Outputs](custom-outputs.md) for implementation details.

## Bundles

```bash
pip install litmus[all-exporters]   # stdf + hdf5 + tdms + mdf4
```
