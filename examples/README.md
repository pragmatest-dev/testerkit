# Litmus Examples

Seven standalone example projects, each a diff off the one before.
Read them in order — every stage adds exactly one concept.

| # | Directory | What it adds | Gap it closes |
|---|-----------|--------------|----------------|
| **1** | `01-vanilla/` | Pure pytest + real `psu` / `dmm` driver classes; Litmus mocks them when no bench is attached. | Baseline. Measurements aren't captured anywhere. |
| **2** | `02-verify/` | `verify(name, value, limit=...)` + Parquet log; `litmus_retry` for transient failures. | Measurements get persisted; flake handling on day one. |
| **3** | `03-inline-limits/` | `@pytest.mark.litmus_limits` decorator. | Limit is now declarative, not an imperative `Limit(...)` object. |
| **4** | `04-sidecar-markers/` | Markers move to a sibling `test_*.yaml`; classes for grouping. | Ops can tune limits without editing Python. |
| **5** | `05-station-catalog/` | Station YAML + catalog; conftest disappears; `litmus_mocks` for per-test overrides; `litmus_prompts` for operator-in-the-loop. | Mock declarations in YAML; per-test fault injection; operator gates. |
| **6** | `06-product-spec/` | Product YAML, fixture routing, `litmus_characteristics` + `litmus_connections` + `tolerance_pct`. | Spec is the source of truth; rows carry traceability. |
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

## A pytest primer (if this is your first time)

These examples are pytest projects, with Litmus loaded as a pytest
plugin. A few conventions you'll see in every directory:

- **`tests/`** — pytest auto-discovers any `test_*.py` (or `*_test.py`)
  file by walking down from the project root. The `tests/` folder is
  convention, not requirement; you could put tests anywhere.
- **`conftest.py`** — pytest's hook file for shared fixtures and
  configuration. Anything defined here is available to every test
  in the same directory tree without an import.
- **`pytest.ini`** — pytest's optional config file. Useful for
  pinning the test directory (`testpaths = tests`), passing default
  flags (`addopts = ...`), or naming registered markers. None of
  these examples need one (vanilla has a comment-only file for
  documentation), but real projects often grow into one. The same
  config can also live under `[tool.pytest.ini_options]` in
  `pyproject.toml`.
- **`pyproject.toml`** — Python's standard project file. Lists
  dependencies (`pytest`, `litmus-test`), build settings, and any
  pytest config you don't want in a separate `pytest.ini`.

Pytest's own docs at <https://docs.pytest.org> are the authoritative
reference. The examples here teach Litmus *through* pytest; they
don't re-document pytest itself.

## Utility scripts

`scripts/` — DuckDB query examples for Parquet results
(`demo_duckdb.py`, `query_results.py`, `demo_queries.sql`).
`interactive_station.py` — a NiceGUI monitor that streams live events
and channel data from a running test. Both are stage-agnostic; run
them from the repo root.
