# Runs execution model — vector-grained chronological telling

**Status:** design (2026-06-17), shaped via discussion. Supersedes/absorbs the
measurement-storage EAV spike (`measurement-storage-eav.md`), which is now just
the projection phase of this larger model.

**One-line:** model a run as `run → step → vector → measurement`, where the
**step** is the code unit and default data carrier; **vectors** are condition points
(one row per actual sweep/loop iteration); persist a normalized **chronological
telling** of the events; project that into DuckDB. (See grain-reshape correction
below — v2 decisions 1 and 4 are reversed.)

**Progress (2026-06-18):** Phases 1-4 landed on `spike/runs-execution-model`,
full suite green. P1 events `51cc244`; P2-4 (format + projection) `8f163b6`.
Phase 5 verified no-op (no consumer reads a removed column). Phase 6 docs in
progress. **Perf gate:** query SQL is UNCHANGED (the daemon rebuilds the
`dynamic_attrs` MAP from the lanes), so query latency = today by code identity —
the "≥ today" gate is met. The `measurements_dynamic` EAV long table is built and
ingested but **queries are not yet repointed onto it**, so the EAV *speedup* (the
"better") is wired-but-unrealized — repointing `_col_expr` to the long table is the
remaining follow-up. Known gaps flagged: Mode-1 rerun retry_count not carried in
the main parquet (only the `_steps` manifest); the direct-API `save_test_run` path
can't emit Mode-2 `vector` rows (no Mode flag on `TestRun` — event/daemon path only).

## v2 model — uniform vectors + vector-as-carrier (branch `spike/runs-execution-model-v2`)

> **GRAIN RESHAPE (2026-06-30, `feat/0.3.0-grain-reshape`):** Decisions 1 and 4 below are
> **REVERSED**. The synthesized scope vector is **deleted**. Step is the default data carrier;
> a non-looping step has ZERO vectors. Open A is **RESOLVED** (no new field — `step.vector_index`
> re-meant to the enclosing iteration). See `step-vector-grain-reshape.md` (contract) and
> `step-vector-grain-reshape-execution.md` (diary). The current model is in
> `docs/_internal/runs-architecture-map.md`.

The discussion converged past v1 (the committed fused/Decision-A model) onto a
cleaner v2. **This branch builds v2.** The rule and the seven decisions:

**Grain rule — outcome is the discriminator:** outcome-bearing entities are ROWS
(`run → step → vector → measurement`, the rollup hierarchy); outcomeless captured
data are NESTED lanes (`inputs`/`outputs`/`custom`).

1. **~~Uniform vectors.~~** ~~Every execution materializes a `vector` row — no Mode-1~~
   ~~fusing. A non-looping step has exactly one (its scope vector); a self-loop adds~~
   ~~more. The vector is the universal execution unit, always present.~~
   **REVERSED (grain reshape):** A non-looping step has ZERO vectors. The step is
   the default carrier. Vectors exist only for actual sweep/loop points.
2. **Vector = the canonical data carrier.** `inputs`/`outputs`/`custom` (nested
   lanes) live on the **vector**; measurements are **children of the vector**. The
   `step` carries ONLY code identity + timing + rolled-up outcome — it **sheds
   `inputs`** (StepStarted/StepEnded drop the inputs field; conditions move to
   VectorStarted).
3. **Measurements stay rows, reference (don't copy) the vector.** A measurement row
   carries only its intrinsic fields (value/units/limits/outcome/char/spec/signal-path)
   and references its vector by key — NO in/out denormalization (kills the pivot).
   Measurements are a **typed fact table, not an EAV** (numeric value, fixed schema);
   only `inputs`/`outputs`/`custom` get the EAV.
4. **~~Step-scope vector.~~** ~~Data recorded in the step body but outside any inner loop~~
   ~~(setup/teardown/between) homes on the step's default scope vector; inner-loop~~
   ~~`context.vector()` iterations are nested under the scope vector.~~
   **REVERSED (grain reshape):** The synthesized scope vector is **deleted**. Step-scope
   data (measurements / observations outside any loop) rides on the step row's own
   `measurements`/`inputs`/`outputs` fields. The accumulator assigns step-scope measurements
   via `_partition_measurements` — if `(step_path, vector_index)` is not in `_looped_keys()`,
   the measurement goes to the step row, not a vector row.
5. **Lineage — enclosing-vector key.** A vectorized class nests: class vector ⊃
   method step ⊃ method vector ⊃ measurement. Each vector/measurement carries its
   **enclosing-vector key** (not just `parent_path`), so the iteration is bound
   exactly (not matched on value). The projection pre-assembles the **merged
   condition** onto the leaf (direct condition queries, no per-row walk) AND keeps
   the parent-vector link (structural up/down traversal).
6. **Units — symmetric, optional, inline+config.** `configure("vin", 3.3, unit="V")`
   and `observe("temp", 24.8, unit="°C")` — one optional `unit=` on both; inline is
   primary, config (sweep `vectors:` for inputs, spec/characteristic for outputs)
   supplies a default. Carries value+unit → the lane's `unit` field (already
   reserved) → the EAV `unit` column (already present). No at-rest schema change for
   the slot itself.
