# Runs execution model — vector-grained chronological telling

**Status:** design (2026-06-17), shaped via discussion. Supersedes/absorbs the
measurement-storage EAV spike (`measurement-storage-eav.md`), which is now just
the projection phase of this larger model.

**One-line:** model a run as `run → step → vector → measurement`, where the
**vector** (one condition set / one execution) is the organizing unit; persist a
normalized **chronological telling** of the events; project that into DuckDB.

## Why (the seam we found)

Today outcome, retry, and conditions are all carried on the **measurement**
grain, so anything without a measurement has to be **fabricated** or is **lost**:

- **Fabricated rows** — an assert fail synthesizes `Measurement(name="assert")`
  (harness.py:1546, vestigial from the measurements-only era); an observation-only
  vector synthesizes a `name=NULL` DONE row (accumulator `_build_promoted_rows`,
  commit 52f9375) just to appear on the measurement read-plane.
- **`retry_count` is a `MAX(vector_retry)` rollup over measurements**
  (_runs_duckdb_daemon.py:1023, commit f995cd5) — a step that retries with **no
  measurement** reads as `retry_count=0`. The retry is invisible.
- **Inputs/outputs are vector-owned but denormalized onto every measurement** —
  `inputs`/`outputs` are built from the vector and copied per measurement
  (logger.py:932-935, 964); 3 measurements in a vector = 3 identical copies.
