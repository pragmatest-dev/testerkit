# Step / vector grain reshape — execution diary

**Branch:** `feat/0.3.0-grain-reshape`  ·  **Tracks #39**  ·  **Started 2026-06-30**

**Design contract:** [`step-vector-grain-reshape.md`](step-vector-grain-reshape.md) — the *what* and
*why*. This file is the *how*: phased plan + progress log. Read the contract first; do not
re-litigate it. **Plan-is-contract: no design deviations.** If a phase surfaces a missing element,
STOP and raise it here, do not auto-pilot a fix.

## Locked decisions (from the shaping + 2026-06-30 Q&A)

1. **No `schema_version` bump.** Pre-0.3.0, nothing shipped. We *redefine what `1.0` is* and wipe
   local data. `SCHEMA_VERSION` stays `"1.0"`. (The contract's "reversible under the version scheme"
   is the post-ship safety net, not a now-action.)
2. **Existing data: wipe + regenerate.** Repo `data/` parquet + the daemon DuckDB are rebuilt under
   the redefined `1.0`. **No read-time migration shim** (no-backcompat, pre-release).
3. **Outcome rollup = worst-of-all-children.** A step's outcome = worst of (its own scope data, each
   of its vectors, each nested step), precedence `failed > error > skipped > passed`.
4. **Scope = grain reshape only.** C4 (`uut_serial` rename) and C5 (instruments `list<struct>`) are
   *separate branches* that likewise refine `1.0` pre-release — not in this branch.
5. **`step_retry` on `VectorStarted`/`VectorEnded` already landed** (events.py) — contract "Open #1"
   is stale. The parent-rerun-disambiguation limitation is **documented, not fixed** (surrogate-id
   future).

## Producer mechanism — the one call the contract left implicit (DECISION, flag-for-veto)

The contract says parametrize variants "become the step's vectors" with "no new event fields." The
only mechanism consistent with that + the existing Mode-2 path is: **every sweep point emits a
`VectorStarted`/`VectorEnded` (reusing the existing events); `step.vector_index` is re-meant to the
enclosing parent iteration.** Three sweep sources unify onto that one path:

| Sweep source | Today | After |
|---|---|---|
| function `@parametrize` (Mode-1) | fuses into step row; `step.vector_index = variant` | emits a vector per variant; step is the logical group, `step.vector_index = enclosing` (null at top) |
| class-outer `litmus_sweeps` (hooks.py:1269) | container `C.vector_index = vi` | `C` emits a vector per outer point (`vector_index = vi`); methods get `step.vector_index = vi` |
| in-body `vectors` (Mode-2) | already emits vector events | unchanged shape; gains child-context hygiene (Phase 4) |

Rejected alternative: accumulator-*synthesizes* variant vectors from `StepStarted` — that is the
scope-vector pattern we are deleting, inverted. Don't.

A **non-swept** step (`def t(ctx)`, row A) emits **no** vector and carries its own
inputs/outputs/measurements, latched on `StepEnded`. Signal for "swept": the pytest item carries
callspec sweep params (Mode-1 / class-outer) or runs an in-body loop (Mode-2).

---

## Phases

Each phase ends with the gate (below). The suite goes **red mid-reshape** and returns green at
Phase 5 — sequencing localizes breakage to one layer at a time. Do phases in order; do not start the
next until the current one's own targeted tests pass (full-suite green is a Phase-5 deliverable).

### Phase 1 — Producer: unify sweep points as vectors; re-mean `step.vector_index`
**Files:** `execution/run_scope.py`, `execution/harness.py`, `pytest_plugin/hooks.py`,
`pytest_plugin/__init__.py`, `pytest_plugin/autouse.py`.

- `run_scope.start_step` (run_scope.py:680-694): stop auto-creating the step's *identity* vector from
  the variant index. The step's `vector_index` (StepStarted/StepEnded) = the **enclosing** parent's
  active vector index (`get_current_vector()` of the parent *before* this step pushes), `None`/0 when
  not nested in a loop.
- `_emit_step_event` (run_scope.py:747-827): delete the double-duty (`vec.index` → step
  `vector_index`). Step inputs/outputs/measurements are the step's **own** scope data (latched on
  `StepEnded`), not a vector's.
- **Vector emission for Mode-1 + class-outer:** bracket the swept item/container body with
  `VectorStarted`/`VectorEnded` carrying the variant's own `vector_index` + params. Mode-1 leaf
  variants: emit in the item lifecycle (autouse `_litmus_push_params` already brackets the item and
  holds the params, autouse.py:150-183). Class-outer: emit at the container open/close
  (hooks.py:1253-1276) per outer point.
- Mode-2 (`_VectorIterator`, __init__.py:1111-1186) already emits vector events — leave its emission;
  context hygiene is Phase 4.
