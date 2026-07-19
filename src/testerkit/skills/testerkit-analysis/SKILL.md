---
name: testerkit-analysis
description: Use when a user wants a computed statistic across TesterKit runs — yield (FPY/RTY/DPMO), a Pareto of top failures, Ppk/Pp capability, a yield trend, a retest rate, or time-loss. Reading or exporting the underlying records is testerkit-data; why one run failed is testerkit-debug.
---

# Analyzing results

The runs exist; this skill turns a question into the right **metric** — a
statistic computed across many runs. Reading the raw records, one run's
detail, or an export is `testerkit-data`; diagnosing a single failure is
`testerkit-debug`.

## 1. Which metric

| Question | Reach for |
|---|---|
| First-pass / final yield, RTY, DPMO, DPPM, counts | `testerkit metrics summary` |
| Top-N failing parts / steps / measurements | `testerkit metrics pareto --group-by part\|step\|measurement` |
| Process capability (Ppk / Pp) | `testerkit metrics ppk --min-samples N` |
| Yield bucketed over time | `testerkit metrics trend --period day\|week\|month` |
| Retested-serial count, retest rate, avg retries | `testerkit metrics retest` |
| Total/pass/fail/error seconds per period | `testerkit metrics time-loss` |

## 2. `testerkit metrics`

```bash
testerkit metrics summary --station bench_01 --since 7d --json
testerkit metrics pareto --group-by step --top 5
testerkit metrics ppk --part PN-1042 --min-samples 30
testerkit metrics trend --period week
testerkit metrics retest
testerkit metrics time-loss
```

Every subcommand shares one filter set: `--since`/`--until` (relative
`7d`/`4h`/`30m` or ISO, `--utc` for UTC interpretation), `--part`
(`uut_part_number`), `--station` (`station_hostname` — never
`station_id`/`station_name`), `--phase` (omitted excludes `development`,
`all` = no filter), `--json`.

## 3. Query API (scripted)

```python
from testerkit.queries import MeasurementsQuery, FieldRef

with MeasurementsQuery() as m:
    m.yield_summary()                           # FPY / final yield / counts
    m.ppk(FieldRef.measurement("v_rail"))       # capability; rejects a non-measurement ref
```

`ppk()` rejects a non-measurement `FieldRef` — outputs/inputs have no limit to
compute against. Opening a query class with no args reads the active project's
data dir; always close it (or use `with`). To pull the raw rows behind a
metric, use `testerkit-data`.

## 4. MCP

| MCP tool | Mirrors |
|---|---|
| `testerkit_metrics(action="summary"\|"pareto"\|"ppk"\|"trend"\|"retest"\|"time_loss", ...)` | `testerkit metrics <subcommand>` (note the underscore in `time_loss` vs the CLI's hyphen) |

## 5. Grafana / dashboards

```bash
testerkit grafana serve                                     # PostgreSQL-wire server over data/
testerkit grafana setup --grafana-home <dir>                # provision datasource + dashboards, local
testerkit grafana export -o <dir>                            # dump dashboard JSON for manual setup
```

## Best-practice defaults

- **Metrics already exclude `development`-phase noise** and stay stable across
  schema changes — reach for `testerkit metrics` or the Query API, never a
  hand-rolled aggregate over the data files.
- **`testerkit serve` → `/metrics`** for a human — the same `MeasurementsQuery`
  the CLI and MCP calls use, so anything scriptable here is also clickable there.

## Deeper
Read the docs:
```bash
testerkit docs show how-to/data/benchmarking
testerkit docs show how-to/data/grafana-dashboards
testerkit docs show how-to/data/compare-runs
```
Sibling skills: `testerkit-data` (read/export the records these metrics compute
over), `testerkit-debug` (why one run failed), `testerkit-capture` (channels/files
these runs reference).
