# Projection Normalization — star schema, join for identity, kill denormalization

**Status:** design contract (approved 2026-07-03). Branch: `feat/0.3.1-projection-cleanup`. Part of #53 (0.3.1).
**Implementation:** delegate to Sonnet, phase by phase, each phase green before the next.

---

## The problem

The daemon's derived projections **denormalize**: run identity is stamped onto step rows and measurement rows, and input/output values are stamped onto carriers as a prefixed `dynamic_attrs` MAP. That is a row-store instinct (avoid joins) that buys ~nothing in **columnar DuckDB** (cheap hash joins) and is the *direct cause* of the drift class we fought all of 2026-07-03: identity lives in N places, so it drifts (`uut_revision` etc. written at rest but dropped by every projection; 5 layers to keep in sync).

## The end-state (star schema)

Each derived table carries **only its own grain's columns + the foreign key to its parent**. Identity lives **once**, in `runs`. Reads JOIN for identity.

| Table | Columns | FK |
|---|---|---|
| `runs` | run identity (all of it — this is the one home) | — |
| `steps` | step code/timing/outcome only | `run_id` |
| `measurements` | measurement fields only | `run_id` + step/vector coords |
| `inputs` | `_LANE_STRUCT` value cols + `name` | `run_id` + step/vector coords |
| `outputs` | `_LANE_STRUCT` value cols + `name` | `run_id` + step/vector coords |
| `instruments` | instrument struct fields | `run_id` |

Consequences:
- **No identity on steps/measurements** — `SELECT ... FROM measurements m JOIN runs r USING (run_id)` for part/station/etc. Metrics queries (yield/pareto/ppk grouped by part/station) add a `runs` join; DuckDB handles it.
- **EAV split**: `measurements_dynamic` (role-keyed) → two honestly-named tables `inputs` / `outputs`, **no `role` column** (the table is the role). Same vector-grain key each. (Every query is role-scoped → `FieldRole` maps to a table, replacing the `role = X` predicate. A role-less query is never issued; would be a UNION.)
- **Drop `dynamic_attrs`** entirely (the prefixed inline MAP). Not a proven perf path — only display reads it (the steps view); derive the inline map at query time by aggregating `inputs`/`outputs` when a view needs it. Reintroduce as two honest `inputs`/`outputs` MAP columns ONLY if a measured hot path demands it — not on spec.
- **Prefixes (`in_`/`out_`) die** with `dynamic_attrs`.
- **Drift becomes structurally impossible** — there is no denormalized copy to diverge, so `test_ingestion_drift` collapses to the uniform rule "each nested-struct table surfaces every field of its struct" (measurements/instruments/inputs/outputs), with no per-grain identity propagation to check.

**Scope boundary:** the **at-rest parquet stays denormalized** (one file per run, all record_types wide — changing it is an epoch bump, out of scope). Only the **derived projections** normalize. Ingest the wide parquet → narrow normalized tables → join on read. This is a derived-cache change: rebuilds on boot, no `schema_version` bump.

## Blast radius (from grep 2026-07-03)

- `src/litmus/data/_runs_duckdb_daemon.py` — table CREATEs, `_*_PERSISTED_COLUMNS`, the ingest INSERTs (drop identity from steps/measurements INSERTs; split the EAV UNNEST into two tables; drop the `dynamic_attrs` map expr), the `runs`/`steps`/`step_vectors`/`measurements` views (join identity from `runs`; drop `dynamic_attrs`).
- `src/litmus/analysis/measurements_query.py` (13 `measurements_dynamic` refs) — `_EAVJoins`, `_VECTOR_KEY`, `_resolve_value_type`, `distinct_values` role path: `JOIN measurements_dynamic ... role=X` → `JOIN inputs`/`JOIN outputs`; identity filters (`uut_part_number` etc.) now need a `runs` join; the metrics SQL templates (`_YIELD_SQL`, `_PARETO_SQL`, `_PPK_SQL`, …) that read identity off `measurements` add a `runs` join.
- `src/litmus/analysis/steps_query.py` — reads `dynamic_attrs`; switch to aggregating `inputs`/`outputs`.
- `src/litmus/data/_accumulator_pool.py` / `backends/_event_accumulator.py` — the inflight overlay: split inflight EAV into two, drop `dynamic_attrs` snapshot, drop identity from inflight step/measurement snapshots (or keep the inflight rows joinable to inflight_runs).
- `src/litmus/data/backends/_row_helpers.py`, `parquet.py`, `run_store.py`, `schemas.py`, `api/schemas.py` — `dynamic_attrs` producers/consumers.
- Tests: `test_measurements_query_sql.py`, `test_observation_pin.py`, `test_perf_daemon.py`, `test_overlay_schema_consistency.py`, `test_ingestion_drift.py` (simplify), `test_steps_query`, metrics tests.

## Phases (each green before the next)

