# Per-execution step records — design notes (parked)

> **Status:** parked mid-design 2026-05-08. Retry rename / `retry_count` rollup work is in
> the working tree (uncommitted). The per-execution event/row redesign is on paper only.
> Open question on inner-vector counting still on the fence.

## What we're trying to solve

A test execution is whatever produces one observable outcome — vanilla pytest pass/fail,
one vector of a sweep, one retry of a failed vector, one method-of-class run under one
class-level parametrize value. Each should get its own row with its own outcome,
timestamps, and 0..M measurements.

Today this is half-done:

- `step.vectors` accumulates a TestVector per iteration / retry (the in-memory state matches
  the per-execution model).
- `build_step_manifest` iterates `step.vectors` at end-of-run, producing N step rows in the
  parquet — also matching the per-execution model.
- BUT `step()` emits exactly **one** `StepStarted` / `StepEnded` pair around the entire
  test function. So events disagree with parquet rows: 1 event pair vs N rows.
- Streaming accumulator (events → AccumulatorPool → inflight tables) sees only one entry
  per `(step_index, vector_index)`. Retried iterations within a single step collide on the
  key.
- Step rows currently carry `vector_retry = NULL`. The 3 step rows for a vector retried 3
  times look identical at the row level (only measurement rows distinguish them via
  `vector_retry`).
- For measurement-free retried tests, retry information is lost entirely (no measurement
  rows = no `vector_retry` to roll up).

The per-execution events/rows redesign closes the gap by making `run_vector` (not `step()`)
the StepStarted/StepEnded emitter, with each `(step_path, vector_index, vector_retry)`
unique.

## What's locked

These design decisions are settled:

- **One-tier model.** Each observable execution = one StepStarted/StepEnded pair = one
  step row. No separate "outer test function" record above the per-execution rows. Setup
  /teardown timing absorbs into the first/last execution's bounds.
- **`step_path` stays clean.** `{class}/{func}` for class methods; `{func}` for
  module-level. Parametrize values live in `inputs` (`in_p`, `in_q`, …), never in
  `step_path`. Decision predates this work and is deliberate.
