# Runs data architecture — navigational map

**Purpose:** a single place that says *what the runs data model is* and *where each
fact authoritatively lives*, so a reader (human or agent) reaches for the right source
instead of reconstructing the model from memory. Internal doc — framework internals are
in-scope here.

## Precedence — what to trust when sources disagree

1. **Source code is ground truth for what is BUILT.** `src/litmus/data/schemas.py`
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

## The grain (v2 final — verified against `schemas.py`)

```
run     (record_type='run')    one row: run/UUT/part/station/env context
└─ step   (record_type='step')   code identity + timing + rolled-up outcome; SHEDS inputs
   └─ vector (record_type='vector')  THE UNIVERSAL EXECUTION UNIT — one row per execution
        inputs / outputs / custom : LIST<lane struct>      (nested on the vector)
        measurements              : LIST<measurement struct> (nested on the vector)
```

Load-bearing facts (each contradicts the stale v1 memory):
- **`record_type ∈ {run, step, vector}`** — there is **no** `record_type='measurement'` row
  at rest. Measurements are a **nested `LIST<struct>` on the vector** (`schemas.py` `_MEASUREMENT_LIST`).
- **The step sheds inputs.** It carries code identity + timing + outcome only. `in_*`/`out_*`
  are **not** denormalized onto steps (that was v1). Conditions live on the **vector** lanes.
- **The vector is always present.** A non-looping step has exactly one **scope vector**; a
  self-loop (`vectors` fixture) adds nested **iteration vectors**. Every execution = a vector row.
- **Retry is a VECTOR KEY COORDINATE: `(step_path, vector_index, retry)`.** Each execution
  including a retry is its own vector row. "Every time the step runs we know it" by the **row
  existing**, not by a count column. (Pin is NOT a coordinate — it's a leaf attribute on the
  measurement/lane; 8 pins at one condition = one vector + 8 pin-rows.)

## The three layers — where each lives

| Layer | What | Source of truth |
|---|---|---|
| **Events** | The chronological telling. `in_*`/`out_*` ride on the events. | `data/events.py`; accumulated by `EventAccumulator` (`backends/_event_accumulator.py`). Events are durable; the accumulator is replay-rebuildable. |
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
- *How is a vector / scope vector / retry built?* → `_event_accumulator.py`
  (`_build_vector_results_from_events` = in-body, keyed `(path,vec,retry)`;
  `_build_scope_vector_results_from_events` = synthesized scope vector;
  `_build_step_results_from_events` = step rows).
- *Why is the model shaped this way?* → `runs-execution-model.md`, **v2 FINAL contract** section.
- *Step identity / path / parent?* → `step_path` is the identity (PK is
  `(run_id, step_path, vector_index)`); `step_name` is its leaf; `parent_path` = `step_path`
  minus the `step_name` suffix (derivable; producers `run_scope.py` / `pytest_plugin/hooks.py`).

## Known band-aids / as-built-vs-contract gaps (do NOT mistake for design)

- **`steps_materialized.retry_count`** — a v1 leftover. It's `MAX(vector_retry)` rolled up to
  the step, with **zero consumers**. It exists only because the synthesized scope vector is
  **hardcoded `retry=0`** (`_build_scope_vector_results_from_events`), so scope-level / Mode-1
  reruns fuse into one vector row and lose the per-execution grain. **Proper kill:** synthesize
  the scope vector **per execution** (carry real `retry` in its key) so every run is a row;
  then `retry_count` is redundant by grain. At-rest change → 0.3.0, not the projection pass.
- **`steps_materialized.has_measurements` / `vector_count`** — dead rollups, no consumers
  (`has_measurements` = `measurement_count > 0`; the UI re-estimates its own vector count).
  Projection-only; droppable without a schema bump.
- **`parent_path`** — redundant (derivable from `step_path` − `step_name`) but still read
  in-process (`pytest_plugin/hooks.py` retest eligibility) and at-rest; its drop is a 0.3.0
  at-rest change, not dead-column cleanup.