- **Hybrid shape** — measurements are long/EAV (`measurement_name` is a value),
  but `in_*`/`out_*` are wide (name is a column → the #37/#38 column explosion).
- **Vectors have no representation in the streaming path** — `run_vector` emits
  no lifecycle event (harness.py:1474-1565); there is no `VectorStarted`. A
  self-loop emits **one** `StepStarted` (carrying only `vectors[0]`, logger.py:811).
  So a data-less inner vector is **lost** in the daemon path, while the offline
  path keeps it (iterates `step.vectors`, _row_helpers.py:847) → **offline/streaming
  drift**, which the "events are truth, all derived" rule forbids.

## The model

```
run
└─ step              — CODE identity (pytest class/method); has its own span
   └─ vector         — one condition set / one execution; UNIVERSAL (index 0 always);
      │                owns inputs (conditions), outputs (context), outcome, retry
      └─ measurement — the FACT: value + units + limits + outcome + traceability
```

Definitions, each grounded in code:

- **Vector is universal** — every step run has ≥1 vector; unswept = index 0 with
  empty inputs (`TestVector.index=0`/`params={}`, models.py:289-290; auto-create
  logger.py:874-878).
- **Vector has an outcome** — `TestVector.outcome` (models.py:295),
  `StepEnded.vector_outcome` (events.py:568). It is a full entity (inputs, outputs,
  outcome, measurements, timing).
- **Vector identity = its composed condition set** — assembled down the hierarchy
  (class param + method param + `expand_vectors` + internal loop). A single integer
  index can't represent composition; the hashable condition set can. This entity
  **is** the projection's condition dimension (units ride here too).
- **Measurement = fact, conditions = dimensions** — yield/Cpk/pareto/parametric all
  anchor on measurements; inputs/outputs are only ever filter/group-by/x-axis.
- **No fabrication** — assert fail = vector `outcome=FAIL`, empty measurements;
  observation-only = vector with outputs, empty measurements; measurement-less
  retry = a real vector execution. The `"assert"`/`NULL`-DONE rows go away.

## Two launch modes (known at collection — not inferred)

The plugin branches on `"vectors" in metafunc.fixturenames` (hooks.py:1684; the
`vectors` fixture returns a `_VectorIterator`, __init__.py:1171):

| Mode | Trigger | Shape | Emission |
|---|---|---|---|
| **1 — single** | parametrize / sweep / unswept (no `vectors` fixture) | `step ≡ vector` (1:1, one timestamp) | one **fused `StepVector`** boundary |
| **2 — loop** | `vectors` fixture requested | `step ⊇ vectors` (1:N) | step boundary (outer span + post-loop outputs) + `VectorStarted/Ended` per yield |

This resolves point 7 deterministically: parametrize is genuinely 1:1, the
vectors-fixture loop is genuinely 1:N, and the framework **knows which** before the
body runs — so emission is honest, not papered-over. The step's own span matters in
Mode 2 because the body can do work before/between/after the loop that belongs to no
vector (so we don't collapse step→vector).

## Decisions

**A — RESOLVED (2026-06-18): recursive step-execution tree.** The model is NOT a
single step→vector level. The unit is the **step-execution** `(step_path,
vector_index, retry)`, recursive via `parent_path`. Every level carries its own
`vector_index` (its iteration at that level) and `inputs` (that level's conditions):

- **class container (outer sweep)** → a step-execution; `_ensure_class_container`
  (hooks.py:1267) already emits it via `start_step(cls, inputs=outer_values,
  vector_index=outer-iter)`, close+reopened per outer value. Existing
  `StepStarted`/`StepEnded` (carry `parent_path` + `vector_index` + `inputs`).
- **parametrize item / single (Mode 1)** → a leaf step-execution; existing
  `StepStarted`/`StepEnded` — this IS the fused StepVector.
- **in-body loop (Mode 2)** → one leaf `StepStarted`, then **N `VectorStarted`/
  `VectorEnded`** (the new events) inside it — the in-body analog of a parametrize
  item's StepStarted. This is the only place the new events are needed; it closes
  the data-less-vector gap.
- A measurement's full condition = `inputs` merged along its `parent_path` chain
  (container outer ∪ leaf inner ∪ in-body row). `retry` rides on whichever boundary
  re-executes (`StepStarted` for rerun/Mode-1, `VectorStarted` for the in-body loop).

So `class → vector → step → vector` = `StepStarted(container, vector_index=outer) ⊃
StepStarted(method, vector_index=inner)` — already modeled by the existing nesting;
the new events are scoped to the Mode-2 in-body loop only.

**Agreed:**
- Vector is the organizing unit; `run → step → vector → measurement`.
- Measurements stay first-class; inputs/outputs are vector-owned EAV context (long, not wide).
- Vector identity = composed condition set (hashable) = the condition dimension.
- `retry` = a vector re-execution; `retry_count` = COUNT of vector executions, not a measurement rollup.
- At-rest = normalized chronological telling (materialized from events), NOT raw events.
- Mode 1 fused `StepVector` / Mode 2 nested, keyed on `fixturenames`.
- New `VectorStarted`/`VectorEnded` events (Mode 2); harness execution/vector/retry
  logic is **unchanged** — it already computes these correctly; we only *emit* them.

**Open:**
- **A — outer (container) vs inner (step) vectors. LOAD-BEARING — resolve before
  Phase 1/2.** NOT hypothetical: `_resolve_sweep_dimensions` (hooks.py:550) already
  splits sweep dims into **outer** (class-level `litmus_sweeps` → "the sequence
  iterations a class container splits into") and **inner** (method-level sweeps /
  `parametrize` / `vectors` fixture). So `class → vector → step → vector` is a real
  nested structure today: a class container has its own vector dimension, each
  containing the method's vectors. (Caveat verified: this outer/inner split is for
  `litmus_sweeps`; a plain class-level `@pytest.mark.parametrize` currently falls
  into *inner* and merges via `callspec.params`, autouse.py:154.) The model must
  decide how container/outer vectors are represented — a step hierarchy where
  intermediate (container) steps carry their own vectors — and `VectorStarted` must
  be able to attach at the container level, not just the leaf. The flat
  `run → step → vector → measurement` statement above is the single-level case; the
  nested case is this decision.
- **B — container (class) row:** its own record vs. `parent_path`-only (derive in projection).
- **Condition dimension placement:** modeled at-rest (vector-as-referenced-entity)
  vs. projection-only dedup. (Leaning: referenced entity.)
- **Units:** ride on the condition dimension; plumbing deferred (slot reserved).

## Phased plan

1. **Events** — add `VectorStarted`/`VectorEnded`; Mode-1 fused `StepVector`
   emission; step lifecycle carries `(vector_index, retry)`. No execution/vector/retry
   logic change (harness already correct) — emission only.
2. **Parquet** — materialize the new chronological-telling format (step → vector →
   measurement, fused `StepVector` for Mode 1); both materialization paths converge
   (closes the offline/streaming drift). No fabricated rows.
3. **DuckDB projection** — `UNNEST` into a measurement **fact** + vector/condition
   **dimension**; steps and `retry_count` become derivations. (This is the absorbed
   EAV spike — long table beats MAP, benched; index only `name`, no high-cardinality
   ART index — see `bench_index_scale.py`.)
4. **Queries** — repoint to the projections; keep the public Query API stable if
   possible (goal: no API-shape change for `RunsQuery`/`StepsQuery`/`MeasurementsQuery`).
5. **UI / MCP** — only if a data-pull API shape changes; otherwise untouched.
6. **Docs** — update all affected docs to the new model: the data-model / schema
   reference, runs/steps/measurements concepts, the vector + retry vocabulary, and
   any page describing `in_*`/`out_*` columns or the old grain. Regenerate the
   marker-gated reference pages (`scripts/generate_reference_docs.py --all`).

## Blast radius

End-to-end across the runs pipeline. Measured touch surface:

- **Irreducible core (~13 files):** emission (`events.py`, `harness.py`, `logger.py`,
  `pytest_plugin/`), at-rest (`schemas.py`, `_row_helpers.py`, `_event_accumulator.py`,
  `parquet.py`), projection (`_runs_duckdb_daemon.py`, `_accumulator_pool.py`),
  query SQL (`measurements_query.py`, `steps_query.py`, `run_store.py`).
- **Containable ripple (~20 files) — gated by the Query API firewall:** `api/`,
  `cli/`, `mcp/tools.py`, 6 UI pages, **6 Grafana dashboard JSONs (raw SQL)**,
  `queries.py`. Churns ONLY if the public `RunsQuery`/`StepsQuery`/`MeasurementsQuery`
  output shape changes. Holding that shape stable (phase 4) collapses this to ~0.
- **Tests:** ~23 files.
- **Unchanged:** harness execution / vector expansion / retry calculation — emission only.
- **Risk concentrations:** `_runs_duckdb_daemon.py` (tuned machine: inflight overlay,
  lock-free parallel reads, dual materialization paths that currently drift); Grafana
  raw SQL; data-breaking cutover (wipe, no backcompat — fine pre-0.2.0).

## Success criteria

1. **Performance ≥ today — HARD GATE, benchmarked.** Every viewer query pattern
   (yield, pareto, Cpk, parametric, facet/distinct, steps list, measurements list)
   and write/ingest throughput must be **at least as fast as the current
   measurement-grain implementation**, on equivalent data. The EAV optimizations
   (long table beats MAP 3–18×; index only `name`; scales to 160M rows spilling —
   `bench_index_scale.py`) should make queries *faster*, not just neutral. Proven
   with a before/after bench on the same dataset, not asserted. A regression on any
   pattern blocks the change.
2. **No projection drift** — offline (`ParquetBackend`) and streaming (accumulator)
   materialize identical records. Closes the current vector-visibility drift.
3. **No fabricated rows** — the `"assert"` row, the `NULL`-named DONE row, and the
   `MAX(vector_retry)` rollup are gone; asserts/observations/retries represent honestly.
4. **No data loss** — measurement-less retries and data-less vectors are captured.
5. **Result parity** — existing analytics (yield/Cpk/pareto/parametric) return
   equivalent results on equivalent runs (modulo the now-correct retry/observation handling).
6. **Lossless + typed** (from the EAV spike) — `int` preserved, no cross-run VARCHAR flip.
7. **Query API shape held stable** where possible; any change enumerated explicitly
   (it's the blast-radius firewall).

## Evidence anchors (verified, so we don't re-litigate)

- vector universal / outcome: models.py:289-295; logger.py:874-878; events.py:568
- step events have no retry, fire once per step: events.py:452/546; harness.py:1651/1666; logger.py:648/811
- `run_vector` emits no lifecycle event: harness.py:1474-1565
- offline vs streaming vector capture (drift): _row_helpers.py:847 vs `_build_step_results_from_events`
- mode flag: hooks.py:1684; `vectors` fixture __init__.py:1171
- inputs/outputs vector-owned, copied per measurement: logger.py:932-935, 964
- fabrications: harness.py:1546 (`"assert"`), accumulator `_build_promoted_rows` (commit 52f9375)
- `retry_count` rollup: _runs_duckdb_daemon.py:1023 (commit f995cd5)
- one-step-row-per-vector grain rationale (counting): commit a6df009

## Prior art

- **STDF** — PTR (parametric, value+limits) vs **FTR** (functional, pass/fail, no
  value); TSR (synopsis rollup). Asserts are FTRs, not fake PTRs.
- **OpenHTF** — phase owns measurements; `PhaseOutcome` covers measurement-fail,
  explicit fail, AND exception; `REPEAT` is a phase-level retry.
- **pytest-rerunfailures** — each attempt is its own report (attempt-as-record).
- **TestStand** — step has a first-class Status; steps own measurements.
