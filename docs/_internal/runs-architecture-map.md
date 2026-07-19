# Runs data architecture — navigational map

**Purpose:** a single place that says *what the runs data model is* and *where each
fact authoritatively lives*, so a reader (human or agent) reaches for the right source
instead of reconstructing the model from memory. Internal doc — framework internals are
in-scope here.

## Precedence — what to trust when sources disagree

1. **Source code is ground truth for what is BUILT.** `src/testerkit/data/schemas.py`
   (`RUN_ROW_SCHEMA`) is the at-rest contract; `_runs_duckdb_daemon.py` is the projection
   contract. Read these first.
2. **Design docs are intent + rationale, and they LAG the code.**
   `docs/_internal/explorations/runs-execution-model.md` is the authoritative *design*
   (use its **"v2 FINAL contract"** section, ~line 87 — earlier sections are superseded
   v1/as-built-v2). It states intent; some "deltas still to do" are already built. When it
   disagrees with `schemas.py`, the code wins for "what is."
3. **Agent memory is a point-in-time hint, not state.** It has been wrong here (it carried
   the v1 `record_type ∈ {run, step, measurement}` model two iterations after the code moved
   on). Verify against source before asserting.

## The grain (grain-reshape final — verified against `schemas.py` + `feat/0.3.0-grain-reshape` P1–P4)

Design contract: `docs/_internal/explorations/step-vector-grain-reshape.md`.
Execution diary: `docs/_internal/explorations/step-vector-grain-reshape-execution.md`.

```
run     (record_type='run')    one row: run/UUT/part/station/env context
└─ step   (record_type='step')   code unit — carries its OWN inputs/outputs/measurements (step-scope)
   └─ vector (record_type='vector')  a CONDITION POINT — one row per sweep/loop iteration
        inputs / outputs / custom : LIST<lane struct>      (nested on the vector)
        measurements              : LIST<measurement struct> (nested on the vector)
```

Load-bearing facts (verified against source):
- **`record_type ∈ {run, step, vector}`** — there is **no** `record_type='measurement'` row
  at rest. Measurements are a **nested `LIST<struct>`** on their carrier (`schemas.py`
  `_MEASUREMENT_LIST`).
- **Steps carry their own data.** A step latches `inputs`/`outputs`/`measurements` on
  `StepEnded` (`_build_step_results_from_events`, `_event_accumulator.py`). Steps do NOT shed
  inputs. The `inputs`/`outputs` lanes on a step row are the step-scope data (conditions from
  sweep params AND any `configure()` call in the step body).
- **A non-looping step has ZERO vectors.** A vector row exists ONLY for an actual sweep/loop
  point: a Mode-1 `@parametrize` variant, a class-outer `testerkit_sweeps` iteration, or a
  Mode-2 in-body `vectors`/`context.vector()` iteration. The synthesized scope vector is
  **deleted** (`_build_scope_vector_results_from_events` and `build_scope_vector_row` are gone).
- **`vector_index` is nullable on step rows.** NULL means the step's parent did not emit any
  loop (top-level step, or a method inside a non-swept class). An int is the enclosing parent's
  loop iteration this step ran under. Null-vs-0 reconstruction: `_parent_emitted_vectors` in
  `_event_accumulator.py` — checks whether the parent's `step_path` appears in
  `_vector_starts`. On vector rows: own leaf position in the loop.
- **Retry is a STEP KEY COORDINATE: `(step_path, step_retry, vector_index_key)`.** The PK
  uses `vector_index_key = COALESCE(vector_index, -1)` (`_runs_duckdb_daemon.py:221`) so a
  step-own row (vi=NULL → -1) and a `vector_index=0` vector row never collide. A non-looping
  step rerun = a distinct step row keyed by `step_retry`. (Pin is NOT a coordinate — it's a
  leaf attribute on the measurement/lane.)
- **Measurements are source-agnostic.** The daemon UNNESTs from `record_type IN ('step',
  'vector')` into one flat fact (`_measurement_unnest_insert`, `_runs_duckdb_daemon.py`):
  vector-source → own `vector_index`; step-source → `NULL` (literal, never `s.vector_index`).
  Consumers never branch on `record_type`. The IS-NOT-DISTINCT-FROM join in `_VECTOR_KEY`
  (`analysis/measurements_query.py:57`) handles the nullable `vector_index`.

