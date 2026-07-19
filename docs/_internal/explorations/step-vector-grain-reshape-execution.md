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
| class-outer `testerkit_sweeps` (hooks.py:1269) | container `C.vector_index = vi` | `C` emits a vector per outer point (`vector_index = vi`); methods get `step.vector_index = vi` |
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
  variants: emit in **`pytest_runtest_call`** around the `yield`, after `start_step` — NOT in
  `_testerkit_push_params` (fixtures run in the *setup* phase, before `start_step` fires in the *call*
  hookwrapper, so emitting there would put `VectorStarted` *before* `StepStarted` — wrong nesting).
  Class-outer: emit at the container open/close (hooks.py:1253-1276) per outer point, via a **separate
  `_outer_vector_tokens` stack** (a class-outer vector must survive multiple nested method `end_step`s;
  pushing it onto `_vector_tokens` would let the first method's `end_step` pop it).
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
- **Step-scope `observe()` / measurements (resolution, 2026-06-30 autonomous run):** the old
  observe→active-vector *mirror* assumed a vector always exists; a non-swept step now has none.
  Resolution (contract-consistent with steps-carry-own-data): **a measurement/observation mirrors to
  the active vector when one is active, and to the step's own outputs/measurements when not.** The
  producer already lands step-scope observations on `StepEnded.outputs` (Phase 1); Phase 2's
  accumulator must consume `StepEnded.outputs`/`inputs` and the no-active-vector measurements onto the
  **step row**, and the `_active_*`/observe-mirror unit tests update to the "no vector → step" path.
- **`null`-vs-`0` reconstruction (handed up from Phase 1):** the producer can only stamp an `int` on
  `StepStarted/Ended.vector_index`, so it emits **`0`** for a step not nested under a loop (same wire
  value as a genuine enclosing iteration `0`). Phase 2 maps the step row's `vector_index` to **`NULL`
  unless the step's parent in `step_path` actually emitted vectors** (top-level, or a non-swept parent
  → `NULL`; parent that looped → keep the index). All events are in hand at build time, so "did the
  parent loop" is decidable. This is what makes the `vector_index IS NULL` step-scope predicate honest.
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
`testerkit mcp serve` + daemons before daemon-spawning tests** (memory: they hang otherwise). `--no-verify`
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
  Decisions locked (above). Diary written.
- **2026-06-30** — **Phase 1 implemented (Sonnet) + gate-reviewed (Opus).** Producer rework:
  identity-vector removed; `step.vector_index` = enclosing (via a `_step_enclosing` vector stack);
  sweep points emit `VectorStarted/Ended` (Mode-1 in `pytest_runtest_call`, class-outer at container
  open/close on a separate `_outer_vector_tokens` stack, Mode-2 unchanged). Gate-review fixes:
  **(1) pre-merge gap on step rows** — `StepEnded.inputs=configured_params` dropped the inherited
  enclosing condition (`configured_params` excludes parametrize seeds, harness.py:897); fixed by
  merging the enclosing vector's params (captured at `start_step`) onto both `StepStarted` and
  `StepEnded` inputs — correctly `None` for a top-level Mode-1 item so it doesn't re-stamp the step's
  own `v`. (2) Removed the now-dead `start_step(vector_index=, inputs=)` params. 5 event-level tests
  green (`test_phase1_grain_reshape.py`); ruff + pyright clean. **17 suite tests red — expected**
  (accumulator still on the old event shape; Phase 2 repairs). Not committed (red suite can't pass the
  full-pytest gate without the banned `--no-verify`; commit at the next green checkpoint).
- **2026-06-30** — **Phase 1 test alignment (Opus).** 11 of the 17 reds are pure Phase-1 behavior and
  were updated + greened: `test_observe_mirrors_to_vector` + `test_top_level_verbs` (9 — the observe→
  vector mirror now needs an explicit vector scope since a non-swept test no longer auto-creates one;
  added a `_vector_scope` fixture), `test_logger::test_start_step_sets_contextvars` (no auto-vector),
  `test_step_end_owning_context` (owning-context merge now lands on `StepEnded.inputs`, asserted via
  the captured event). The other **6 are Phase-2 behavior** (steps-carry-own-measurements + the
  swept-class reshape): `test_logger::test_log_measurement_no_double_append` + 5 in
  `test_class_step_containers`. The gate runs the FULL suite (only benchmark/`test_e2e` excluded — the
  17/17 match with the excluded count was coincidence), so the commit lands as **Phase 1+2 green
  together**. **Next: Phase 2 (in progress) — accumulator + log_measurement→step + the 6 tests.**
