# Litmus Examples

Three progressively richer examples. Each opens in a fresh IDE and runs
green against mocked instruments. Read them in order — the test bodies
stay nearly identical across tiers; the YAML layers around them grow.

| Tier | Directory | What it shows |
|------|-----------|---------------|
| **1 — Bringup** | `01-bringup/` | One test file, one `conftest.py`, no station / product / fixture YAML. Limits inline or in a sidecar. The smallest useful Litmus project. |
| **2 — Station** | `02-station/` | Add `stations/`, `products/`, `fixtures/`. Instrument fixtures auto-register from the station. Limits resolve from product characteristics. |
| **3 — Profiles** | `03-profiles/` | Add `catalog/`, profiles (`production` / `characterization`), multi-pin iteration, binding-aware limits. The full production flow. |

## Running

Each tier is self-contained. `cd` in and run pytest:

```bash
cd examples/01-bringup && uv run pytest -v
cd examples/02-station && uv run pytest --station=demo_station_001 --mock-instruments -v
cd examples/03-profiles && uv run pytest --mock-instruments -v
cd examples/03-profiles && uv run pytest --litmus-profile=production --mock-instruments -v
```

## Drivers

Tiers 2 and 3 share a small PyVISA-flavored driver package at
`drivers/` (DMM, PSU, Eload, Scope — all `MagicMock`-backed for
`--mock-instruments`). A sibling `conftest.py` inserts the repo root
into `sys.path` so tests can `from examples.drivers import DMM`. Swap
these for real drivers (PyMeasure, vendor libs) without touching test
code.

## Starter projects

`litmus init --tier=bringup` scaffolds a Tier 1 project. `--tier=bench`
scaffolds the Tier 2 shape. Both match the layouts here.

## Scripts

`scripts/` contains DuckDB query examples for Parquet results
(`demo_duckdb.py`, `query_results.py`, `demo_queries.sql`).
`interactive_station.py` is a NiceGUI monitor that streams live events
and channel data from any running test.
