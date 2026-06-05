# Stage 11 — Querying data (consumer side / data analyst)

The "I produced the data, now what?" example. Once tests have run
and data is on disk, how do analysts, external tools, and custom
dashboards FIND it? UI is half the story (`/runs`, `/metrics`,
`/events`, `/channels`, `/files`); the public Query API is the other
half — programmatic access for analysis scripts, ETL, MCP tools,
external dashboards.

## What this example does

`scripts/seed_runs.py` populates `data/` with 8 fake runs across 5
DUTs and 2 stations (using `LitmusClient` — the programmatic
run-building API). `scripts/analyze.py` then queries them through
the public API:

- **`RunsQuery`** — list recent runs, filter by DUT serial, count
  outcomes
- **`MeasurementsQuery`** — yield summary by `(product, station,
  phase)`, distinct DUT serials, parametric queries
- **`EventStore`** — replay the lifecycle event timeline

Output is plain text so you can run it in any terminal. Replace the
print loops with pandas / matplotlib / plotly for real analysis;
the queries return native Python objects (Pydantic `RunRow`,
`MeasurementRow`, dicts) that plug into whatever downstream
visualization you use.

## Layout

```
examples/11-querying-data/
├── README.md
├── litmus.yaml
├── pyproject.toml
└── scripts/
    ├── seed_runs.py        # produce: 8 runs, 5 DUTs, 2 stations
    └── analyze.py          # consume: list / filter / yield / events
```

## Run it

```bash
cd examples/11-querying-data
uv run python scripts/seed_runs.py    # ~10 s — seeds 8 runs with 1 s spacing
uv run python scripts/analyze.py      # waits for daemon ingest, then prints
```

The analyze script polls `RunsQuery` until the runs daemon has
ingested the freshly-seeded parquets (typically a few seconds; the
script waits up to 30 s).

## Two halves of discovery

The same data is reachable two ways. This example shows the
programmatic half:

```python
from litmus.queries import RunsQuery

with RunsQuery(_data_dir="data") as q:
    for run in q.list_recent(limit=20):
        print(run.dut_serial, run.outcome)
```

For the UI half, run `uv run litmus serve --reload` and navigate:

| Page | What it shows |
|---|---|
| `/runs` | Same `RunsQuery` data, clickable rows → per-run detail |
| `/metrics` | Yield + Pareto + Cpk views (uses `MeasurementsQuery`) |
| `/measurements` | Full parametric query (filter by characteristic, DUT, station, time) |
| `/events` | Timeline replay (uses `EventStore`) |
| `/channels` | At-rest channel data (uses `ChannelStore`) |
| `/files` | At-rest artifacts (uses `FileStore`) |

The UI and the Query API read through the SAME primitives. Anything
you can do programmatically is reachable through the UI; anything
you click in the UI has a matching Python API call.

## Why deep imports here

This example is consumer-side code — not a pytest test. Per the
established import policy (re-exports reserved for test-author
constructs like `Limit` and the sweep value builders), interactive
and data-mover code uses deep imports:

```python
from litmus.queries import RunsQuery
from litmus.queries import MeasurementsQuery
from litmus.queries import EventStore
from litmus import LitmusClient
```

The verbosity signals "store-direct layer." Full API-surface
reorganization (more top-level re-exports for non-test consumers)
is queued as a follow-on PR — when it lands these imports collapse
to `from litmus import queries, EventStore, LitmusClient`.

## See also

- [Concepts → Three stores](../../docs/concepts/data/three-stores.md)
  — what lives where (Parquet runs, ChannelStore, FileStore, EventStore)
- [How-to — Querying channels](../../docs/how-to/data/querying-channels.md)
- [How-to — Querying events](../../docs/how-to/data/querying-events.md)
- [How-to — Compare two runs](../../docs/how-to/data/compare-runs.md)
- [Reference — Query API](../../docs/reference/data/query-api.md)