- **Class containers stay.** `parent_path` carries the class hierarchy, and the class
  itself is its own step record (`step_path=ClassName`, `parent_path=""`). Container's
  outcome cascades from children. Container `vector_retry=0` always (the container itself
  doesn't retry; the methods inside do).
- **`vector_retry` is 0-based.** Aligns with STDF `MIR.RTST_COD`, pytest `--reruns N`,
  software-test conventions. Internal counters (`TestVector.retry`,
  `MeasurementRecorded.retry`, the `vector_retry` parquet column) all 0-based.
- **`retry_count` is a daemon-derived rollup** on `steps_persisted`, computed as
  `COALESCE(MAX(vector_retry) FILTER (WHERE record_type = 'measurement'), 0)`. Operators
  filter `WHERE retry_count > 0` to find anything that retried.
- **`max_retries` is the user-facing bound** on `RetryConfig` and `TestVector` (0-based,
  default 0 = no retries, ge=0). The retry loop iterates `range(max_retries + 1)`.
- **Industry validation:** OpenHTF, OpenTAP, TestStand, STDF all emit per-execution
  records. Litmus matches this direction. Robot Framework's aggregate-by-default
  `[Template]` is the only outlier and its own community recommends not using templates
  when per-iteration visibility matters.

## What's in flight (uncommitted on the branch)

The **retry rename + retry_count rollup** is fully implemented in the working tree but
not committed. 28 files modified covering:

| Area | What changed |
|---|---|
| Pydantic models / events | `TestVector.attempt` → `retry`, `TestVector.max_attempts` → `max_retries`, `MeasurementRecorded.attempt` → `retry`, `RetryConfig.max_attempts` → `max_retries` (default 0, `ge=0`) |
| Parquet schema | `vector_attempt` column → `vector_retry` |
| Harness | `_attempt: int = 1` → `_retry_index: int = 0`; loop `for retry in range(max_retries + 1)` |
| Pytest plugin retry adapter | `RetryConfig.max_retries` → `flaky(reruns=max_retries)` direct mapping (no -1 offset) |
| Analytics SQL alias | `attempts` → `executions` (internal) and `avg_attempts` → `avg_retries` (output) |
| Daemon | `vector_retry` everywhere; new `retry_count` aggregation column |
| Tests + examples + sidecar YAML | `max_attempts=N` → `max_retries=N-1` (semantic shift), `vector_attempt=0` → `vector_retry=0` |

Targeted tests (875 in subset) passed. Full suite has not been re-run since last edits.
Lint clean for the data/execution/pytest_plugin files (verified). Docs not yet updated
(RELEASE-0.1.0.md, data-schemas.md, data-architecture.md, lakehouse-import.md still
reference the old vocabulary in places).

## What's NOT started — the per-execution events/rows redesign

The bigger architectural change. Code untouched. Plan in
`~/.claude/plans/golden-booping-treasure.md`. Concrete pieces:

- Add `retry: int = 0` to `StepStarted` and `StepEnded` events.
- Move `emit_step_started` / `emit_step_ended` from harness `step()` into `run_vector` so
  each `(vector_index, retry)` iteration emits its own event pair.
- `EventAccumulator._step_starts` / `_step_ends` keys grow to `(step_index, vector_index,
  retry)`.
- `steps_persisted` PK extends to `(run_id, step_path, vector_index, vector_retry)`.
  Auto-migration via existing `ALTER TABLE ADD COLUMN IF NOT EXISTS` pattern adds
  `vector_retry BIGINT` to the table.
- Pytest plugin auto-wraps non-`vectors`-fixture tests in an implicit single-execution
  `run_vector`. The `vectors` fixture iterator emits per-iteration step events for
  self-iterating tests.
- pytest-rerunfailures retries threaded through `request.node.execution_count` →
  stamped onto `vector_retry` so production-mode reruns are visible in the rollup.
- Inflight schemas + tests + docs follow.

## Open question we're tracking — sequence sweeps + inner-vector counting

This is the on-the-fence issue. **Sequence sweeps DEFINITELY require separate-in-time step
executions, each with its own time + vectors captured, interleaved appropriately for
clients.** That part is settled.

What's not settled: **how do we number `vector_index` on inner tests when there's also
outer-level (sequence / class) expansion?**

### The constraint (locked)

A "sequence sweep" — whether implemented today as `@pytest.mark.parametrize` on a class
or in the future as a true Litmus sequence — must produce:

1. **Separate-in-time step records** per outer value. If the sequence runs three times
   (outer values A, B, C), there are three distinct executions per inner test, each with
   their own start/end timestamps.
2. **Vector identity captured** on each execution so consumers can answer "which outer
   value was this?" and "which inner sweep variant was this?"
3. **Properly interleaved emission order**, e.g., for class param `[A, B, C]` and methods
   `test1`, `test2`, `test3`: `test1[A], test2[A], test3[A], test1[B], …` (Litmus's
   class-vec-invariant ordering — confirmed by user). Interleaving is preserved naturally
   by event timestamps; no special ordering logic needed.

### The on-the-fence question

Given a test inside a sequence-swept context, with the test ALSO having its own inner
vectors (either via `@pytest.mark.parametrize` on the method OR via the `vectors` fixture
self-iteration) — what does `vector_index` mean on the resulting executions?

#### Option A — flat composition

`vector_index = outer_collection_idx * len(inner_matrix) + inner_idx`

For class param `[A, B, C]` × inner `[1, 2, 3]`:
- TestX[A]::test_method inner iter 0 → `vector_index = 0`
- TestX[A]::test_method inner iter 1 → `vector_index = 1`
- TestX[A]::test_method inner iter 2 → `vector_index = 2`
- TestX[B]::test_method inner iter 0 → `vector_index = 3`
- TestX[B]::test_method inner iter 1 → `vector_index = 4`
- ... → `vector_index = 5, 6, 7, 8`

9 unique values across all expansion. Same shape as if pytest had fully expanded
everything into separate items. Consumers query the outer/inner axis via
`inputs.in_p` / `inputs.in_q`.

**Pro:** consistent with non-self-iterating case; PK collisions are impossible because
the math gives unique values.

**Con:** `vector_index` loses local meaning ("which inner iteration was this?" requires
modulo arithmetic with `outer_size` knowledge). Operators have to reach for `inputs` to
slice by axis.

#### Option B — local `vector_index`, lifted outer dimension somewhere else

`vector_index` counts ONLY the inner sweep (0..N-1, reset per outer pytest item).
Outer dimension lives only in `inputs` (e.g., `in_p` for class param).

For the same case:
- TestX[A]::test_method iters → `vector_index = 0, 1, 2`
- TestX[B]::test_method iters → `vector_index = 0, 1, 2`
- TestX[C]::test_method iters → `vector_index = 0, 1, 2`

PK on `(run_id, step_path, vector_index, vector_retry)` collides — three sets of (0, 1, 2)
under the same step_path. Need step_path or another column to disambiguate.

**Pro:** `vector_index` keeps local "which inner iteration" meaning. Cross-outer-variant
queries naturally read "the same inner index across different outer values."

**Con:** PK collision — needs either step_path with parametrize bracket (rejected: params
off step_path is deliberate design) or a separate `outer_vector_index` column.

#### Option C — flat composition + an explicit `outer_vector_index` column

Like Option B but with an extra column to capture outer dimension uniquely. PK becomes
`(run_id, step_path, outer_vector_index, vector_index, vector_retry)`.

**Pro:** both axes preserved as columns. Each retains local meaning. Cross-outer queries
clean.

**Con:** schema gets a new column; consumers need to know which dimension lives where;
two-dimensional vector identity is more cognitive load.

### The fence

Option A is what the design notes lean toward — minimal schema impact, consistent with
non-self-iterating expansion. Option C is the most rigorous about preserving axis
locality. Option B is broken on PK uniqueness.

**Likely path forward:** Option A unless an operator use case emerges that genuinely
needs separate axis columns. Most consumers work with `inputs` for axis slicing today,
so flat composition probably suffices.

**To revisit when we pick this back up:** test the actual operator query patterns
against a multi-level swept run and see whether `WHERE inputs.in_p = 'A' AND
vector_index BETWEEN 0 AND 2` (flat-composition pattern) is acceptable, or whether the
ergonomics demand `WHERE outer_vector_index = 0 AND vector_index BETWEEN 0 AND 2`.

## Class container handling — locked design

Class containers stay as their own step records. Hierarchy:

```
TestX                          (parent_path="",      step_path="TestX",            container)
TestX/test_method[A-1]         (parent_path="TestX", step_path="TestX/test_method", first execution)
TestX/test_method[A-2]         (parent_path="TestX", step_path="TestX/test_method", second execution)
...
```

The container row has `vector_index=0, vector_retry=0` (containers don't sweep, don't
retry). Its outcome cascades from children: `MIN(child.outcome by OUTCOME_RANK)` =
worst-of {failed, errored, skipped, passed}. This handles class-level "did everything
inside pass?" without any new mechanism — it's the same cascade we already compute.

Sequence containers (when sequences come back) follow the same pattern: a sequence is
just another level of `parent_path` nesting.

## Where this leaves us

1. **Retry rename work is in flight, uncommitted.** Self-consistent; could commit as a
   stopping point or hold for the bigger redesign.
2. **Per-execution event/row redesign is on paper only.** Plan exists. Locked in shape.
   One open question (Option A vs C for inner-vector counting under outer expansion) —
   not blocking decisions on the rest.
3. **Sequence sweeps are a constraint we must satisfy** — separate-in-time per execution
   is non-negotiable; how `vector_index` numbers them is the fence question.

## When we resume

Read this file first. Then either:

- Commit the in-flight retry rename, then start the per-execution redesign (the plan
  file `~/.claude/plans/golden-booping-treasure.md` has the full implementation
  breakdown).
- Or hold both and pivot to other 0.1.0 work (`RELEASE-0.1.0.md` has Tier 1 items not
  blocked by this).

The fence question (Option A vs C) doesn't have to be answered before starting the
per-execution events work — we can wire Option A first (it's simpler), and only revisit
if real query patterns push toward C.
