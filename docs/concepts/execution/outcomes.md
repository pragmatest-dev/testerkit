# Outcomes

Every measurement, vector, step, and run carries an `Outcome` (an enum) or `None` if no verdict was ever rendered. This page explains what each value means, how a worse outcome on a child rolls up to the parent, and where each value gets stamped.

For the column-by-column shape of how outcomes land in parquet, see [parquet schema → outcome values](../../reference/data/parquet-schema.md). For the level hierarchy (measurement → vector → step → run) the cascade walks, see [step hierarchy](step-hierarchy.md).

## The severity ladder

Outcomes are ordered by severity. When a parent has multiple children, the parent's outcome is the worst child's outcome — and once a parent reaches a given level, a less-severe later child doesn't weaken it.

| Severity | Value | One-liner |
|---:|---|---|
| 7 | `ABORTED` | Process died before cleanup; rig state is unknown. |
| 6 | `TERMINATED` | Operator stopped the run; cleanup ran; rig is safe. |
| 5 | `ERRORED` | Code blew up (not an assertion). |
| 4 | `FAILED` | A verdict ran and was violated. |
| 3 | `PASSED` | A verdict ran and was satisfied. |
| 2 | `DONE` | Code ran cleanly with no verdict — "I logged data". |
| 1 | `SKIPPED` | Explicit skip; the body didn't run. |
| — | `None` | Never judged at all. Treated as severity `-1` by the cascade. |

### Cascade rule

The cascade returns the higher-severity of the two. **Ties favor the current outcome** — once a parent reaches FAILED, an incoming FAILED leaves it unchanged. This matters when interpreting timestamps: the first FAILED stamp is the one that survives.

`None` participates with severity `-1`, so any real outcome wins against `None`.

## Verdict intent — what separates PASSED from DONE

The difference between PASSED and DONE on a step that ran cleanly: did the body *try* to judge?

A step has **verdict intent** if either fires during the test:

- A passing rewritten `assert` — pytest fires a hook for every rewritten assertion that passes, and Litmus records the step id when that hook fires.
- A measurement whose limits resolved — when `measurement.limit_low` or `measurement.limit_high` is set, recording that measurement marks the step as having intent.

At step end, the plugin picks PASSED if the step had any verdict intent, DONE if not.

So:

- Test body exits cleanly **with** verdict intent → **PASSED**.
- Test body exits cleanly **without** verdict intent → **DONE**.
- Test body raises `AssertionError` → **FAILED**.
- Test body raises anything else → **ERRORED**.
- Test body raises `pytest.skip.Exception` → **SKIPPED**.

## What each outcome means

### `PASSED` — a verdict ran and was satisfied

- **Measurement-level**: value was checked against a limit and was in range (`value in limit` returned `True`).
- **Step-level**: the test body exited cleanly AND verdict intent fired at least once.
- **Run-level**: cascade rollup from PASSED steps with nothing worse anywhere.

### `FAILED` — a verdict ran and was violated

- **Measurement-level**: value was checked and `value in limit` returned `False`.
- **Step-level**: the test body raised `AssertionError` (rewritten or bare), OR a contained measurement landed FAILED and cascaded up.
- **Run-level**: cascade rollup from any FAILED step.

### `DONE` — clean run, no verdict

The "I logged data" outcome. Not a "good" outcome and not a "bad" one — judgment never happened.

- **Measurement-level**: a value was recorded with no `low`/`high`/`nominal` (no limit to check against).
- **Step-level**: the body exited cleanly AND no verdict intent fired.
- **Run-level**: cascade rollup from DONE steps with nothing worse.

### `SKIPPED` — explicit skip

- **Step-level**: `pytest.skip(...)`, `@pytest.mark.skip`, `@pytest.mark.skipif`, or a setup-phase skip exception. The test body either didn't run or stopped early.
- **Run-level**: cascade rollup where the only contained outcomes were SKIPPED.
- **Vector-level**: `VectorBuilder.skip(...)` on the `LitmusClient` builder path explicitly stamps SKIPPED. Not produced by the runtime cascade.

### `ERRORED` — unhandled exception

Two distinct paths land here, and they're not interchangeable:

- **Step-level**: the test body (or setup / teardown) raised any non-`AssertionError`, non-skip exception. A `ValueError`, `RuntimeError`, `pyvisa.VisaIOError`, etc. **No `Measurement` row is recorded for the broken call** — the step is ERRORED, not the (non-existent) measurement.
- **Measurement-level**: the row exists, with `value=None`. Happens when:
  - `verify("vout", instr.measure_voltage())` was called and `measure_voltage()` *returned* None silently (broken driver, mock not configured, swallowed timeout).
  - A direct `Measurement(value=None)` was constructed and `check_limit()` was called on it (rare).

