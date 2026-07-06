---
name: litmus-analysis
description: Use when a user wants an answer out of existing Litmus test runs — yield, Pareto, Ppk, a trend, a retest rate, a single run's detail, or an export/report — not why one run failed (that's litmus-debug).
---

# Analyzing results

Runs already exist on disk; this skill's job is turning a question into the
right `litmus` call, Query API call, or MCP call — never a hand-rolled
`read_parquet` when a purpose-built path already exists.

## 1. Which tool for which question

| Question | Reach for |
|---|---|
| One run's detail | `litmus show <run_id>` |
| Recent runs, filtered | `litmus runs --since 7d --json` |
| Yield / Pareto / Ppk / trend / retest / time-loss | `litmus metrics <subcommand>` |
| Scripted / programmatic query | `RunsQuery` / `StepsQuery` / `MeasurementsQuery` |
| Remote or agent-driven | MCP `litmus_runs` / `litmus_steps` / `litmus_metrics` |
| A query shape none of the above expose | ad-hoc DuckDB over `data/runs/**/*.parquet` (last resort) |

## 2. Per-run

```bash
litmus runs --station bench_01 --since 7d --json
litmus show <run_id>                       # terminal: outcome, steps, measurements
litmus show <run_id> -f html -o report.html
litmus show <run_id> --env                 # captured environment snapshot
litmus export <run_id> -f stdf -o exports/stdf/
litmus sbom <run_id> -o sbom.json          # CycloneDX 1.6
```

`litmus show -f` accepts `html|pdf|json|csv`. `litmus export`'s `id` is a
run_id or session_id prefix, auto-detected from stored events.

## 3. `litmus metrics` — manufacturing analytics

```bash
litmus metrics summary --station bench_01 --since 7d --json
litmus metrics pareto --group-by step --top 5
litmus metrics ppk --part PN-1042 --min-samples 30
litmus metrics trend --period week
litmus metrics retest
litmus metrics time-loss
```

| Subcommand | Answers |
|---|---|
| `summary` | FPY, final yield, run counts, RTY, DPMO, DPPM, avg duration |
| `pareto --group-by part\|step\|measurement` | Top-N failing parts / steps / measurements |
| `ppk --min-samples N` | Ppk/Pp per (part, station, measurement, characteristic, pin) |
| `trend --period day\|week\|month` | Yield % bucketed over time |
| `retest` | Retested-serial count, retest rate, avg retries |
| `time-loss` | Total/pass/fail/error seconds per period |

Every subcommand shares one filter set: `--since`/`--until` (relative
`7d`/`4h`/`30m` or ISO, `--utc` for UTC interpretation), `--part`
(`uut_part_number`), `--station` (`station_hostname` — never
`station_id`/`station_name`), `--phase` (omitted excludes `development`,
`all` = no filter), `--json`.

## 4. Query API (scripted)

```python
from litmus.queries import RunsQuery, MeasurementsQuery, FieldRef

with RunsQuery() as q:
    for r in q.list_recent(limit=20, outcome="failed"):
        print(r.uut_serial_number, r.station_hostname, r.outcome)

with MeasurementsQuery() as m:
    m.ppk(FieldRef.measurement("v_rail"))
    m.histogram(field=FieldRef.output("capture_length"))
    m.parametric(y=FieldRef.measurement("v_rail"), x=FieldRef.input("vin"))
```

At rest, `inputs`/`outputs`/`measurements` are nested `list<struct>`
lanes — reference a value by **role + name** via `FieldRef.measurement`/
`.output`/`.input`, never a prefixed column. `ppk()` rejects a
non-measurement `FieldRef` — outputs/inputs have no limit to compute
against. Opening a query class with no args reads the active project's
data dir; always close it (or use `with`).

## 5. MCP equivalents

| MCP tool | Mirrors |
|---|---|
| `litmus_runs(action="list"\|"get", run_id=...)` | `litmus runs` / `RunsQuery` |
| `litmus_steps(run_id=..., action="list"\|"tree")` | `StepsQuery.list_for_run` / `.tree_for_run` |
| `litmus_metrics(action="summary"\|"pareto"\|"ppk"\|"trend"\|"retest"\|"time_loss", ...)` | `litmus metrics <subcommand>` (note the underscore in `time_loss` vs the CLI's hyphen) |

## 6. Grafana / dashboards

```bash
litmus grafana serve                                     # PostgreSQL-wire server over data/
litmus grafana setup --grafana-home <dir>                # provision datasource + dashboards, local
litmus grafana export -o <dir>                            # dump dashboard JSON for manual setup
```

## Best-practice defaults

- **CLI `--json` or the Query API, not raw `read_parquet`** — both already
  exclude `development`-phase noise and stay stable across schema changes.
  Reach for DuckDB over `data/runs/**/*.parquet` only when the question
  needs `UNNEST` gymnastics neither surface exposes.
- **`litmus serve` → `/metrics`** for a human — same
  `MeasurementsQuery`/`StepsQuery`/`RunsQuery` classes as every CLI/MCP
  call above, so anything scriptable here is also clickable there.

## Deeper
Read the docs:
```bash
litmus docs show how-to/data/export-results
litmus docs show how-to/data/grafana-dashboards
litmus docs show how-to/data/compare-runs
litmus docs show how-to/data/benchmarking
```
Sibling skills: `litmus-debug` (why one run failed), `litmus-capture`
(channels/files these runs may reference), `litmus-tests` (what produced
the rows being queried).