7. **Projection unchanged in shape.** The daemon still produces `measurements_dynamic`
   (in/out/custom EAV) + a measurements fact + steps; queries (incl. the committed
   EAV repoint) read the same projection. v2 changes how it's fed (uniform vectors;
   measurements reference; merged-condition assembled up the vector stack), not the
   projection's output shape — public Query API stays byte-stable.

v1 (the prose below) is retained for the rationale; where v1 says "Mode-1 fuses /
measurements denormalize in/out / step carries inputs," **v2 supersedes it** per the
above.

**Resolved build decisions (2026-06-19):**
- **Scope-vector production = (A) synthesize in the materializer.** The materializer
  derives a scope `vector` per step and moves the conditions onto it, so the AT-REST
  parquet is uniform (step rows shed inputs → onto the synthesized scope vector).
  `StepStarted`/`StepEnded` keep `inputs` on the *wire* as the source the materializer
  reads — NO emission rewrite. The durable/queried layer is uniform; the telling is a
  normalized projection of the (unchanged) events. Promoting to real per-execution
  emission (B) is a clean later seam, not built now.
- **Lineage = reuse the existing vector key + merged condition; NO new at-rest column.**
  The merged condition on the leaf (via context inheritance) serves group/filter-by-value;
  `(step_path, vector_index, retry)` + `parent_path` carry the structure. An explicit
  `enclosing_vector_key` is deferred (only needed to disambiguate same-value class
  iterations — rare, and arguably the same condition).
- **Units (decision 6):** proceed as specified.

## v2 FINAL contract (post-discussion, 2026-06-19) — supersedes the above where they differ

The design discussion converged past the as-built v2. **This is the authoritative
contract.** Two layers, deliberately different shapes (the industry pattern —
STDF/OpenHTF/TestStand all capture nested and query from an unpacked DB):

- **At-rest = nested parquet** (the portable archive / source of truth).
- **Query = unpacked typed tables** (the DuckDB projection: a flat measurement
  **fact** + the in/out/custom **EAV**, with the outcome rollup).

**Grain (at-rest):** *(grain-reshape correction applied — see note above; see
`runs-architecture-map.md` for the current authoritative model)*
```
run    (row)
└─ step   (row)   code unit — carries its OWN inputs/outputs/measurements (step-scope)
   └─ vector (row, ONLY for actual sweep/loop points — ZERO vectors for a non-looping step)
      │   inputs / outputs / custom : LIST<STRUCT<name, kind, value lanes, unit, uut_pin>>
      └─ measurements : LIST<STRUCT< name, value, units, limits, outcome,
                                      characteristic, spec, signal-path (uut_pin/…) >>
                                                    ← NESTED under their carrier (step or vector)
```

**Deltas from the as-built v2 (what still needs doing):**
1. **Nest measurements UNDER the vector** (reverses as-built v2's separate
   `record_type='measurement'` rows referencing the vector). Kills the empty-lane
   sparsity; makes the per-pin array natural. The flat measurement **fact lives in
   the projection** (daemon `UNNEST`s the nested measurements at ingest). The
   outcome rollup is a **query-layer** aggregation, not a reason for at-rest rows.
   *(Supersedes decision 3 "measurements stay rows".)*
3. **`unit=` on `verify`** (measurements) — symmetric with `configure`/`observe`
   (already shipped). The measurement carries its unit.
4. **Pin / signal-path on observations** — extend `_auto_traceability` (logger.py:125)
   to the `observe` path so a pinned raw capture inside a `connections` loop gets
   `uut_pin`/`fixture_connection`/`instrument_channel` stamped automatically (the
   active-connection contextvar is already set). Add `uut_pin` to the lane struct.
   Makes observations symmetric with measurements (both pin-able).
