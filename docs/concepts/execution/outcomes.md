# Outcomes

Every measurement, vector, step, and run carries an `Outcome` — one of seven values — or `None` if no verdict was ever rendered. This page explains what each value means, how a worse outcome on a child rolls up to the parent, and where each value gets stamped.

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

- A passing `assert` ran in the test body.
- A measurement with a limit was recorded — the limit is the thing that gets judged.

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
- **Run-level**: rolls up from PASSED steps with nothing worse anywhere.

### `FAILED` — a verdict ran and was violated

- **Measurement-level**: value was checked and `value in limit` returned `False`.
- **Step-level**: the test body raised `AssertionError` (rewritten or bare), OR a contained measurement landed FAILED and cascaded up.
- **Run-level**: rolls up from any FAILED step.

### `DONE` — clean run, no verdict

The "I logged data" outcome. Not a "good" outcome and not a "bad" one — judgment never happened.

- **Measurement-level**: a value was recorded with no `low`/`high`/`nominal` (no limit to check against).
- **Step-level**: the body exited cleanly AND no verdict intent fired.
- **Run-level**: rolls up from DONE steps with nothing worse.

### `SKIPPED` — explicit skip

- **Step-level**: `pytest.skip(...)`, `@pytest.mark.skip`, `@pytest.mark.skipif`, or a setup-phase skip exception. The test body either didn't run or stopped early.
- **Run-level**: cascade rollup where the only contained outcomes were SKIPPED.
- **Vector-level**: `VectorBuilder.skip(...)` on the `LitmusClient` builder path explicitly stamps SKIPPED. Not produced by the runtime cascade.

### `ERRORED` — unhandled exception

Two distinct paths land here, and they're not interchangeable:

- **Step-level**: the test body (or setup / teardown) raised any non-`AssertionError`, non-skip exception. A `ValueError`, `RuntimeError`, `pyvisa.VisaIOError`, etc. **No `Measurement` row is recorded for the broken call** — the step is ERRORED, not the (non-existent) measurement.
- **Measurement-level**: the row exists, with `value=None`. Happens when:
  - `verify("vout", instr.measure_voltage())` was called and `measure_voltage()` *returned* None silently (broken driver, mock not configured, swallowed timeout).

Exceptions do not produce ERRORED measurements — they produce a step-level ERRORED with no measurement row.

- **Run-level**: rolls up from any ERRORED step or measurement.

### `TERMINATED` — operator stopped cleanly

The operator stopped the run (Ctrl-C or SIGTERM). Cleanup ran to completion — fixture teardowns finished, instruments went to a safe state, and results were saved.

The rig **is** in a known state. The run was stopped on purpose, with cleanup. Downstream tooling and operator runbooks can read TERMINATED as "intentional stop; rig safe."

### `ABORTED` — process died before cleanup

The runs daemon was asked to write a run that never saw a `RunEnded` event. The teardown chain didn't complete — the run never finalized.

- The process was killed mid-flight (SIGKILL, segfault, OOM kill, host shutdown).
- An exception bypassed teardown before finalization could run.

The rig state is **unknown**. When a run is killed before it finalizes, Litmus records the unfinished run as ABORTED automatically — so a process that dies mid-run is never silently lost. A non-pytest runner can also mark a run ABORTED through the results API; see the [client reference](../../reference/runtime/client.md).

ABORTED on a parquet row means the run never closed cleanly — downstream tooling and operator runbooks should treat the rig as "physically inspect required."

### `None` — never judged, never finalized

The row exists (it was collected, or a step was opened) but no outcome was ever set.

- A pytest test that pytest collected but never ran — earlier failure aborted the session, or `--exitfirst` cut things short.
- A vector that ran but recorded nothing and didn't raise.

A finalized run whose row has no outcome simply never reached a verdict. The operator UI shows these as "Never Ran" — see [step manifest](step-manifest.md#never-ran).

## Where each outcome gets stamped

The tables below list what triggers each outcome at each level, worst → least severe.

### Measurement level

A measurement has no outcome until its value is checked against a limit.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level)* |
| `TERMINATED` | *(never produced at this level)* |
| `ERRORED` | A `None` value reached the check — typically a driver returned `None` silently (e.g. `verify("vout", None)`) |
| `FAILED` | Value checked against its limit and out of range |
| `PASSED` | Value checked against its limit and in range |
| `DONE` | Value recorded with no limit to judge against — e.g. `measure(...)` with no limit |
| `SKIPPED` | *(never produced at this level)* |
| `None` | Default; row constructed but limit check not invoked |