## The three layers — where each lives

| Layer | What | Source of truth |
|---|---|---|
| **Events** | The chronological telling. The `inputs`/`outputs` lanes ride on the events. | `data/events.py`; accumulated by `EventAccumulator` (`backends/_event_accumulator.py`). Events are durable; the accumulator is replay-rebuildable. |
| **At-rest parquet** | Nested archive, one file per run. `record_type ∈ {run,step,vector}`, nested lanes + measurements. | `RUN_ROW_SCHEMA` in `data/schemas.py`. Written via `table_from_rows`/`_build_write_schema`. |
| **DuckDB projections** | Unpacked query tables, derived at ingest. | `_runs_duckdb_daemon.py`: `runs_materialized`, `steps_materialized`, `measurements_materialized` (daemon **UNNESTs** the nested `measurements`), `measurements_dynamic` (EAV from the in/out/custom lanes) + the `dynamic_attrs` MAP. Inflight twin: `_accumulator_pool.py` (`INFLIGHT_*_SCHEMA`); the `runs`/`steps`/`measurements` **VIEWs** UNION materialized + inflight overlay `BY NAME`. |
| **Query API** | Read surfaces. | `analysis/runs_query.py`, `steps_query.py`, `measurements_query.py`, `measurement_facets.py`. UI/API/CLI/MCP/Grafana read **through these**, never raw parquet. |

**No-drift rule** (see memory `project_runs_data_flow_no_drift`): all paths derive from the
accumulator; one projection shape feeds inflight + parquet + index; a live run renders
identically to a completed one. Enforced by a guard in `tests/test_conventions.py` and the
inflight↔materialized equivalence test (`tests/test_data/test_overlay_schema_consistency.py`).

## "To answer X, read Y"

- *What columns are at rest?* → `RUN_ROW_SCHEMA` (`schemas.py`). NOT the daemon tables.
- *What columns does a query table have / is a column read?* → `_runs_duckdb_daemon.py`
  (CREATE + `_*_PERSISTED_COLUMNS`) for shape; `analysis/*_query.py` + UI/API for consumers.
- *How are step rows / vector rows built?* → `_event_accumulator.py`
  (`_build_step_results_from_events` = step rows, latches own data, null-vs-0 reconstruction;
  `_build_vector_results_from_events` = ALL vector rows — Mode-1/class-outer/Mode-2 — keyed
  `(step_path, vector_index, retry)` from `VectorStarted`/`VectorEnded` events; note: its
  docstring predates the grain reshape and erroneously says "Mode-2 only" — the code is
  correct, the docstring is stale). Synthesized scope vector (`_build_scope_vector_results_from_events`)
  is **deleted**.
- *Why is the model shaped this way?* → `runs-execution-model.md`, **v2 FINAL contract** section.
- *Step identity / path / parent?* → `step_path` is the identity (PK is
  `(run_id, step_path, vector_index)`); `step_name` is its leaf; `parent_path` = `step_path`
  minus the `step_name` suffix (derivable; producers `run_scope.py` / `pytest_plugin/hooks.py`).

## Known band-aids / as-built-vs-contract gaps (do NOT mistake for design)

The grain reshape (`feat/0.3.0-grain-reshape`) resolved the following items that were listed
as band-aids in earlier versions of this file:

- **`steps_materialized.retry_count`** — **DELETED** (grain reshape). Was a `MAX(vector_retry)`
  rollup over the synthesized scope vector; the scope vector is now gone; every rerun is a
  distinct step row keyed by `step_retry`, so `retry_count` is redundant.
- **`steps_materialized.has_measurements` / `vector_count`** — **DELETED** (grain reshape).
  Were dead projection-only rollups with zero consumers.
- **`parent_path`** — no longer a stored at-rest column. `steps_materialized` does not carry it;
  parent derivation is `step_path.rsplit("/", 1)[0]` at read time where needed (e.g.
  `pytest_plugin/hooks.py` retest eligibility). Not a band-aid — resolved.