5. **`uut_pin` grain-bearing for measurements** — per-pin MPR: same `measurement_name`,
   shared limits, one record per pin, distinguished by `uut_pin` (NOT a name `[x]`
   suffix, NOT a vector input). Pin comes from config via the connections iterator.
6. **Projection: `UNNEST` nested measurements → the fact table** (+ in/out → EAV as
   today). Mechanical; **query output shape stays byte-stable** — the committed
   EAV-query repoint and all consumers (UI/CLI/MCP/Grafana) are untouched.

**Already built on `spike/runs-execution-model-v2` (no change):** uniform vectors;
vector carries inputs/outputs/custom; step sheds inputs; units on `configure`/`observe`;
`_auto_traceability` auto-stamping the connection signal-path onto **measurements**;
`ConnectionIterator`/`for_characteristic` per-pin loop from config; the EAV projection
+ dynamic-axis EAV-query speedup (parent branch).

**Two distinct inner loops (model clarification):** the `vectors` fixture loop →
new **conditions** → new vector rows; the `connections.for_characteristic` loop →
same condition, different measurement point → new **measurements/observations within
the vector** (the per-pin axis). The pin loop never creates vectors.

**Net:** of the five deltas, only #1 (nest measurements) is a structural reversal of
the branch; the rest are additive. After this, the at-rest is a clean nested archive
(vector ⊃ inputs/outputs/custom lanes + a measurements array, each measurement
optionally pinned), and the query layer is the unpacked fact + EAV.

### Observation pinning (#4 / #39) — grain & the EAV-field decision (2026-06-20)

Settled framing for where the pin lives:

- **Vector = the shared operating point** (conditions set once). **Pin = a leaf
  attribute** on the measurement/lane — NOT a structural level, NOT a vector
  coordinate. 8 pins at one condition = **one vector + 8 pin-rows**: the conditions are
  not duplicated (they're genuinely shared), only the measurements multiply (they're
  real, distinct per-pin data, not redundant). This is *why* the `dynamic_attrs` MAP is
  legitimately vector-grained — putting pin at vector grain would wrongly duplicate the
  shared conditions 8×.
- **"8 pins feel like 1"** = one `for conn in connections` loop in test code; the
  platform stamps the active pin per row. Uniform code, honest grain — "one setup, many
  probes."
- **Grain follows the loop expression:** `parametrize(pin)` → pin is a swept condition →
  vector-per-pin; `for conn in connections` (same conditions) → intra-vector pin-rows.
  Deliberately **no connection/pin container** between vector and measurement — pin is
  the unit of unique data, not a unit of shared context.
- **Optional, uniform across `verify`/`measure`/`observe`:** pin is stamped *iff* a
  connection is active. No connection loop → `uut_pin` is simply absent (the plain
  `observe("cap", wf)` / pure-pytest path stays simple). Mirrors `_auto_traceability`'s
  existing empty fall-through.

**The open decision — does observation-pin go in the EAV?** Measurements carry
`uut_pin` on the **fact** (`measurements_materialized`), queryable via `WHERE uut_pin=…`
without touching the EAV. **Observations have no fact row** — `observe` writes only an
`out_<name>` lane — so an observation's pin can only live in the lane-derived
structures: the EAV (`measurements_dynamic`) and/or the `dynamic_attrs` MAP. The EAV
today has **no `uut_pin` column** (`_LANE_SELECT` drops it; the dynamic-axis catalog
deliberately excludes `*_uut_pin` as traceability, not an axis). So #39 forks:

- **Traceability-only** — pin rides the lane's `uut_pin` field; **no EAV schema change**;
  consistent with "pin is not a dynamic axis"; but observation-pin is not
  filterable/groupable in the dynamic-axis path.