- **Targeted check:** event-stream assertions — a parametrize step yields N `VectorStarted` (one per
  variant) + one logical step; a `def t(ctx)` yields a step + zero vectors; a swept class yields
  C-vectors + methods whose `StepStarted.vector_index` = the enclosing outer point.

### Phase 2 — Accumulator: delete scope vector; steps carry own data; vectors are leaf carriers
**Files:** `data/backends/_event_accumulator.py`, `data/backends/_row_helpers.py`,
`data/backends/parquet.py`.

- **Delete** `_build_scope_vector_results_from_events` (accumulator:587-686), `build_scope_vector_row`
  (_row_helpers:868-928), and their callers (parquet.py:~288-300 offline path; accumulator
  snapshot:~319). The synthesized scope vector is gone.
- `_partition_measurements` (accumulator:483-510): drop the `by_scope` lane. A measurement attaches
  to its owning **vector** when one is active, else to the **step row** (step-scope data). Key step
  rows `(step_path, step_retry, enclosing_vector_index)` — mostly `(path, retry, null)`.
- `_step_key` (accumulator:53-65): `vector_index` stops meaning "this step's own sweep variant"; it is
  now the enclosing iteration. Update the docstring.
- Step rows latch own `inputs`/`outputs`/`measurements`; a non-looping step → **zero** vector rows.
  Vector rows are leaf carriers (own `vector_index` + inputs + measurements), now including Mode-1
  parametrize + class-outer vectors (they emit events as of Phase 1).

### Phase 3 — Projection: UNNEST from BOTH step and vector rows; `step_path` chain-walk
**Files:** `data/_runs_duckdb_daemon.py`.

- `_measurement_unnest_insert` (daemon:1000-1031): UNNEST measurements from
  `record_type IN ('step','vector')` (today: `'vector'` only) into the *same* flat fact — **source-
  agnostic** (consumers filter, never branch on `record_type`; **no `kind`/discriminator column**).
- **`vector_index` has two roles — keep them apart (the easy bug):**
  - **vector row's `vector_index` → the fact's own leaf coordinate** (the within-run X). Lands on the
    fact as-is.
  - **step row's `vector_index` → a chain-walk *selector*, NEVER written to the fact.** It names which
    of the *parent's* vectors this execution ran under; it is consumed climbing the tree and turned
    into merged-condition *values*.
  - So the UNNEST sets `vector_index` by source: **vector-source → `v.vector_index` (own); step-source
    → `NULL` (literal, never `s.vector_index`).** This is what lets one table hold both without a
    discriminator. `m`'s step row enclosing `vector_index=0` and `m`'s vector row own `vector_index=0`
    do not collide: the step-scope fact is `NULL`, the vector-scope fact is `0`.
  - Two-branch insert:
    ```sql
    -- vector-scope: own index            -- step-scope: NULL, never s.vector_index
    SELECT v.…, v.vector_index AS vector_index   SELECT s.…, NULL::BIGINT AS vector_index
    FROM rows v, UNNEST(v.measurements) t(m)      FROM rows s, UNNEST(s.measurements) t(m)
    WHERE v.record_type='vector'                  WHERE s.record_type='step'
    ```
  - Query predicates this enables: step's own = `vector_index IS NULL`; a vector's = `vector_index=k`;
    all of the step = `step_path=…` (no vector predicate). **This is a change from today** — currently
    every step gets a scope vector stamping `vector_index=0`, so there are no `NULL`s; after the
    reshape `NULL` becomes the load-bearing "belongs to the step itself" marker.
  - **NULL-safe EAV join (Sonnet trap, verified):** the input filter joins `measurements` ↔
    `measurements_dynamic` on `_VECTOR_KEY` (`measurements_query.py:56-61`), which uses
    `{a}.vector_index = m.vector_index`. Now that `vector_index` is NULL-bearing, change that `=` to
    `IS NOT DISTINCT FROM` (exactly as `vector_retry` already does on the next line), or every
    step-scope measurement silently loses its dynamic-attrs join (`NULL = NULL` is never true) and its
    inputs go unqueryable. **No new join to step/run projections** — conditions stay at the fact's own
    grain (the fact↔same-grain EAV join); pre-merge already put enclosing inputs on the leaf's own EAV
    rows, so filtering an inner vector by an enclosing input needs no parent-projection join.
