# Analyzing results (metrics, queries, exports, reports)

Once runs exist on disk, this card is "how do I get an answer out of them" —
CLI analytics, the Python Query API, ad-hoc DuckDB, per-run reports, and
exports. For "which tool for this request" first, see
`litmus refs show routing`. For what a run/step/measurement row actually
contains, see `litmus refs show verify` and `litmus refs show observe`.

## `litmus metrics` — which question, which subcommand

All subcommands read through `MeasurementsQuery` (`src/litmus/analysis/measurements_query.py`),
except `pareto --group-by step` (`StepsQuery`) and `pareto --group-by part`
(`RunsQuery`).

| Question | Subcommand | Answers with |
|---|---|---|
| What's my yield? | `litmus metrics summary` | FPY, final yield, run counts, RTY, DPMO, DPPM, avg duration |
| What fails most? | `litmus metrics pareto --group-by part\|step\|measurement` | Top-N failing parts / steps / measurements, count + rate |
| Is the process capable? | `litmus metrics ppk --min-samples N` | Ppk/Pp per (part, station, measurement, characteristic, pin) population |
| Is yield drifting? | `litmus metrics trend --period day\|week\|month` | Yield % bucketed over time |
| How much retest is happening? | `litmus metrics retest` | Retested-serial count, retest rate, avg retries per period |
| What is failure costing in time? | `litmus metrics time-loss` | Total/pass/fail/error seconds per period |

`pareto --group-by` picks the lens: `part` groups by `uut_part_number`
(worst SKUs), `step` groups by `step_path` (worst tests), `measurement`
(the historical default) groups limit-bearing measurements by name.

## Shared filters

Every subcommand takes the same filter set (`_base_filters` in
`src/litmus/cli/metrics.py`):

| Flag | Filters on | Notes |
|---|---|---|
| `--since` / `--until` | run start time | relative (`7d`, `4h`, `30m`) or ISO date/datetime; bare values are local time unless `--utc` |
| `--part` | `uut_part_number` | single value or repeat for OR |
| `--station` | `station_hostname` (coalesced) | never `station_id`/`station_name` |
| `--phase` | test phase | omitted = excludes `development`; `all` = no phase filter |
| `--utc` | `--since`/`--until` interpretation | also `LITMUS_UTC=1` |
| `--json` | output shape | prints `[]` on no data, never bare "No data" |
| `--period` | `day\|week\|month` | `summary`, `trend`, `retest`, `time-loss` only |

```bash
litmus metrics summary --station bench_01 --since 7d --json
litmus metrics pareto --group-by step --top 5
litmus metrics ppk --part PN-1042 --min-samples 30
```

## Querying data: the public Query API

Three classes, one per materialized table — `RunsQuery`, `StepsQuery`,
`MeasurementsQuery` (`from litmus.queries import ...`). Same daemon the CLI
and operator UI use; opening one with no args reads the active project's
data dir. Always close it (or use a `with` block):

```python
from litmus.queries import RunsQuery, MeasurementsQuery

with RunsQuery() as q:
    for r in q.list_recent(limit=20, outcome="failed"):
        print(r.uut_serial_number, r.station_hostname, r.outcome)

with MeasurementsQuery() as m:
    for row in m.yield_summary(station="bench_01", period="week"):
        print(row.period, row.passed, row.rty, row.dpmo)
```

At rest, a run's `inputs` / `outputs` / `measurements` are nested
`list<struct>` lanes, not flat `in_*`/`out_*` columns — you reference a
value by **role + name**, never a prefixed column. `FieldRef` is that
reference:

```python
from litmus.queries import FieldRef

m.ppk(FieldRef.measurement("v_rail"))               # measurement, limit-bearing
m.histogram(field=FieldRef.output("capture_length")) # output, no limit
m.parametric(y=FieldRef.measurement("v_rail"), x=FieldRef.input("vin"))
```

`ppk()` rejects a non-measurement `FieldRef` — outputs and inputs have no
limit to compute Ppk against. A bare string is shorthand for
`FieldRef.measurement(name)`.

Full method list (`list_recent`, `get`, `pareto`, `yield_summary`,
`trend`, `retest`, `time_loss`, `parametric`, `histogram`, ...):
`docs/reference/data/query-api.md`.

## Ad-hoc DuckDB over the parquet files

For a one-off query outside the Query API, DuckDB reads `data/runs/**/*.parquet`
directly — but the nested lanes need `UNNEST`, and there's no automatic
`development`-phase exclusion:

```python
import duckdb

duckdb.sql("""
    SELECT v.uut_serial_number, v.station_hostname, m.name, m.value, m.outcome
    FROM read_parquet('data/runs/**/*.parquet') AS v,
         UNNEST(v.measurements) AS t(m)
    WHERE v.record_type IN ('step', 'vector')
""").df()
```

Prefer the Query API when it covers the question — it handles the UNNEST,
phase default, and daemon-backed freshness for you. Reach for raw
`read_parquet` only when you need a query shape the Query API doesn't expose.

## Per-run: `litmus runs` / `litmus show` / `litmus export` / SBOM

| Command | Does |
|---|---|
| `litmus runs --limit 20 --json` | List recent runs (`RunsQuery.list_recent`) |
| `litmus show <run_id>` | Terminal summary: outcome, steps, measurements |
| `litmus show <run_id> -f html\|pdf\|json\|csv -o <path>` | Generate a report via `litmus.reports.core.generate_report` |
| `litmus show <run_id> --env` | Environment snapshot (packages, versions) captured at run time |
| `litmus export <id> -f csv\|json\|stdf\|hdf5\|tdms\|mdf4` | Replay a run/session's events into another format |
| `litmus sbom <run_id> -o sbom.json` | CycloneDX 1.6 SBOM from the run's captured environment |

`export`'s `id` is a run_id or session_id prefix, auto-detected from stored
events.

## Grafana

`litmus grafana serve` starts a PostgreSQL-wire-protocol server over `data/`
(runs, events, channels); `litmus grafana setup --grafana-home <dir>` (local)
or `--grafana-url <url> --grafana-token <token>` (API) provisions the
datasource and ships dashboards. `litmus grafana export -o <dir>` dumps the
dashboard JSON + provisioning templates for manual setup.

## MCP equivalents

| MCP tool | Mirrors |
|---|---|
| `litmus_runs(action="list"\|"get", run_id=...)` | `litmus runs` / `RunsQuery` |
| `litmus_steps(run_id=..., action="list"\|"tree")` | `StepsQuery.list_for_run` / `.tree_for_run` |
| `litmus_metrics(action="summary"\|"pareto"\|"ppk"\|"trend"\|"retest"\|"time_loss", ...)` | `litmus metrics <subcommand>` (note: `time_loss` uses an underscore in the MCP action, a hyphen on the CLI) |

## Best practice

Prefer `litmus metrics ... --json` or the Query API over hand-rolled
`read_parquet` — both already exclude `development`-phase noise by default
and stay stable across schema changes. Point a human at `litmus serve` →
`/metrics` (Yield / Pareto / Ppk / Retest / Time loss / Assets tabs) — it
reads through the same `MeasurementsQuery`/`StepsQuery`/`RunsQuery` classes,
so anything scriptable here is also a page a test engineer can click through.
