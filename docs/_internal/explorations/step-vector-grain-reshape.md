# Step / vector grain reshape — vectors are condition points, steps are code

**Status:** design, converged (2026-06-30). Shaped in discussion; **not implemented**. **No new
at-rest fields** — this is a *semantic + data-placement* reshape of existing columns, carried by
the `schema_version` scheme (so it's reversible if we get it wrong).

> **Addendum (superseded):** "No new at-rest fields" did not survive implementation. A follow-up
> fix locked 2026-06-30 adds one new coordinate, `vector_outer_index`, and makes `step.vector_index
> ≡ NULL` an unconditional invariant (no more null-vs-0 hack) — see "Follow-up fix:
> `vector_outer_index` (nested-vector identity + correct retry) — LOCKED 2026-06-30" in
> `step-vector-grain-reshape-execution.md`.

**Origin:** the #24 "de-fuse / configure robustness" follow-ons
(`runs-execution-model.md` §"Deferred to before 0.3.0"). Walking **#2** (scope-vs-iteration
aliasing) and **#3** (data-dependent Mode classification) collapsed both into one model. Shared
root cause: `step.vector_index` did **double duty** — the step's own sweep variant *and* the
container iteration (see `_event_accumulator._step_key` docstring).

**Reverses** two v2 decisions in `runs-execution-model.md` ("uniform vectors"; "step sheds
inputs onto a synthesized scope vector") and **resolves** its deferred `enclosing_vector_key` /
"Open A — outer vs inner vectors (LOAD-BEARING)."

---

## One breath

A **step** is a unit of test *code* (function/method today; sequence/subsequence later). A
**vector** is a *condition point* — one parameter combination or one in-body iteration — that
belongs to a step. A **measurement** belongs to a vector. The tree strictly alternates
**step → vector → step → vector**; there is no vector→vector edge.

## The key idea — one relative `vector_index`

> **`vector_index` = the row's 0-based position in the loop it's in, relative to that loop.**
> Null when the row is in no loop.

A "loop" is any of:
- a **parametrize / sweep** on a step → its points are that step's **vectors**,
- an **in-body** `vectors` / `context.vector()` loop → its points are that step's **vectors**,
- a **parent step's sweep** → a nested **substep** runs *at* one of its points.

So `vector_index` unifies all three. The only difference between them is *where the loop's
points materialize* — as the step's own vector rows, or as the position a nested step ran at —
not what `vector_index` means. Per record type:
- **vector row** → its own position in its step's (flattened) loop,
- **step row** → which iteration of its **enclosing** loop it ran in (the parent's), **null** if
  it isn't nested inside a loop. *Mostly null in practice.*

This is **not** the old harmful double-duty (two meanings on the *same* record type). It gives a
field that's otherwise dead on step rows (`step.vector_index` is structurally always-null today)
one coherent, relative meaning — at **no new-column cost**. Unifying read across record types:
*"which iteration of the nearest enclosing loop this row sits at."*

## Coordinates — all existing fields, no additions

- `step_path` — the structural **hierarchy** path (`"/".join(step_stack)`, e.g. `C/m`). Display
  name + the parent link (the prefix is the parent). *Not* a disk path (`step_file`/`step_module`
  are separate). The lineage walk goes up this.
- `step_index` — **local** position among siblings (from `assign_indices`; resets per parent —
  partitioned by `class_name` today). Display/order.
- `vector_index` — the relative loop position above. On vector rows = own; on step rows =
  enclosing iteration (mostly null). Nullable.
- `vector_retry` — in-body re-execution of a vector within its step execution.
- `step_retry` — outer item-rerun attempt of the step.

**Lineage / merged condition** = walk `step_path` up; at each level the child's `vector_index`
selects which of the parent's vectors it ran under, so the projection pulls the parent's
condition from that vector. Assembled once at ingest. (For m@temp=25: `step_path=C/m`,
`vector_index=0` → C's vector[0] → `temp=25`, merged with m's own vectors' `x`.)

## Invariants

1. **Strict step→vector→step alternation.** No vector nests in a vector; deeper structure
   crosses a *step* boundary (a substep with its own vectors).
2. **Config multi-dim sweeps flatten.** `expand_vectors` pre-expands a step's sweep matrix into
   one flat vector list; dimensions live in each vector's *inputs*, not as nested rows. *Within
   a step → flat; across steps → hierarchical.*
3. **Steps carry their own data.** Step rows latch their own `inputs`/`outputs`/`measurements`
   (scope data, latched on `StepEnded`). A non-looping step can have **zero vectors**. The
   synthesized scope vector is **deleted**.
4. **Identity vs aggregation are orthogonal.** Local `step_index`/`vector_index` along the
   `step_path` chain = *identity* (multidimensional). Merged condition **values** = *aggregation*
   (flat), assembled up the chain at ingest.
5. **`vector_index` is a within-run parametric X** — positional, **unstable cross-run**; a fine
   "pick as X" within a run, never a cross-run `GROUP BY` key (cross-run keys on condition values).
6. **`vector_index` nullable** — null on run rows; null on step rows *unless* nested in a loop;
   0..N on vector rows.

## Layers

- **Event (WAL):** the producer stamps `step.vector_index` (the enclosing iteration) on
  `StepStarted`/`StepEnded` — it has it at a nested `start_step` (`get_current_vector()` of the
  parent). The step's own sweep variants stop riding `StepStarted.vector_index`; they become the
  step's vectors. No new event fields.
- **At-rest (parquet):** existing columns, re-meaning'd `vector_index`; vectors are **leaf
  carriers** (own `vector_index` + inputs + measurements). Lineage by walking `step_path`.
- **Projection (DuckDB):** UNNESTs measurements from **both** step and vector rows into one flat
  fact; walks `step_path` + each level's `vector_index` to pre-assemble the flat **merged
  condition** onto each fact. Aggregation is `GROUP BY` on condition columns — recursion happens
  once at ingest.

## Permutation table

`vector_index` column = the **vector row's own** index. Step-row coordinate shown as
`step.vector_index`.

### Flat steps (not nested → `step.vector_index = null`)

| # | Test shape | Loop | Step-exec rows | Vector rows (own `vector_index`) | Step's own scope data? |
|---|---|---|---|---|---|
| A | `def t(ctx)` | none | `t` | — | **yes** (its measurements) |
| B | `@parametrize(v=[0,1]) def t(ctx)` | external | `t` (logical group) | `v` → 0,1 | no (all on vectors) |
| C | `def t(ctx, vectors)` (matrix=2) | internal | `t` | iteration → 0,1 | **yes** (setup/teardown) |
| D | `for c: with context.vector(c)` | internal | `t` | activation → 0..n | **yes** |
| E | `def t(ctx)` fails→reruns | none | `t`×2 (`step_retry` 0,1) | — | yes, per attempt |
| F | internal loop, whole step reruns | internal | `t`×2 | iters under each | yes, per attempt |

### Nested (`step_path` carries the hierarchy)

| # | Test shape | Step-exec rows | Vector rows (own `vector_index`) | `m.step_path` / `m.vector_index` |
|---|---|---|---|---|
| G | `class C: def m(ctx)` (C not swept) | `C`, `m` | — | `C/m` / **null** (C made no vector) |
| H | `C litmus_sweeps(temp=[25,85])` + `def m(ctx)` | `C` + C-vecs(temp 0,1); `m`×2 | C: temp → 0,1 | `C/m` / **0,1** |
| I | swept `C` + `def m(ctx, vectors)` (inner=3) | `C` + C-vecs(0,1); `m`×2 + m-vecs(0,1,2 each) | C: temp; m: iters | `C/m` / **0,1** (m's own vecs normal) |
| J | `class C: @parametrize(v=[0,1]) def m(ctx)` (C plain) | `C`, `m` + m-vecs(v 0,1) | m: v → 0,1 | `C/m` / **null** |

m@temp=25 vs m@temp=85 (H/I) share `step_path` + `step_index` and differ only by
`step.vector_index` (0 vs 1).

## Worked example — outer×inner aggregates identically to the fused form

`litmus_sweeps(temp=[25,85])` (class, outer) + method inner `vectors` over `x=[0,1,2]` → six
`vout` measurements.

- **Hierarchical (this reshape):** `C` → 2 C-vectors (temp) → `m` per temp (`m.vector_index`
  0/1, `step_path=C/m`) → 3 m-vectors (x, own 0,1,2) → `vout`. Projection assembles `{temp, x}`
  up the chain.
- **Fused (no inner loop):** all sweeps on the step → `expand_vectors` flattens 2×3 into six
  step-vectors at `vector_index 0..5`, each with `{temp, x}` directly.

**Both produce six `vout` facts tagged `temp` + `x`.** Every analytic groups on those input axes
(`GROUP BY temp`/`x`/`temp,x`) — never on `vector_index` or structure. Same fact table, same
`GROUP BY`, byte-identical output. The choice is purely structural/execution: fused re-runs the
body per full combination; hierarchical amortizes per-temp setup across the x-loop. Placement of
the sweep picks which; analytics can't tell.

## Aggregation is union-free at the query layer

The daemon UNNESTs measurements from **both** step and vector rows into one flat fact at ingest;
every fact row carries its full owner coordinate. Litmus reads go through the projection, never
raw parquet, so:
- *all of a step* = filter on its `step_path` (+ chain) — its own scope measurements **and**
  every vector's, **no union**;
- *just the step's own* = `… AND vector_index IS NULL`;
- *just a vector's* = `… AND vector_index = k`.
The only "union" is the one-time ingest UNNEST. (A direct raw-parquet reader without the daemon
would scan two record types — the projection spares Litmus readers that.)

**Consequence — measurement queries are source-agnostic.** A query for a measurement never
needs to know whether it was logged at *step scope* or inside a *vector*; the projection
**promotes** step-row and vector-row measurements into the *same* flat fact under the *same*
coordinate, so consumers filter, never branch on `record_type`. This is a hard requirement of
the reshape: both step and vector measurements (and inputs/outputs) must promote to the right
place, and no query may care about the source row kind.

## Future-proofing — sequences / subsequences

`step_index` is local (per parent, today `class_name`), and `step_path` (names) is the current
global identity. For **reusable subsequences** — the same subsequence called from two sites
yields the same name-path — names stop being globally unique, and a **surrogate execution id**
(per step execution) becomes the clean global key, with `step_index`/`vector_index` still local
position. That's an at-rest change (a new field) deferred until sequences actually land —
covered by the `schema_version` scheme, not built now. Generalizing `assign_indices`' parent
partition from `class_name` to an arbitrary container is the other half. Until then, `step_path`
suffices.

## Open / remaining

- **Outcome rollup** — a step's outcome rolls up from its own data + its vectors + nested steps;
  confirm.
- **Parent rerun with substeps** — if a *parent* step reran, a substep's `vector_index` (which
  parent loop iteration) doesn't say which parent *attempt*; needs the parent's `step_retry` to
  disambiguate. Rare; the surrogate-id future fix covers it. Note the limitation.
- **#1 (`step_retry` on vector events)** — folds into the vector→step linkage; the uncommitted
  #1 change stays shelved until reconciled.
- **Sequencing & blast radius** — bigger than 0.3.0 hardening. Decide whether it *is* the 0.3.0
  reshape. S1–S6 retry-model tests change; scope-vector code (`build_scope_vector_row` /
  `_build_scope_vector_results_from_events`) deleted; the daemon projection gains the
  chain-walk; the accumulator stops keying `step.vector_index` as the step's own sweep variant.

## Promoted from the #24 hardening iteration (2026-06-30)

- **Child-context per in-body iteration (Mode-2 context hygiene).** Today `_VectorIterator`
  *mutates the shared step context* each iteration (`self._ctx._params.clear(); ...update`,
  `pytest_plugin/__init__.py:1153`), so configured keys bleed across iterations and the step
  context ends at the last vector's values. Under the reshape the iterator pushes a **child
  context** based on the step's base and **resets to base** each iteration, so a step-scope
  `configure()` (before/around the loop) survives and the step base stays unpolluted. This
  pairs with steps-carry-own-data (it gives those surviving step-scope configures a slot) and
  **removes the `_step_ran_inbody_loop` gate** the minimal #4 fix relies on. Mode-1
  (parametrize) needs nothing — each variant is a separate pytest item with a fresh
  function-scoped context already. *(The minimal #4 owning-context fix shipped in the #24
  iteration; this is its clean successor — see `_emit_step_event` / `_owning_contexts`.)*

## Supersedes / relationship

- Reverses `runs-execution-model.md` v2 **decision 1** ("uniform vectors") and **decision 2**
  ("step sheds inputs onto a synthesized scope vector").
- Resolves its **"Open A — outer vs inner vectors (LOAD-BEARING)"** and the deferred
  `enclosing_vector_key` — replaced by *no new field*: `step_path` (hierarchy) + a re-meaning'd
  relative `vector_index`.
- Subsumes #24 **#2** (scope-vs-iteration) and **#3** (Mode classification). #24 **#4**
  (ambient-context coupling) still stands.

## Decision log

- **2026-06-30** — converged in discussion. Path: started "add a scope coordinate"; rejected
  `enclosing_vector_key` (presumes a vector always encloses); verified both `step_index` and
  `vector_index` are *local* (`assign_indices` partitions by `class_name`); considered a
  surrogate `step_id` + `enclosing_step_id`, then **dropped it** for a **no-new-field** model —
  `vector_index` gets one *relative* meaning at every level (own on vectors, enclosing iteration
  on steps), unifying parametrize / inner-looping / hierarchy; hierarchy rides existing
  `step_path`. Confirmed strict step/vector alternation (config flattens; nesting only via
  steps) and union-free aggregation. Surrogate deferred to the reusable-subsequence future.
  User convinced; at-rest reversible under `schema_version`. Not implemented.
- **2026-06-30 (close-out)** — #24 hardening iteration shipped the *minimal* #1 (rerun vector
  step-timing) and #4 (owning-context latch) on `feat/0.3.0-instruments`. #2/#3 subsumed here;
  #5 documented/deferred. Promoted into this reshape: the Mode-2 child-context cleanup and the
  source-agnostic-measurement-query requirement.