- **2026-06-30** — **Phase 2 implemented (Sonnet) + gate-reviewed (Opus).** Scope-vector machinery
  deleted; `TestStep` gained in-memory `measurements`/`inputs`/`outputs`; `log_measurement` attaches to
  the step when the active vector isn't its own (`is_own_vector = active_vector in step.vectors`);
  accumulator builds step rows carrying own data + null-vs-0 reconstruction (`_parent_emitted_vectors`
  via `step_path.rsplit`). **Cross-phase blocker hit + resolved (option 1, contract-consistent — NULL
  is the chosen design; sentinel rejected):** the daemon's `steps_materialized.vector_index BIGINT NOT
  NULL` (in the PK) quarantined NULL step rows — every non-swept run. Pulled the *minimal* daemon
  constraint fix into Phase 2: `vector_index` nullable + `vector_index_key = COALESCE(vector_index,-1)`
  carries the PK/ON-CONFLICT role (NULL→-1 keeps a step-own row distinct from a `vector_index=0` vector
  row, dedups repeated NULLs); `steps` view `EXCLUDE`s it; `_STEPS_PERSISTED_COLUMNS` gains
  `vector_index`. Also: step-input projection — the daemon aggregated `dynamic_attrs`/`measurement_count`
  with `FILTER (record_type='vector')`, which missed a non-swept step's own inputs (on the 'step'
  record); changed to `FILTER (record_type <> 'measurement')` (each `(step_path, vector_index)` group is
  step- OR vector-origin, never both). The full Phase 3 daemon projection (source-agnostic measurement
  UNNEST, vector-row outcome/timing, null-safe EAV join) stays deferred. The reshape broke ~25 tests
  across `test_data` (materializer/vector-grained/retry-model S1–S6/instruments/measurement-attribution)
  + `verify_cascade`; **S1–S6 pulled forward from Phase 5** since they block the green commit. Observe
  regression fixed (in-body vector push — a setup-phase fixture vector is clobbered by the plugin's
  call-phase `start_step` auto-close).
- **2026-06-30** — **Phase 2 greening complete (2329 passed) + Phase 3 pulled forward.** One test
  remained — `test_inflight_overlay_matches_materialized_for_same_events` — and it caught a **real data
  loss**, not a stale expectation: after deleting scope vectors, a plain `def test(): measure(...)`
  step-scope measurement reaches the in-flight overlay but **vanishes at finalize**, because the daemon
  `_measurement_unnest_insert` still sources `record_type='vector'` only. P2 is therefore NOT commitable
  alone (scope-vector deletion and the step-row measurement UNNEST are atomic). **Revised commit plan:
  the at-rest reshape lands as one green checkpoint = P1 + P2 + the P3 measurement projection** (the
  diary's phase split under-anticipated this coupling). Phase 3 (in progress): two-branch UNNEST
  (vector→own index, step→NULL), `num_measurements` count fix, overlay step-scope NULL parity, null-safe
  EAV join (`measurements_query._VECTOR_KEY` `=`→`IS NOT DISTINCT FROM`), exclude internal
  `vector_index_key` from drift parity, rewrite the drift test's event stream to the new shape. **Next:
  review Phase 3 → full suite → comment-scrub → commit the reshape.**
- **2026-06-30** — **At-rest reshape COMMITTED** as `327da234` (P1 + P2 + P3 measurement projection),
  24 files, full suite green (2331 passed) through the gate. Phase 3 gate-reviewed (two-branch UNNEST
  rendered as one SELECT with a `CASE`; two extra overlay-parity changes — `snapshot_step_rows` emits
  vector-grain rows, step-row `measurement_count` is step-scope-only — sound). Comment-scrub pass done
  (agents' what-narration removed per the no-comments hard rule); dead `_snapshot_active_vector_params`
  + its import deleted. The phase split proved artificial: scope-vector deletion (P2) and the step-row
  measurement UNNEST (P3) are atomic (else step-scope measurements vanish at finalize), so they landed
  together. **Remaining: Phase 4 (Mode-2 `_VectorIterator` child-context hygiene; also drop the dead
  `_step_ran_inbody_loop` if still present) and Phase 5 (row A–J permutation tests, docs — mark
  runs-execution-model v2 decisions reversed, update runs-architecture-map).**
- **2026-06-30** — **Phase 4 done + gate-reviewed.** `_VectorIterator` no longer mutates the shared
  step context; each iteration pushes a `self._ctx.child()` (params = the vector's, `_prev` chained for
  `changed()`/`.last()`), made the active context with try/finally reset symmetry in both branches, so a
  step-scope `configure()` before the loop survives on the base and per-iteration keys don't bleed. Dead
  `_step_ran_inbody_loop` deleted. Regression test asserts (a) setup key visible each iteration via the
  parent chain, (b) no cross-iteration bleed, (c) base unpolluted after the loop; two tests that asserted
  the old mutate-shared-context behavior updated to read the active child context. Full suite green (2332
  passed). **Next: Phase 5 (permutation tests + docs).**
- **2026-06-30** — **Phase 5 done → RESHAPE COMPLETE.** Internal docs corrected against source:
  `runs-architecture-map.md` grain section rewritten (step = code + default carrier; vector = optional
  condition point; measurement source-agnostic; daemon `vector_index_key`); the three known band-aids
  marked resolved/deleted. `runs-execution-model.md` decisions 1 (uniform vectors) + 4 (scope vector)
  struck + REVERSED, Open A + #2/#3 marked RESOLVED. Permutation rows A–J mapped; added C (in-body
  setup/teardown + N vectors), G (nested method in non-swept class → `vector_index` NULL), J
  (`@parametrize` in plain class → method vectors, step `vector_index` NULL). Fixed the stale
  `_build_vector_results_from_events` docstring ("Mode-2 only" → all sweep sources). Reference docs: no
  drift. Full suite **2335 passed**. All five phases committed on `feat/0.3.0-grain-reshape`
  (`327da234` P1–P3, `029b1e64` P4, this commit P5). **Branch ready for review/merge.** Deferred to the
  reusable-subsequence future: surrogate execution-id for positional parent-rerun disambiguation.

## Follow-up fix: `vector_outer_index` (nested-vector identity + correct retry) — LOCKED 2026-06-30

**Bug (verified in parquet):** a clean N×M nested sweep (swept class × in-body method loop) mislabels
`vector_retry` as the outer-iteration ordinal (e.g. 3×3 → `vector_retry` 0/1/2 with zero real retries),
and the nested vectors aren't uniquely identifiable because the outer coordinate isn't carried.

**Root cause:** the occurrence counter keys on `(step_path, vector_index)`, which omits the outer
(class) vector a nested step runs under. `vector_index` alone can't distinguish the same inner point
across outer iterations.

**Fix — one new coordinate, `vector_outer_index`:**
- Meaning: *the `vector_index` of the outer vector this record's step runs inside; NULL at top level.*
  (Named `vector_outer_index`, not `parent_*`/`enclosing_*`: "parent" resolves to a step and isn't a
  grain; "outer" is the codebase's own word for the outer sweep. It names a tree edge, so it carries a
  column definition.)
- **Coordinate model (at rest, run/step/vector rows):** `vector_index` is **vector-rows-only** (own
  position; NULL on run/step). `vector_outer_index` is on the **step** (which outer vector it runs
  under; NULL top-level) and propagated onto that step's **vector** rows. `step.vector_index` ≡ NULL —
  delete `_parent_emitted_vectors` / the null-vs-0 hack.
- **Keys:** step = `(step_path, step_retry, vector_outer_index)`; vector =
  `(step_path, vector_outer_index, vector_index, vector_retry)`. Retry counts on
  `(step_path, vector_outer_index, vector_index)` → same-exact-thing-twice increments; a clean sweep is
  all `0`. `parent_path` stays derived (`rsplit`), `/` reserved.
- **Producer-stamped (decided): events gain `vector_outer_index`** — `StepStarted/Ended`,
  `VectorStarted/Ended`, `MeasurementRecorded`, stamped from the active outer vector at emit
  (`RunScope._step_enclosing` / `get_current_vector`). Single authoritative source (the alternative —
  accumulator deriving by outer-vector ordering — is the fragile path that already misfired). This DOES
  change the event shape; forced, because with `step.vector_index` NULL a swept method's executions
  otherwise share `(step_path, step_retry)`.
- **Projections:** `vector_outer_index` column on `steps_materialized` + measurement/input/output
  projections; carried in the UNNEST; `_VECTOR_KEY` join (`measurements_query.py`) includes it NULL-safe
  (`IS NOT DISTINCT FROM`) → 1:1.
- **Depth ≥3 is impossible today** (pytest caps at class-outer × method-inner); when reusable
  subsequences bring it, `vector_outer_index` becomes a parent *execution* id (joins-or-coordinates) —
  no cost now.

**Verify (clean-room, real query — NOT a synthetic probe):** wipe `data/`; run the 3×3 probe; query the
measurement projection → exactly 9 `vout` rows, each a distinct `(voltage,current)`, **all `retry=0`**;
filter `voltage=2 AND current=5` → exactly one row (1:1 join); a rerun-one-point variant → that point
`retry=1`, others 0. Read integer conditions from `value_int`. S1–S6 + full `pytest -q` green.