- **Queryable** — add `uut_pin` to the EAV; cheap (the lanes already carry it at-rest, so
  it's projection-only; additive column; the pre-release index rebuild is free).

**Resolved (2026-06-20): queryable, via an EAV `uut_pin` column.** No strong reason against,
and it's cheap because the lane is a **shaped record** — `name, kind, value_int/double/bool/
text/timestamp/json, unit` — and **`unit` is the precedent**. `uut_pin` is just one more field
on that shape, taking the *identical* path `unit` already took: at-rest lane → `_LANE_SELECT`
→ EAV column (+ insert SELECT). The at-rest lanes already carry `uut_pin`; `_LANE_SELECT` only
drops it today. Keep it a **filter column, NOT a dynamic axis** — the catalog already excludes
`*_uut_pin`, so pin stays *indexed traceability*, not a condition.

The `dynamic_attrs` MAP is a **separate, lossy `VARCHAR→VARCHAR` collapse** (`_LANE_VALUE_VARCHAR`
flattens the typed value to one string) — it **already drops `unit`**, and the row-wise consumers
(`steps_query`/`run_store`) read name→value and don't need it. So pin doesn't belong in the MAP
any more than `unit` does; there's no MAP "rework" to defer — the MAP simply isn't pin's home,
the shaped EAV is.

Watch the DISTINCT grain (NULL-pin conditions stay one row) and byte-stable output (filter-only;
don't surface the column in existing query outputs).

**`uut_pin = NULL` means "applies to ALL pins", not "unknown".** A capture taken outside a
connection loop — e.g. one multi-channel file covering every channel at once — is pinless by
nature; its measurements carry `uut_pin = NULL` meaning board-/all-pins scope. The field is
genuinely *per-pin OR all-pins*. **Query consequence:** a per-pin view must treat NULL as a
match — `WHERE uut_pin = :pin OR uut_pin IS NULL`, never bare equality — or the all-pins rows
that legitimately apply to that pin get silently dropped.

### `dynamic_attrs` MAP vs the EAV — two projections, not redundancy (KEEP, 2026-06-20)

Investigated whether the inline `dynamic_attrs MAP(VARCHAR,VARCHAR)` on
`measurements_materialized`/`steps_materialized` can be dropped in favour of pivoting the typed
`measurements_dynamic` EAV (measure-first, #43). **Conclusion: KEEP — they are two projections for
two access patterns, not a lossy duplicate.**

- **MAP = row-wise, overlay-uniform, single-run.** `runs`/`steps`/`measurements` are UNION VIEWS
  (`_runs_duckdb_daemon.py` `_create_views`, ~1451) splicing the `*_materialized` tables with
  `overlay.inflight_*` (an attached in-memory DB fed by `AccumulatorPool`). The overlay carries the
  MAP directly (`_accumulator_pool.py:357,417`; built in `_event_accumulator.snapshot_step_rows`
  ~281/329). The two row-wise readers — `steps_query.py:171`, `run_store.py:215` — read the MAP off
  the unified view, so they work identically for finalized AND in-flight runs in one columnar fetch,
  no join.
- **EAV = column-wise, cross-run.** `measurements_dynamic` is populated ONLY at parquet ingest
  (`INSERT INTO measurements_dynamic` at ~1019 and ~1590) — there is **no inflight EAV tier** and it
  is not in the UNION view chain. It serves the parametric/explore/cross-run dynamic-axis queries.
- **Removal is blocked on correctness, not taste.** Pivoting the EAV from the row-wise readers would
  return empty in_/out_ for every in-flight run (silent live-run data loss). Restoring that would mean
  building a parallel in-memory EAV overlay fed by `AccumulatorPool` — equivalent scope to what the MAP
  already does, plus a GROUP BY on every read.
- **No perf incentive either.** Measured single materialized run (20 steps × 10 in_/out_ lanes):
  MAP read+expand **541µs median**; equivalent EAV pivot+expand **905µs median** (~1.67× slower). The
  MAP is pre-computed at ingest; the pivot pays an aggregate per read.

So "why does the MAP exist / why keep it" = it is the live-uniform row-wise read path; the EAV is the
cross-run analytical path. Neither is legacy.

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
- **A — outer (container) vs inner (step) vectors. LOAD-BEARING — RESOLVED (grain reshape).**
  Resolution: no new field. `step_path` carries the hierarchy (container/method nesting);
  `step.vector_index` is re-meant to the **enclosing parent iteration** (NULL if not nested
  under a swept parent). A class-outer `testerkit_sweeps` iteration emits `VectorStarted`/
  `VectorEnded` for the container step; a nested method's `StepStarted.vector_index` is the
  enclosing iteration index. Null-vs-0 reconstruction in `_event_accumulator._parent_emitted_vectors`
  distinguishes "no enclosing loop" (→ NULL) from "enclosing loop iteration 0" (→ 0).
  See `step-vector-grain-reshape.md` §"The key idea — one relative `vector_index`".
- **B — container (class) row:** resolved — container step emits its own StepStarted/StepEnded
  (separate from the outer VectorStarted/VectorEnded); both persist as distinct row kinds.
- **Condition dimension placement:** resolved — merged conditions pre-assembled at capture via
  child-context-off-base (Phase 4 hygiene); no recursive walk at projection time.
- **Units:** shipped (`unit=` on `configure`/`observe`/`verify`; lane `unit` field populated).

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

## Phase 2 record spec — the "right parquet" (the scenario-test contract)

Record grain: `record_type ∈ {run, step, vector, measurement}`, keyed
`(step_path, parent_path, vector_index, retry)`; `inputs`/`outputs`/`custom` as
nested `LIST<STRUCT<name, kind, value_int/double/bool/text/timestamp, value_json,
unit>>`. **A `vector` record appears ONLY for Mode-2 in-body iterations** — Mode 1
and containers fuse (the `step` record IS the step≡vector). A measurement's full
condition = `inputs` merged up the `parent_path` chain.

1. **single / unswept** (`def test_v(context)`): `run` + `step(test_v, vec=0,
   retry=0)` + `measurement`. **No separate `vector` row** (fused).
2. **parametrize (Mode 1)** (`@parametrize(vin=[3.3,5.0])`): one `step` per item,
   distinct `node_id`, `vector_index` 0/1; measurements under each. No `vector` rows.
3. **self-loop (Mode 2)** (`vectors` fixture, 3 rows): ONE `step` + **3 `vector`
   rows** (`vector_index` 0/1/2 from `VectorStarted`) + measurements under each.
4. **class container × method**: `step(TestC, vector_index=outer, inputs={temp})`
   ⊃ `step(TestC/test_m, parent_path=TestC, vector_index=inner, inputs={vin})`;
   measurement condition = `{temp, vin}` merged up `parent_path`.
5. **retry**: Mode-1 → a second `step` row, `retry=1`. Mode-2 → a second `vector`
   row, same `vector_index`, `retry=1`.
6. **measurement-less**: assert-only → `step`/`vector` `outcome=FAIL`, **zero**
   measurement rows, **no** `name="assert"` row. observation-only → `outputs=[…]`,
   zero measurements, **no** NULL-named DONE row.

**Files (each scoped):** `_row_helpers.py` (lane encode + `record_type` gains
`vector` + `to_flat_dict` nested + `build_vector_row` + tree walk in
`build_step_manifest`/`iter_rows`; delete the `name="assert"` synth at harness.py:1546
keeping `vector.outcome=FAILED`); `schemas.py` (`RUN_ROW_SCHEMA` → 3 nested
`LIST<STRUCT>` + `record_type` enum gains `vector`); `_event_accumulator.py`
(dispatch `VectorStarted`/`VectorEnded` → vector rows; delete `_build_promoted_rows`;
drop `retry_count = MAX(vector_retry)` reliance); `parquet.py` (materialize +
reconstruct the 4 record types; converge with the accumulator snapshot).

**Execution approach:** TDD — scenarios 1/3/5/6 via the offline `TestRun →
materialize_run_to_parquet` path (no daemon); scenarios 2/4 via `pytester` (plugin
expands them) reading the resulting parquet. The two paths MUST converge on
identical records — that's the main correctness risk. The lane encoding can be
lifted from `stash@{0}` (re-pointed at the vector grain).

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

## Retry & outcomes — de-fuse to one row per execution (0.3.0 design, 2026-06-27)

**Resolves the known gap flagged at the top** ("Mode-1 rerun retry_count not carried")
and supersedes the narrow "drop retry_count" framing. The v2 contract already declared
*"every execution materializes a row — no Mode-1 fusing"*; the implementation never
delivered it. This is finishing that kill, and `retry_count` falls out dead as a
consequence — it was only ever the patch over the un-killed fusing.

**The bug (verified 2026-06-27).** The accumulator keys `_step_starts`/`_step_ends` by
`(step_path, vector_index)` — no attempt — so a `StepStarted(retry=1)` *overwrites* the
`retry=0` entry: one step row per `(step_path, vector_index)` however many times it reran.
`_build_scope_vector_results_from_events` then synthesizes the scope vector hardcoded to
`retry=0`. So Mode-1/scope reruns fuse into one row. `test_scenario_4` *asserts* the fused
shape (`kinds["step"] == 1`, `kinds["vector"] == 1`) — the de-fuse was never even attempted
at the test level. (In-body Mode-2 vector retries are *not* fused — those rows are keyed
`(step_path, vector_index, retry)` and are already distinct.)

**The model — stable indices + two OBSERVED retry coordinates; derive only aggregates.**
- `step_index` = **execution position**. Assigned in `assign_indices` from collection
  order, which *is* execution order; there is no separate "collection vs execution" index.
  **Stable**: parametrize variants share it (`StepKey` uses `func.__name__`, so the `[N]`
  suffix never reaches the key — verified `hooks.py:714` + `assign_indices`); a rerun reuses
  it (same collected item).
- `vector_index` = the point within the step (parametrize variant *or* in-body sweep
  point; timestamps tell those two apart — parametrize coincident, in-body nested).
- **`step_retry` / `vector_retry` = the attempt coordinates the producer OBSERVED** — two
  distinct axes (the *outer* item attempt, the *inner* in-body vector attempt). **Stored, not
  derived**: an observed source fact like `outcome` or a timestamp — nearly free (mostly 0,
  RLE → ~nothing). *Sourcing status (verified 2026-06-27):* `vector_retry` IS sourced — the
  harness `run_with_retry` loop sets `vector.retry` → `VectorStarted.retry` (`harness.py:1693`/
  `:587`). **`step_retry` is NOT** — `StepStarted.retry` is never passed at emit
  (`run_scope.py:816`), so it's always 0. So this isn't un-conflating one field; it's *adding
  the missing outer axis* and sourcing it (from the pytest item-rerun count — confirm the exact
  pytest-rerunfailures attribute, likely `item.execution_count`, when implementing).
- **Full execution identity = `(step_index, step_retry, vector_index, vector_retry)`.** Every
  execution is a distinct row at that key. A rerun is a *different key* (incremented retry),
  so it can't overwrite the prior attempt — which **is** the anti-fusing mechanism.

**Coordinate vs rollup — the distinction that resolves the whole thread.**
- A per-row **retry coordinate** (`step_retry`/`vector_retry`) is a *source fact* the
  producer witnessed. **Store it** — it's the de-fuse key *and* a self-describing read
  convenience (a row says "step attempt 1, vector attempt 0" standalone).
- **`retry_count`** is a derived *aggregate* (`MAX(vector_retry)`). **Don't store it** —
  derive on demand. Killing the rollup was right; it never required killing the coordinate.

  *Store what was observed; derive what's aggregated.*

**Two retry axes — outer (item) and inner (vector):**
- **step retry** (outer) = a failed test *item* re-runs via pytest-rerunfailures → the whole
  step + its inner loop re-execute → `step_retry++`.
- **vector retry** (inner) = the harness `run_with_retry` loop re-runs one vector *in place*,
  before `StepEnded` → `vector_retry++`.

**Retry definitions — count the right grain.**
- `step_retry` = how many times the **step (the pytest item) executed** (− 1). One count per
  step *execution*, **not** per vector. *Do not* count `step_index` occurrences across vector
  rows — a vectorized step has N vectors sharing one `step_index`, so that would count its
  vectors as step retries. Source: `item.execution_count − 1`, stamped on `StepStarted` at emit.
- `vector_retry` = how many times this **`(step_index, vector_index)` executed** (− 1).

The two relate by grain, not by a floor:
- **Scope vector (1 per step execution): `vector_retry = step_retry`.** The single vector runs
  exactly once per step execution, so its occurrence count *is* the step's — you know it from
  the step, no separate counting (S1, S3 → equal).
- **Iteration vectors (inner loop):** count occurrences of `(step_index, vector_index)`. Tracks
  `step_retry` as a baseline but **diverges** — *above* on an in-body `testerkit_retry` (S4:
  `(0,1)` runs 3×), *below* on a conditional skip (a vector that doesn't run every attempt). So
  neither bounds the other; they're independent counts that coincide only when a vector runs
  once per step attempt.

A step rerun and an in-body retry both make the vector's `(step_index, vector_index)` appear
again and both count toward `vector_retry`; cause is irrelevant to the count.

**Where it's computed.** `step_retry` is event-stamped (`execution_count`). `vector_retry` can
*also* be event-time: a `{(step_path, vector_index): count}` cache on the **session-scoped
`RunScope`** (which survives reruns — they run in-process) increments per vector execution; the
scope vector just takes `step_retry`. (Equivalently, derive at ingest via
`ROW_NUMBER() OVER (PARTITION BY step_path, vector_index ORDER BY time)`.)

Worked rows (S1: 1:1 step rerun; S4: loop + in-body retry of vec 1):

```
S1   step_retry vector_index vector_retry
       0          0            0
       1          0            1     ← (0,0) ran twice; scope vector ⇒ vector_retry = step_retry
S4     0          0..2         0       (attempt 0, three points)
       1          0            1       (attempt 1: each ran once per attempt ⇒ tracks step_retry)
       1          1            1
       1          1            2     ← (0,1) ran an extra in-body time ⇒ above step_retry
       1          2            1
``` (Contrast: unstored,
you'd attribute vectors to step attempts by `[started_at, ended_at]` containment + `ROW_NUMBER`
— correct but fragile to timestamp coincidence; the stored coordinate removes that, timestamps
stay informational.)

**Derived at ingest — only the aggregates:**
- step attempts = `COUNT(DISTINCT step_retry)` per `step_index`
- vector attempts = `COUNT` rows per `(step_index, step_retry, vector_index)`
- "retried?" / retry rate / any rollup — computed from the stored coordinates, never stored
  back.

**Outcomes get better, not just tidier.** Today fusing runs `retry_aware_rollup`, which
collapses node-id-sharing steps to their *final* attempt — **destroying** attempt-1's
outcome at the projection layer (a passed-after-retry reads identical to a passed-first-try;
only `retry_count` hinted, and only at "how many," never "what"). One honest row per
execution keeps every attempt's real outcome, and the rollup becomes derived:
- **First-Pass Yield** = first step-attempt's outcome (now a *real, direct* metric)
- **final/effective** = last attempt's outcome
- **retried?** = attempt count > 1

`retry_aware_rollup` dissolves — it existed only to pick a winner from rows fusing destroyed.

**Where things live — observed in the archive, aggregates at ingest.** Store the *source
facts* (`step_retry`/`vector_retry`, indices, outcomes, timestamps) in parquet — that's what
lets an external/lakehouse reader interpret a row standalone. Derive the *aggregates* (counts,
rates, rollups) at DuckDB ingest, recomputed on rebuild (no drift). Never bake an *aggregate*
into the archive — that's the `retry_count`-in-the-metadata-blob anti-pattern this session
deleted. The line is observation-vs-aggregate, not store-nothing.

**The work (at-rest → rides the 0.3.0 `schema_version` bump):**
- Accumulator: key step + scope-vector by the **full** `(step_path, step_retry, vector_index,
  vector_retry)` identity instead of `(step_path, vector_index)` — so a rerun is a distinct
  key (no overwrite, no fusing); emit one row per execution, each with its own
  boundaries/outcome.
- **Source `step_retry` (NEW — verified missing 2026-06-27).** `vector_retry` is already
  sourced (harness `run_with_retry` → `VectorStarted.retry`); `step_retry` is **not** —
  `StepStarted.retry` is never passed at emit (`run_scope.py:816`), always 0. So the producer
  must stamp it: read the pytest item-rerun count (likely `item.execution_count` from
  pytest-rerunfailures) in the runtest hook → `StepStarted.retry`. Then add `step_retry` to
  `RUN_ROW_SCHEMA` alongside the already-present `vector_retry`. **This is a producer change,
  not just an at-rest schema edit** — the de-fuse touches producer + accumulator + schema.
- Drop the dead **rollups** `retry_count` / `has_measurements` / `vector_count` (the metadata
  blob via `step_entry_dict`, the daemon projection, the inflight overlay, `StepRow`) — no
  consumer left.
- Remove **`retry_aware_rollup`**; the outcome rollup becomes a query-layer aggregation.
- Flip `test_scenario_4` from asserting one fused row to asserting two execution rows — the
  test that locked in the bug becomes the test that proves the fix.

## Inputs are vector-scoped and stable — honor `configure()` at the vector grain (decided 2026-06-29)

**Decision.** The vector *is* the input set: it binds inputs, outputs, and measurements for one
execution. Inputs are **vector-scoped and stable** — every measurement in a vector shares one
input set. We do **not** support a per-measurement input axis (e.g. `configure()` between two
measurements of the same vector yielding different inputs per measurement). One input set per
vector, full stop.

**What this settles.**
- **Storage:** one input set per vector row; measurements carry no inputs at rest
  (`_measurement_event_struct` correctly omits them). "Input search misses measurements with
  extra inputs" is a non-issue *by decision* — there is no per-measurement input to miss.
- **Event:** `MeasurementRecorded.inputs` stays on the wire for live subscribers (computed at
  measure time, `run_scope.py`), understood to be vector-stable — never persisted per
  measurement. Keep it; don't store it.
- **`vector_index` is positional and ALWAYS unstable — never an analysis key.** It reorders
  when vectors vary (validation vs production run different vectors), so the identity of a
  vector is its **input values**, not its index. Any analysis keyed on `vector_index` alone is
  wrong by construction. Corollary: the at-rest layer must not *merge* two executions just
  because they share a positional `(step_path, vector_index, retry)` — distinct input sets are
  distinct vectors.

**The defect this exposes — inputs lane is snapshotted too early.** The stored inputs lane is
sourced from the **start** event (`StepStarted`/`VectorStarted`) in the accumulator, while
outputs come from the **end** event. `configure()` runs in the test body, *after* the start
event, so configure-added inputs never reach the stored lane — though they appear on the live
event and in the CSV/DataFrame export (`iter_rows`→`build_row`, which reads in-memory
`vector.params` at end). So the canonical stored surface is the lossy one and disagrees with
the other two.

**Seed on Started, latch on Ended — per row grain.** Each Step/Vector parquet row is seeded by
its `Started` event and latched (overwritten) by its `Ended` event at write time. `configure()`
mutates the in-memory params, so the `Ended` snapshot captures the change: the **step row
latches `StepEnded`**, each **vector row latches its `VectorEnded`**. While in-flight (no End
yet) the overlay reads the Started snapshot; at finalize End wins. This is `_end_overrides_start`
applied uniformly per grain — no special "step stays at Start" rule. `configure()` overriding a
parametrize key is visible because per-grain / per-vector End snapshots differ.

**The one real wrinkle — the Mode-1 bridge.** For `configure()` to reach `StepEnded`, the
configured values must be in `vec.params` by step end:
- **Mode-2 (in-body loop):** already handled — the harness re-reads the live context into each
  `vector.params` at vector end (`harness.py:1699`), so `VectorEnded` carries it.
- **Mode-1 (scope):** NOT automatic — `configure()` writes `Context._params`, but the scope
  vector's params come from the `active_vector_params` ContextVar seeded once at
  `autouse.py:156` (before the body). So `_emit_step_event` (step end) merges the live context
  into `vec.params` — but **only the keys `configure()` actually set** (`Context.configured_params`,
  tracked separately from parametrize `set_params` seeds), so a configured key overrides while
  untouched parametrize values are preserved. Gated to Mode-1 via `_step_ran_inbody_loop` (the
  in-body occurrence tracker) so it never folds context onto a multi-vector step's `vectors[0]`.

No-drift: overlay and parquet differ *only while a block is in-flight*; once `End` arrives both
converge.

## Deferred to before 0.3.0 — de-fuse / configure robustness follow-ons

These were surfaced during the de-fuse review and consciously deferred (out of the de-fuse
commit's scope); the common-path behaviour is correct, these harden edges. **Must land before
tagging 0.3.0.** Tracked as a task; recorded here so it survives memory.

1. **Rerun iteration-vector step timing** — an in-body vector in a rerun step reads the
   *lowest-retry* StepStarted/StepEnded timing (`_min_retry_match`), not its own attempt's. Needs
   `step_retry` on `VectorStarted`/`VectorEnded` so the accumulator can resolve per-attempt.
2. **~~Scope-vs-iteration aliasing (Scenario A)~~** — **RESOLVED (grain reshape).** The
   accumulator's `_partition_measurements` distinguishes step-scope from vector-scope by
   `_looped_keys()`: a measurement with `(path, vector_index)` NOT in `_looped_keys()` goes
   to the step row; one that IS in `_looped_keys()` goes to its vector. No coordinate clash.
3. **~~Data-dependent Mode classification~~** — **RESOLVED (grain reshape).** `_step_ran_inbody_loop`
   is deleted (Phase 4). Per-execution Mode is signaled by whether `VectorStarted` events were
   emitted for that step; the `_parent_emitted_vectors` check in the accumulator provides the
   null-vs-0 reconstruction for the step's `vector_index`.
4. **Ambient-context coupling** — the step-end merge reads `get_current_context()`, which at a
   container/auto-close `StepEnded` may not be the step's owning context. Thread the owning
   `Context` into `_emit_step_event` instead.
5. **`configured_params` vs `set_params`-after-`configure`** (minor) — a parametrize `set_params`
   landing after `configure()` for the same key is still reported as configured. Contrived;
   tighten the contract if it ever bites.
