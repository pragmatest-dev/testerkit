# Grafana Dashboards

Visualize test results, events, and instrument channel data with pre-built Grafana dashboards.

## Overview

Litmus ships 10 Grafana dashboards that query all three data stores:

- **Parquet** (runs, measurements) — yield, duration, failure analysis, traceability
- **Arrow IPC** (events) — session timeline, instrument activity, dialogs
- **Arrow IPC** (channels) — time-series instrument data, channel statistics

No Grafana plugins required. Litmus includes a `pgwire` server (the PostgreSQL wire protocol — what every PostgreSQL client speaks — implemented over DuckDB) that Grafana's built-in PostgreSQL datasource connects to directly.

## Quick Start

### 1. Install Grafana extras

```bash
pip install litmus-test[grafana]
# or
uv add litmus-test[grafana]
```

### 2. Start the data server

```bash
litmus grafana serve
```

This starts a PostgreSQL-compatible server on port 5433 that exposes all Litmus data stores as SQL tables.

### 3. Set up dashboards

**API-based** (Docker, remote, Grafana Cloud):

```bash
# Basic auth
litmus grafana setup \
    --grafana-url http://localhost:3000 \
    --grafana-user admin --grafana-password admin

# API token
litmus grafana setup \
    --grafana-url http://localhost:3000 \
    --grafana-token glsa_xxxxxxxxxxxx
```

**File-based** (local Grafana install):

```bash
litmus grafana setup --grafana-home /usr/share/grafana
```

This creates the datasource, a "Litmus" folder, and imports all 10 dashboards.

## Dashboards

### Yield Overview
Overall yield gauge, pass/fail/error counts, first-pass yield over time, outcome breakdown pie chart, and volume bar chart. Filter by phase, product, and station.

### Failure Pareto
Top failing steps ranked by failure count with failure rate percentages. Identifies the most impactful test failures.

### Measurement Distribution
Histogram of measurement values with statistical summary (mean, sigma, Cp, Cpk). Select any measurement by name.

### Measurement Trend
Scatter plot of measurement values over time with limit lines (low, high, nominal). Select any measurement by name.

### Station Comparison
Yield and duration comparison across test stations. Identifies station-level performance differences.

### Test Duration
Average and P95 test duration over time, duration by step (identifies bottlenecks), and duration distribution histogram.

### Unit Traceability
Full test history for a specific serial number — all runs and measurements. Select by DUT serial.

### Event Log
Event volume over time, event type breakdown, recent sessions with cross-store joins (event count + channel samples per session), instrument activity, and dialog events. Filter by event type.

### Channel Explorer
Time-series visualization of instrument channel data. Per-session statistics (mean, min, max, stddev), channel volume over time. Select by channel name.

### Asset Utilization
Instrument inventory with identity, calibration status, and total usage time. Activity breakdown by instrument, sessions per instrument, and recent operations. Filter by instrument role.

## Architecture

```
Grafana (PostgreSQL datasource)
    |
    | PostgreSQL wire protocol
    v
litmus grafana serve (Buena Vista + DuckDB)
    |
    |-- read_parquet('<data_dir>/runs/**/*.parquet')  --> measurements, runs
    |-- Arrow IPC (<data_dir>/events/**/*.arrow)      --> events
    |-- Arrow IPC (<data_dir>/channels/**/*.arrow)    --> channels
```

All timestamps are stored as UTC and converted to naive UTC timestamps at the pgwire layer for Grafana compatibility.

The data server auto-refreshes Arrow IPC tables every 30 seconds (configurable with `--refresh`). Parquet views are always live (DuckDB reads on query).

## SQL Tables

| Table | Source | Description |
|-------|--------|-------------|
| `measurements` | Parquet | One row per measurement with full denormalized metadata |
| `runs` | Parquet (VIEW) | One row per run — aggregated from measurements |
| `events` | Arrow IPC | All event bus events with JSON payload |
| `channels` | Arrow IPC | Instrument channel time-series data |

## CLI Reference

```
litmus grafana serve [OPTIONS]
    --host TEXT       Bind address (default: 0.0.0.0)
    --port INTEGER    pgwire port (default: 5433)
    --data-dir PATH
    --refresh-seconds INTEGER  Seconds between IPC refreshes (default: 30)

litmus grafana setup [OPTIONS]
    --grafana-url TEXT        API mode: Grafana URL
    --grafana-token TEXT      API mode: bearer token
    --grafana-user TEXT       API mode: basic auth username
    --grafana-password TEXT   API mode: basic auth password
    --grafana-home DIRECTORY  File mode: Grafana install dir
    --host TEXT               pgwire host for datasource (default: 127.0.0.1)
    --port INTEGER            pgwire port for datasource (default: 5433)
    --folder TEXT             Dashboard folder name (default: Litmus)

litmus grafana export [OPTIONS]
    --output-dir PATH  Export dashboards and templates (default: grafana-export)
```
