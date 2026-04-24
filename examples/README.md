# Litmus Examples

Three standalone example projects. Each directory is a complete
Litmus project — its own `pyproject.toml`, `conftest.py`, `drivers/`,
YAML config, and tests. Open any one in a fresh IDE and it runs
against mocked instruments.

| Tier | Directory | What it shows |
|------|-----------|---------------|
| **1 — Bringup** | `01-bringup/` | One test file, one sidecar, `MagicMock` fixtures in `conftest.py`. No station, product, or fixture YAML. |
| **2 — Station** | `02-station/` | Add `drivers/` + `stations/` + `products/` + `fixtures/` + `catalog/`. Instrument fixtures auto-register from the station; spec-backed limits resolve from the product. |
| **3 — Profiles** | `03-profiles/` | Full production flow: user-maintained catalog, profiles (`production` / `characterization`), multi-pin iteration, binding-aware limits. |

## Running

```bash
cd examples/01-bringup  && uv run pytest -v
cd examples/02-station  && uv run pytest --mock-instruments -v
cd examples/03-profiles && uv run pytest --mock-instruments -v
cd examples/03-profiles && uv run pytest --litmus-profile=production --mock-instruments -v
```

Each tier has its own `README.md` with layout details.

## Starter projects

`litmus init --tier=bringup` scaffolds a Tier 1 project.
`litmus init --tier=bench` scaffolds the Tier 2 shape.

## Utility scripts

`scripts/` — DuckDB query examples for Parquet results (`demo_duckdb.py`,
`query_results.py`, `demo_queries.sql`). `interactive_station.py` — a
NiceGUI monitor that streams live events and channel data from any
running test. Both are cross-tier; run them from the repo root.