Exceptions do not produce ERRORED measurements — they produce a step-level ERRORED with no measurement row.

- **Run-level**: cascade rollup from any ERRORED step or measurement.

### `TERMINATED` — operator stopped cleanly

A SIGTERM or Ctrl-C reached pytest, the SIGTERM-to-`KeyboardInterrupt` handler converted it, `pytest_keyboard_interrupt` fired, fixture teardowns ran, instruments went to safe state, the parquet was flushed.

The rig **is** in a known state. The run was stopped on purpose, with cleanup. Downstream tooling and operator runbooks can read TERMINATED as "intentional stop; rig safe."

### `ABORTED` — process died before cleanup

The runs daemon was asked to write a run that never saw a `RunEnded` event. The teardown chain didn't complete — the run never finalized.

- The process was killed mid-flight (SIGKILL, segfault, OOM kill, host shutdown).
- An exception bypassed teardown before finalization could run.

The rig state is **unknown**. ABORTED can be set on a `TestRun` two ways, but only one of them writes the row to parquet:

- The runs daemon stamps `"aborted"` directly into `run_outcome` when it reaches a run that never closed — the materializer fallback. This is the involuntary path (process died) and is the only path that lands an ABORTED row in parquet automatically.
- `LitmusClient.RunBuilder.abort(message=...)` sets `outcome = Outcome.ABORTED` on the in-memory `TestRun` and returns it. `abort()` does NOT save the run — the caller has to do something with the returned object (e.g. inspect, log, or persist it explicitly).

ABORTED on a parquet row means the run never closed cleanly — downstream tooling and operator runbooks should treat the rig as "physically inspect required."

### `None` — never judged, never finalized

The row exists (it was collected, or a step was opened) but no outcome was ever set.

- A pytest test that pytest collected but never ran — earlier failure aborted the session, or `--exitfirst` cut things short.
- A vector that ran but recorded nothing and didn't raise.

Field-missingness IS the receipt: `outcome=None` on a finalized run's row means that row never reached the verdict stage. The display layer derives "Never Ran" from `outcome IS NULL` plus the run's finalized state — see [step manifest](step-manifest.md#never-ran).

## Where each outcome gets stamped

The tables below list what triggers each outcome at each level, ordered worst → least severe (the cascade direction). **If you're an operator reading a report, the plain-meaning section above is enough.**

### Measurement level

`Measurement.outcome` defaults to `None`. `Measurement.check_limit()` is the recorder; the `verify` function is the judgment-only sibling that returns an `Outcome` without mutating.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level)* |
| `TERMINATED` | *(never produced at this level)* |
| `ERRORED` | `Measurement(value=None).check_limit()` — caller passed `None`, typically a driver returned `None` silently; `verify("name", None)` — same cause via the judgment path |
| `FAILED` | Value set, limit reconstructed, `value in limit` is `False` |
| `PASSED` | Value set, limit reconstructed, `value in limit` is `True` |
| `DONE` | Value set but no `low`/`high`/`nominal` (no limit to judge against); `LitmusClient.measure()` with no limits and a non-None value |
| `SKIPPED` | *(never produced at this level)* |
| `None` | Default; row constructed but limit check not invoked |

An exception in a called function (e.g. driver raises a VISA timeout) does **not** produce an ERRORED measurement — it produces no measurement record at all, and the enclosing step lands ERRORED instead.

### Vector level

`TestVector.outcome` defaults to `None`.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level)* |
| `TERMINATED` | *(never produced at this level)* |
| `ERRORED` | Cascade up from a measurement that landed ERRORED; vector body raises any non-`AssertionError` exception; `LitmusClient` builder: vector measurement landed ERRORED and current isn't already FAILED |
| `FAILED` | Cascade up from a measurement that landed FAILED; vector body raises `AssertionError`; `LitmusClient` builder: vector measurement landed FAILED; `VectorBuilder.fail(...)` explicit call |
| `PASSED` | Cascade up from a PASSED measurement |
| `DONE` | Cascade up from a recorded-but-unjudged measurement |
| `SKIPPED` | `VectorBuilder.skip(...)` explicit call (`LitmusClient` builder path only — not produced by the runtime cascade) |
| `None` | Default; vector ran but recorded nothing and didn't raise |

### Step level

