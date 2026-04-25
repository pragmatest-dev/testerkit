# Litmus Examples

Seven standalone example projects, each a diff off the one before.
Read them in order — every stage adds exactly one concept.

| # | Directory | What it adds | Gap it closes |
|---|-----------|--------------|----------------|
| **1** | `01-vanilla/` | Pure pytest — assertions, fixtures, parametrize. | Baseline. Measurements aren't captured anywhere. |
| **2** | `02-verify/` | `verify(name, value, limit=...)` + Parquet log. | Measurements get persisted, pass/fail stays. |
| **3** | `03-inline-limits/` | `@pytest.mark.litmus_limits` decorator. | Limit is now declarative, not an imperative `Limit(...)` object. |
| **4** | `04-sidecar-markers/` | Markers move to a sibling `test_*.yaml`; classes for grouping. | Ops can tune limits without editing Python. |
| **5** | `05-station-catalog/` | Station YAML + instrument catalog; `psu` / `dmm` fixtures auto-register. | No hand-rolled `conftest` fixtures. Mocks via flag. |
| **6** | `06-product-binding/` | Product YAML, fixture routing, `litmus_binding` + `tolerance_pct`. | Spec is the source of truth; rows carry traceability. |
| **7** | `07-profiles/` | Profiles under `profiles/*.yaml` with `extends:` chains. | Scenarios (dev / production / characterization) without per-test forking. |

## Running

```bash
cd examples/01-vanilla && uv run pytest -v
cd examples/02-verify && uv run pytest -v
# ...
cd examples/07-profiles && uv run pytest --test-phase=production -v
```

Every stage works standalone. Each has its own `README.md` with the
diff from the previous stage and the gap it leaves for the next one.

## Utility scripts

`scripts/` — DuckDB query examples for Parquet results
(`demo_duckdb.py`, `query_results.py`, `demo_queries.sql`).
`interactive_station.py` — a NiceGUI monitor that streams live events
and channel data from a running test. Both are stage-agnostic; run
them from the repo root.