An exception in a called function (e.g. driver raises a VISA timeout) does **not** produce an ERRORED measurement — it produces no measurement record at all, and the enclosing step lands ERRORED instead.

### Vector level

`TestVector.outcome` defaults to `None`.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level)* |
| `TERMINATED` | *(never produced at this level)* |
| `ERRORED` | Rolls up from an ERRORED measurement; or the vector body raised a non-`AssertionError` exception |
| `FAILED` | Rolls up from a FAILED measurement; or the vector body raised `AssertionError`; or `VectorBuilder.fail(...)` was called (results-API path) |
| `PASSED` | Rolls up from a PASSED measurement |
| `DONE` | Rolls up from a recorded-but-unjudged measurement |
| `SKIPPED` | `VectorBuilder.skip(...)` explicit call (`LitmusClient` builder path only — not produced by the runtime cascade) |
| `None` | Default; vector ran but recorded nothing and didn't raise |

### Step level

A step's outcome rolls up from its measurements and from how the test body ended; once a worse outcome is set, a less-severe later result doesn't weaken it.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | *(never produced at this level — see Run level)* |
| `TERMINATED` | Operator stopped the run (Ctrl-C or SIGTERM) |
| `ERRORED` | Test body raised any non-`AssertionError`, non-skip exception; setup or teardown raised any non-skip exception; or rolls up from an ERRORED measurement or vector |
| `FAILED` | Test body raised `AssertionError`; or rolls up from a FAILED measurement or vector; or `StepBuilder.fail(...)` was called (results-API path) |
| `PASSED` | Test body exited cleanly AND verdict intent was recorded for this step; or rolls up from a PASSED measurement with nothing worse |
| `DONE` | Test body exited cleanly with **no** verdict intent (no asserts ran, no measurements with limits) |
| `SKIPPED` | Test body raised `pytest.skip.Exception` (`pytest.skip(...)`, `@pytest.mark.skip`, `skipif`); setup-phase skip exception (e.g. `skipif` from a fixture); `StepBuilder.skip(...)` explicit call |
| `None` | Default; the step opened but never ran (e.g. an upstream failure stopped the session) |

### Run level

A run's outcome rolls up through every measurement and step; it's written to the run's saved record when the run ends.

| Outcome | Triggering conditions |
|---|---|
| `ABORTED` | The run was killed before it finalized; Litmus records the unfinished run as ABORTED automatically. A non-pytest runner can also mark a run ABORTED via the results API. |
| `TERMINATED` | Rolls up from any step that landed TERMINATED |
| `ERRORED` | Rolls up from any ERRORED step or measurement |
| `FAILED` | Rolls up from any FAILED step or measurement |
| `PASSED` | Rolls up from PASSED step(s) with nothing worse anywhere |
| `DONE` | Rolls up from step(s) that ran cleanly without verdict intent |
| `SKIPPED` | Cascade rollup where the only contained outcomes were SKIPPED |
| `None` | Default; a run that ended without any outcome being set. A run killed before it finalizes is recorded as ABORTED instead (see top row). |

### Multi-UUT site orchestrator

In a multi-UUT run, each site (one DUT) gets its own result; the session's overall result is the worst across sites.

| Site result | Triggering condition |
|---|---|
| `errored` | The site's run never finished cleanly (killed, orphaned) |
| `failed` | The site's run finished with a failure |
| `passed` | The site's run finished clean |

Each DUT still records its own detailed outcome from the per-run tables above. For the operational guide, see [multi-UUT testing](../../how-to/execution/multi-uut-testing.md).

## See also

- [Step hierarchy](step-hierarchy.md) — the measurement / vector / step / run levels the cascade walks
- [Step manifest](step-manifest.md) — how `outcome IS NULL` rows show as "Never Ran" in finalized runs
- [Event log](../data/event-log.md) — `RunEnded` / `StepEnded` events that carry the cascade-derived outcome
- [Multi-UUT testing](../../how-to/execution/multi-uut-testing.md) — site orchestrator outcomes in practice
- [Limits](../../how-to/execution/limits.md) — how a measurement gets a limit attached (the trigger for measurement-level PASSED/FAILED and for step-level verdict intent)
- [Models reference](../../reference/data/models.md) — `Outcome` enum source-of-truth and field tables for `Measurement` / `TestVector` / `TestStep` / `TestRun`
- [Parquet schema](../../reference/data/parquet-schema.md) — column-level definitions of `run_outcome`, `step_outcome`, `measurement_outcome`