- **Merged condition is pre-assembled at capture, NOT walked at ingest.** Each step/vector row already
  carries its full merged `inputs` because the producer captures them from the **live inheriting
  context** (the enclosing sweep set `temp`; the inner loop adds `x`; the row's `inputs` is `{temp,x}`).
  So the projection reads the carrier's own `inputs` straight into `dynamic_attrs` — **no recursive
  `step_path` chain-walk.** This rests on the load-bearing invariant **"every row's `inputs` already
  contains all enclosing conditions,"** guaranteed by **Phase 4's child-context-off-base** (base = the
  inherited enclosing conditions, child = base + this iteration). Dense per row; parquet RLE compresses
  the repeated enclosing values (same philosophy as instruments dense-per-row).
  - Consequence: the step row's `vector_index` (enclosing iteration) is **no longer a projection
    selector** — purely positional identity now, only needed to disambiguate a degenerate same-valued
    sweep (the documented surrogate-id limitation; analytics groups on values regardless).
  - Producer responsibility (Phase 1/4): stamp merged inputs on every row. Test the invariant directly
    (a nested swept case asserts the leaf vector's `inputs` already contains the enclosing `temp`).
- Outcome rollup: worst-of-all-children (decision 3) when materializing step/run outcome.
- **Targeted check:** the worked example (contract §"Worked example") — hierarchical outer×inner and
  the fused form produce a byte-identical six-row `vout` fact table grouped on `{temp, x}`.

### Phase 4 — Mode-2 context hygiene + remove the gate
**Files:** `pytest_plugin/__init__.py` (`_VectorIterator`), `execution/run_scope.py`
(`_step_ran_inbody_loop`).

- `_VectorIterator` (__init__.py:1147-1154): stop mutating the shared step context
  (`self._ctx._params.clear(); .update(params)`). Push a **child context** off the step base; **reset
  to base** each iteration. A step-scope `configure()` around the loop survives; the base stays
  unpolluted (pairs with steps-carry-own-data).
- **Remove** `_step_ran_inbody_loop` (run_scope.py:541-547) and its use (run_scope.py:770) — the
  step-scope merge now flows from steps-carry-own-data + the owning-context latch (#4, already
  shipped). Mode-1 needs nothing (each variant is its own item with a fresh function-scoped context).

### Phase 5 — Tests + data regen + docs (return to green)
**Files:** `tests/test_data/test_retry_model.py`, new tests, `docs/`, regenerate reference docs, wipe
`data/`.

- **Retry model (S1–S6):** S1 (1:1 scope-vector) → a non-looping step that **carries its own data,
  zero vectors**; rerun = `step_retry` on the step row. S2–S6 (in-body) re-key but keep their counts.
- **New permutation tests** for contract rows A–J: parametrize→vectors (B/J), class-outer→C-vectors +
  `m.vector_index` (H/I), nested-not-swept→null `m.vector_index` (G), chain-walk merged condition,
  source-agnostic measurement query (step-scope and vector-scope measurements answer the same query
  without a union), worst-of outcome rollup, Mode-2 child-context hygiene (step-scope `configure`
  survives the loop).
- **Wipe + regenerate** `data/` fixtures and rebuild the daemon DuckDB under redefined `1.0`.
- **Docs:** mark `runs-execution-model.md` v2 decisions 1 & 2 reversed; update
  `runs-architecture-map.md`; regenerate marker docs (`scripts/generate_reference_docs.py --all`);
  update any user-facing parquet/data-model page. Update the contract's "Open / remaining" + decision
  log and the memory pointers.

---

## Verification gate (every phase, and final)

`ruff check .` · `ruff format .` · `pyright`/`mypy src/` · `pytest -q` (full suite is the Phase-5
deliverable; per-phase, run the phase's targeted tests + the layer's existing tests). **Kill stray
`litmus mcp serve` + daemons before daemon-spawning tests** (memory: they hang otherwise). `--no-verify`
is BANNED. NO code comments beyond a genuine one-line non-obvious *why*.

## Blast-radius reference (verified 2026-06-30, source-grounded)

- Double-duty stamp: `run_scope.py:774,787,813`; documented `_event_accumulator._step_key:53-65`.
- Scope vector: `_event_accumulator.py:587-686`, `_row_helpers.py:868-928`, `parquet.py:~288-300`,
  snapshot `_event_accumulator.py:~319`.
- Measurement partition: `_event_accumulator.py:483-510` (`by_iteration` / `by_scope`).
- Projection UNNEST (vector-only today, no chain-walk): `_runs_duckdb_daemon.py:1000-1031`.
- Mode-2 mutation + gate: `__init__.py:1147-1154`, `run_scope.py:541-547`.
- Read consumers (no scope-vs-iteration branching): `analysis/steps_query.py`,
  `analysis/measurements_query.py`, `ui/pages/results/detail.py:360-477`.
- Only **S1** asserts scope-vector facts; S2–S6 test in-body vectors.

## Progress log

- **2026-06-30** — Branch cut from `main` (`a823c1fb`). Blast radius re-verified against source.
  Decisions locked (above). Diary written. **Next: Phase 1.** Nothing implemented yet.
