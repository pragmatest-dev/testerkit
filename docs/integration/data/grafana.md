# Grafana

The Grafana integration ships ten dashboards and a PostgreSQL-wire
data server that exposes every Litmus store as queryable SQL
tables. Grafana's built-in PostgreSQL data source connects to the
server — no plugin install required, no Litmus-specific shim on
the Grafana side.

For a step-by-step setup walkthrough (install the extras, start
the server, import the dashboards), see the
[Grafana dashboards how-to](../../how-to/data/grafana-dashboards.md).
This page is the reference for what the integration provides:
which dashboards exist, what tables they query, and where the
boundaries are.

## Architecture

```
litmus runs / measurements (parquet)   ┐
litmus events (Arrow IPC)              │── DuckDB connection
litmus channels (Arrow IPC)            ┘         │
                                                 │ pgwire
                                                 ▼
                                       Grafana PostgreSQL data source
                                                 │
                                                 ▼
                                       Ten provisioned dashboards
```

`litmus grafana serve` boots a buenavista pgwire server on port
5433 by default. The server creates an in-memory DuckDB connection
with:

- `measurements` — view over `<data_dir>/runs/**/*.parquet`
- `runs` — view that aggregates the measurements view to one row
  per run
- `events` — Arrow table loaded from `<data_dir>/events/*.arrow`
- `channels` — Arrow table loaded from `<data_dir>/channels/*.arrow`

Grafana queries these four tables (or three logical sources — the
runs view rolls up measurements) over the wire. Parquet views are
lazy and pick up new files between queries; Arrow tables refresh
every 30 seconds by default (`--refresh-seconds` overrides).

## CLI commands

| Command | Purpose |
|---|---|
| `litmus grafana serve` | Start the pgwire server. Options: `--host` (default `0.0.0.0`), `--port` (default `5433`), `--data-dir`, `--refresh-seconds` (default 30). |
| `litmus grafana setup` | Install the provisioning config + dashboards into a Grafana instance. Two modes: file-based (writes into a local Grafana install) or API-based (POSTs to a Grafana HTTP API — for Docker, remote, or Grafana Cloud). |
| `litmus grafana export` | Write the dashboard JSON files and provisioning Jinja2 templates to a directory. Useful when you want to inspect them, hand-edit, or check into your project's infra repo. |

All three commands require the `grafana` extras:
`uv pip install 'litmus-test[grafana]'`. The extras install
buenavista (the pgwire implementation) — without it, the import
in `litmus grafana serve` fails fast with a clear error.

## Ten shipped dashboards

All ten live under `src/litmus/grafana/dashboards/` as JSON files
that reference the data source through the `${DS_LITMUS}` template
variable. The setup commands substitute the data source UID at
import time.

| Dashboard | What it shows |
|---|---|
| **Yield Overview** | First-pass yield, pass / fail volume, overall yield metrics |
| **Failure Pareto** | Top failing steps and measurements ranked by failure count |
| **Measurement Distribution** | Histogram and SPC statistics (Cpk, Cp) for a selected measurement |
| **Measurement Trend** | Measurement values over time with limit lines |
| **Test Duration** | Test execution time trends, bottleneck steps, duration distribution |
| **Station Comparison** | Yield, throughput, and duration compared across test stations |
| **Unit Traceability** | Full test history and measurement detail for a specific serial number |
| **Asset Utilization** | Instrument usage, activity, and calibration status across sessions |
| **Event Log** | Event volume, session timeline, instrument activity from the event bus |
| **Channel Explorer** | Time-series visualization of instrument channel data by session |

Each dashboard targets the Litmus PostgreSQL data source. Variables
on the dashboard let operators pick part, station, time window,
serial number, etc. without editing panels.

## Customizing dashboards

Two patterns:

1. **Fork in place** — open a dashboard in Grafana, edit panels,
   save. The change persists in Grafana's own database. Next
   `litmus grafana setup` overwrites it unless you move it out of
   the Litmus folder first.

2. **Export, fork, re-import** — run `litmus grafana export -o my-dashboards/`,
   edit the JSON, manage with version control, import the forked
   versions yourself (Grafana's API or Grafana provisioning).

For panel-level reference (which SQL the panels run, which
variables drive which selectors), open the panel in Grafana and
inspect the query — every panel is a transparent SQL query over
the four tables above.

## Limitations and caveats

- **Refresh latency** — events and channels refresh into the
  DuckDB tables every 30 seconds (configurable). Live dashboards
  see new rows on the next refresh cycle, not immediately.
- **In-memory connection** — every `litmus grafana serve` process
  owns its own DuckDB connection. Don't run multiple servers
  pointing at the same data dir; they don't share state and
  Grafana would see one or the other.
- **Authentication** — the pgwire connection is set up under the
  `litmus` user. The API-based setup path (`litmus grafana setup
  --grafana-url ...`) also sends `litmus` as the password. Suitable
  for localhost or a trusted LAN; not for an exposed endpoint
  without further hardening.
- **PostgreSQL plugin requirement** — the bundled dashboards
  target `grafana-postgresql-datasource`. Grafana 10.x or later
  ships it built-in. Earlier versions need the plugin installed
  separately.
- **Schema drift** — the dashboards assume the current parquet
  schema. If Litmus's parquet columns change in a future release,
  the dashboards will need to be regenerated; the Litmus release
  notes will call out when that happens.

## See also

- [Grafana dashboards how-to](../../how-to/data/grafana-dashboards.md) —
  step-by-step setup, including Docker and Grafana Cloud variants
- [Parquet schema](../../reference/data/parquet-schema.md) — the columns
  the dashboards select
- [Three stores](../../concepts/data/three-stores.md) — where parquet,
  events, and channels live on disk
- [Find flaky tests](../../how-to/data/find-flaky-tests.md) — the
  diagnostic recipe that combines Yield + Failure Pareto +
  Measurement Trend on a single workflow