`TestStep.outcome` defaults to `None`. Cascades both up from measurements and from runner signals (pytest plugin). Every plugin-side stamp goes through the cascade so a worse outcome already on the step isn't weakened.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level — only the run-level materializer fallback stamps `"aborted"`)* |
| `TERMINATED` | Operator hit Ctrl-C, or SIGTERM was converted to `KeyboardInterrupt` by the SIGTERM handler |
| `ERRORED` | Test body raised any non-`AssertionError`, non-skip exception; setup or teardown phase raised any non-skip exception; cascade up from a measurement or vector that landed ERRORED; `LitmusClient` builder: vector ERRORED and step isn't already FAILED |
| `FAILED` | Test body raised `AssertionError` (rewritten or bare); cascade up from a measurement or vector that landed FAILED; `LitmusClient` builder: vector FAILED; `StepBuilder.fail(...)` explicit call; a contained vector landed FAILED during finalization; parquet readback fallback when any contained vector has `outcome == FAILED` |
| `PASSED` | Test body exited cleanly AND verdict intent was recorded for this step; cascade up from a PASSED measurement; parquet readback fallback when no FAILED vectors |
| `DONE` | Test body exited cleanly with **no** verdict intent (no asserts ran, no measurements with limits) |
| `SKIPPED` | Test body raised `pytest.skip.Exception` (`pytest.skip(...)`, `@pytest.mark.skip`, `skipif`); setup-phase skip exception (e.g. `skipif` from a fixture); `StepBuilder.skip(...)` explicit call |
| `None` | Default; step opened but the call hook never escalated (e.g. test never ran due to upstream failure) |

### Run level

`TestRun.outcome` defaults to `None`. The cascade rolls up through every measurement and every step-level escalation; the runs daemon writes the final `run_outcome` column when the run is materialized.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` (string `"aborted"`) | Materializer fallback when the runs daemon is asked to write a run with `outcome=None` because it never saw a `RunEnded`. Bypasses the cascade. `LitmusClient.RunBuilder.abort()` also stamps `Outcome.ABORTED` on the in-memory `TestRun`, but does not save it — only the materializer-fallback path writes an ABORTED row to parquet automatically. |
| `TERMINATED` | Cascade rollup from any step that landed TERMINATED |
| `ERRORED` | Cascade rollup from any ERRORED step or measurement |
| `FAILED` | Cascade rollup from any FAILED step or measurement |
| `PASSED` | Cascade rollup from PASSED step(s) with nothing worse anywhere |
| `DONE` | Cascade rollup from step(s) that ran cleanly without verdict intent |
| `SKIPPED` | Cascade rollup where the only contained outcomes were SKIPPED |
| `None` | Default; emitted via `RunEnded` only when the cascade never escalated. The materializer treats this case as `"aborted"` (see top row). |

#### Persistence path

The runs daemon accumulates events (`SessionStarted`, `RunStarted`, `StepStarted`, `MeasurementRecorded`, etc.) into per-run accumulators. On `RunEnded`, the daemon materializes the run to parquet; the cascade-derived outcome rides in through `RunEnded`. If the daemon's close runs before a `RunEnded` for a still-open run, the materializer is invoked with no outcome and stamps `"aborted"` as the fallback.

The cascade never produces ABORTED. The materializer fallback above is the only path that writes an ABORTED row to parquet automatically. `LitmusClient.RunBuilder.abort()` stamps `Outcome.ABORTED` on the run object it returns, but does not save it; an external runner that wants to land that row has to persist the returned object explicitly.

### Multi-UUT slot orchestrator

Per-slot child outcomes are derived from subprocess exit codes. These are **strings, not the Outcome enum** — they aggregate process exit codes, not the per-run cascade.

| `SlotResult.outcome` | Triggering condition |
|---|---|
| `"errored"` | `SlotResult` initialized; child never exited cleanly (orphan, killed by orchestrator finally) |
| `"failed"` | Child pytest exited non-zero |
| `"passed"` | Child pytest exited with code 0 |

The session-level result is the worst of these across slots. Each child still writes its own `RunEnded` carrying its cascade-derived `Outcome` from the per-run tables above. For the operational guide, see [multi-UUT testing](../../how-to/execution/multi-uut-testing.md).

## See also

- [Step hierarchy](step-hierarchy.md) — the measurement / vector / step / run levels the cascade walks
- [Step manifest](step-manifest.md) — how `outcome IS NULL` rows show as "Never Ran" in finalized runs
- [Event log](../data/event-log.md) — `RunEnded` / `StepEnded` events that carry the cascade-derived outcome
- [Multi-UUT testing](../../how-to/execution/multi-uut-testing.md) — slot orchestrator outcomes in practice
- [Limits](../../how-to/execution/limits.md) — how a measurement gets a limit attached (the trigger for measurement-level PASSED/FAILED and for step-level verdict intent)
- [Models reference](../../reference/data/models.md) — `Outcome` enum source-of-truth and field tables for `Measurement` / `TestVector` / `TestStep` / `TestRun`
- [Parquet schema](../../reference/data/parquet-schema.md) — column-level definitions of `run_outcome`, `step_outcome`, `measurement_outcome`
