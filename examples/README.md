# TesterKit Examples

Twelve standalone example projects. Stages 1–7 are a strict diff
chain — read them in order, every stage adds exactly one concept on
top of the last. Stages 8–12 are standalone topical examples that
assume the stage 1–7 concepts and each go deep on one further area
(evidence, streaming, artifacts, querying, multi-site); they aren't a
diff chain off each other, so read them in whatever order matches
what you need.

## The core progression (1–7)

| # | Directory | What it adds | Gap it closes |
|---|-----------|--------------|----------------|
| **1** | `01-vanilla/` | Pure pytest + real `psu` / `dmm` driver classes; TesterKit mocks them when no bench is attached. | Baseline. Measurements aren't captured anywhere. |
| **2** | `02-verify/` | `verify(name, value, limit=...)` + Parquet log; `testerkit_retry` for transient failures. | Measurements get persisted; flake handling on day one. |
| **3** | `03-inline-limits/` | `@pytest.mark.testerkit_limits` decorator. | Limit is now declarative on the test function, not inline in the body. |
| **4** | `04-sidecar-markers/` | Markers move to a sibling `test_*.yaml`; classes for grouping. | Ops can tune limits without editing Python. |
| **5** | `05-part-spec/` | Part YAML drives spec-aware limits (`characteristic` + `tolerance_pct`); still on the conftest bench. | Limit values live once in the datasheet, not duplicated per test. |
| **6** | `06-station-catalog/` | Station YAML + catalog + fixture connections; conftest disappears; `ctx.connections` iteration; `testerkit_mocks` for per-test overrides; `testerkit_prompts` for operator-in-the-loop. | Bench is config-driven; tests iterate connections; mock + prompt gates land. |
| **7** | `07-profiles/` | Profiles under `profiles/*.yaml` with `extends:` chains; bind `station_type` + `fixture` per phase. | Scenarios (dev / production / characterization) load the right limits AND the right wiring without per-test forking. |

## Deeper topics (8–12)

| # | Directory | What it adds | Gap it closes |
|---|-----------|--------------|----------------|
| **8** | `08-waveform-evidence/` | `observe(name, waveform)` before `verify()` routes a captured `Waveform` to ChannelStore and stamps its `channel://` URI onto every measurement row in the vector. | A pass/fail scalar row links straight to the raw waveform evidence behind it — one click from "what" to "why". |
| **9** | `09-instrument-streaming/` | No pytest — a standalone script streams live DMM samples via `channels.stream` into ChannelStore session files; the operator UI panel renders it push-style. | The same streaming primitive test code uses, exercised from the interactive case (notebook / REPL / bench debug), not a test run. |
| **10** | `10-artifacts-and-byte-streams/` | FileStore artifacts — `PIL.Image`, raw `bytes`, a Pydantic report, and a JSONL byte stream via `files.stream(name, format="jsonl")` — each landing a `file://` URI on the verify row. | Non-tabular evidence (photos, vendor blobs, reports, event logs) gets the same one-click navigation from a verify row as ChannelStore data does. |
| **11** | `11-querying-data/` | Consumer-side: `TesterKitClient` seeds runs, then `RunsQuery` / `MeasurementsQuery` / `EventStore` read them back programmatically — no operator UI involved. | Shows the public Query API, the other half of "where did my data go" for analysts, ETL, MCP tools, and external dashboards. |
| **12** | `12-parallel-sites/` | A 2-site fixture (`is_multi_site`) turns a bare `pytest` invocation into an orchestrator process plus one worker subprocess per site, each running the full session against its own site. | The same test code scales from one UUT to N tested in parallel — no test code changes, just fixture YAML. |

## Running

```bash
cd examples/01-vanilla && uv run pytest -v
cd examples/02-verify && uv run pytest -v
# ...
cd examples/07-profiles && uv run pytest --test-phase=production -v

cd examples/08-waveform-evidence && uv run pytest -v
# 09-instrument-streaming has no tests/ — see its README, it's a script run from two terminals
cd examples/10-artifacts-and-byte-streams && uv run pytest -v
# 11-querying-data has no tests/ either — see its README, it's `seed_runs.py` then `analyze.py`
cd examples/12-parallel-sites && uv run pytest -q
```

Stages 1–7 each work standalone and have their own `README.md` with
the diff from the previous stage and the gap it leaves for the next
one. Stages 8–12 each have their own `README.md` too, but describe a
standalone topic rather than a diff.

## A pytest primer (if this is your first time)

These examples are pytest projects, with TesterKit loaded as a pytest
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
  dependencies (`pytest`, `testerkit`), build settings, and any
  pytest config you don't want in a separate `pytest.ini`.

Pytest's own docs at <https://docs.pytest.org> are the authoritative
reference. The examples here teach TesterKit *through* pytest; they
don't re-document pytest itself.

## Utility scripts

These live at the `examples/` root (not inside a numbered stage) and
are stage-agnostic — run them from the `examples/` directory:

- **`scripts/`** — DuckDB query examples for Parquet results
  (`demo_duckdb.py`, `query_results.py`, `demo_queries.sql`), plus
  `seed_artifact_demo.py`, which seeds one run carrying every
  viewable artifact type (waveform, image, JSON report, ...) so you
  can click through the operator UI's artifact viewers without
  running a full example stage first.
- **`interactive_station.py`** (+ `static/station.css`) — a NiceGUI
  monitor that streams live channel data and session events from a
  running test, in any process. Run it, then run `pytest` in another
  terminal against any example to see it update live.