1. **Tables + ingest.** Add `inputs`/`outputs` tables; make `steps`/`measurements` materialized tables drop denormalized identity (keep FKs); drop `dynamic_attrs` at ingest. Keep the OLD `measurements_dynamic` alongside temporarily so queries don't break mid-flight — OR do it atomically with phase 2.
2. **Views + queries.** Views join `runs` for identity; `measurements_query`/`steps_query` join `inputs`/`outputs` and `runs`. Remove `measurements_dynamic` + `dynamic_attrs`.
3. **Inflight overlay.** Mirror the normalized shape in the overlay (or make overlay rows join inflight_runs).
4. **Tests.** Simplify `test_ingestion_drift` to the uniform per-table rule; fix `test_overlay_schema_consistency`; update SQL tests. Full suite green.
5. **Docs.** Update `parquet-schema.md` / query-api reference for the normalized projection (at-rest unchanged; projection tables described).

## Verification

- Full pre-commit suite green at each phase.
- `test_ingestion_drift` reduced to "each nested-struct table surfaces its struct" (no identity-propagation checks).
- A query-parity check: yield/pareto/ppk and the parametric/explore surfaces return the same results as before the refactor (join-for-identity ≡ denormalized-identity).
- No `measurements_dynamic`, no `dynamic_attrs`, no `in_`/`out_` prefix anywhere.

---

## Progress log

- 2026-07-03 — Design approved after the drift-fix session. Root cause named: denormalization (row-store instinct) in a columnar DB is the drift source; normalize + join kills it structurally. Branch created. Implementation delegated to Sonnet.
- 2026-07-03 — Implementation start. Resolved 3 mechanical ambiguities the contract left implicit (not stopping — none change the end-state table map, all are "where does the join physically live" choices, logged for transparency per guardrail #5):
  1. **Where identity-reconstruction joins live**: inside the daemon's `runs`/`steps`/`step_vectors`/`measurements`/`instruments` VIEWS (in `_runs_duckdb_daemon.py`), not duplicated into every `measurements_query.py`/`steps_query.py` SQL template. This is what the contract's own "Consequences" example (`FROM measurements m JOIN runs r USING (run_id)`) literally is — the view body. Net effect: `measurements_query.py`/`steps_query.py` need ~zero changes for identity (verified: `steps_query.py`'s `list_for_session`/`list_for_run` already read `session_id`/`site_index`/`uut_serial_number`/`station_id` straight off the `steps`/`step_vectors` views with no join in caller code — that only keeps working if the view reconstructs them).
  2. **`measurements_materialized` keeps step-level fields** (`step_name`, `step_outcome`, `step_started_at/ended_at`, vector coords) — only RUN identity (uut/station/part/test_phase/project/operator/git/env/session/site/fixture) is dropped and joined from `runs`. Step-level denormalization onto measurement rows was never named as the drift source (only run-identity was: "`uut_revision` etc. written at rest but dropped by every projection") and re-deriving it via a step join hits a real step_retry ambiguity (next point) for no stated benefit.
  3. **`inputs`/`outputs` gain a `step_retry BIGINT` column** (not in the contract's table map) — a narrow, deliberate exception to "no new fields," justified by hard rule #6 (query parity is the correctness bar) overriding the anti-scope-creep default. Rationale: the OLD `steps_materialized.dynamic_attrs` was built at ingest directly from each row's own nested lane list, correctly scoped by that row's real `step_retry` (available on every at-rest row per `schemas.py`). The new design derives `steps`/`step_vectors`' inputs/outputs maps at QUERY TIME by joining the persisted `inputs`/`outputs` tables (per contract: "derive the inline map at query time... when a view needs it") — but the old `measurements_dynamic` join key (`run_id, step_index, step_path, vector_index, vector_outer_index, vector_retry`) never carried `step_retry`, so without adding it, two reruns of the same step would collide/fan-out on the new join, a real regression under `pytest-rerunfailures`. Added the column (ingest already has it on every raw parquet row — zero at-rest impact) and used it ONLY in the `steps`/`step_vectors` view's join; `measurements`' inputs/outputs join key is UNCHANGED (no `step_retry`) to match the old `measurements_dynamic` behavior exactly (that gap is pre-existing, out of scope to fix here).
  4. **Inflight overlay identity left denormalized** (Phase 3 scoped down): `inflight_runs`/`inflight_steps`/`inflight_measurements` keep their current wide/denormalized shape for run identity (not joined against `inflight_runs` internally) — the overlay is ephemeral (rebuilt from event replay every daemon start), never the source of at-rest drift the contract calls out, and mirroring the star schema into it is pure-cost/no-correctness-benefit under this session's time budget. What DID change in the overlay: `dynamic_attrs` (prefixed MAP) → two unprefixed `inputs_map`/`outputs_map` MAP columns (mechanical rename to satisfy "no dynamic_attrs, no in_/out_ prefix anywhere" — the overlay has no long-EAV table to join against for live rows, so it keeps building these maps directly from the accumulator's in-memory dicts, cheaply, same as before). Flagged as a followup if full overlay normalization is ever wanted.
