# Outcomes

Every measurement, vector, step, and run carries an `Outcome` (an enum) or `None` if no verdict was ever rendered. This page explains what each value means, how a worse outcome on a child rolls up to the parent, and where each value gets stamped in source.

For the column-by-column shape of how outcomes land in parquet, see [parquet schema → outcome values](../reference/parquet-schema.md). For the level hierarchy (measurement → vector → step → run) the cascade walks, see [step hierarchy](step-hierarchy.md).

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

Defined in `src/litmus/data/models.py:124-131` (`_OUTCOME_SEVERITY`).

### Cascade rule

`escalate_outcome(current, incoming)` (`data/models.py:140-167`) returns the higher-severity of the two. **Ties favor the current outcome** — once a parent reaches FAILED, an incoming FAILED leaves it unchanged. This matters when interpreting timestamps: the first FAILED stamp is the one that survives.

`None` participates with severity `-1`, so any real outcome wins against `None`.

## Verdict intent — what separates PASSED from DONE

The difference between PASSED and DONE on a step that ran cleanly: did the body *try* to judge?

A step has **verdict intent** if either fires during the test:

- A passing rewritten `assert` (registered by `pytest_assertion_pass` in `pytest_plugin/hooks.py:153-172`).
- A measurement whose limits resolved — `logger.measure` registers the intent when `measurement.limit_low` or `measurement.limit_high` is set (`execution/logger.py:861-867`).

The plugin tracks intent per step in a module-level set, `_STEP_JUDGMENT_INTENT` (`hooks.py:88`). At step end, `_stamp_step_from_call_outcome` (`hooks.py:1304-1346`) picks PASSED if the step id is in the set, DONE if not.

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

The runs daemon's materializer (`materialize_run_to_parquet`) was asked to write a run that never saw a `RunEnded` event. `logger.finalize()` never ran — the cleanup chain didn't complete.

- The process was killed mid-flight (SIGKILL, segfault, OOM kill, host shutdown).
- An exception bypassed `_teardown_logger` before `finalize()` could run.

The rig state is **unknown**. ABORTED can be set on a `TestRun` two ways, but only one of them writes the row to parquet:

- The runs daemon's materializer stamps `"aborted"` directly into `run_outcome` when it reaches a run that never closed — the materializer fallback. This is the involuntary path (process died) and is the only path that lands an ABORTED row in parquet automatically.
- `LitmusClient.RunBuilder.abort(message=...)` sets `outcome = Outcome.ABORTED` on the in-memory `TestRun` and returns it. `abort()` does NOT save the run — the caller has to do something with the returned object (e.g. inspect, log, or call `_backend.save_test_run(...)` explicitly).

ABORTED on a parquet row means the run never closed cleanly — downstream tooling and operator runbooks should treat the rig as "physically inspect required."

### `None` — never judged, never finalized

The row exists (it was collected, or a step was opened) but no outcome was ever set.

- A pytest test that pytest collected but never ran — earlier failure aborted the session, or `--exitfirst` cut things short.
- A vector that ran but recorded nothing and didn't raise.

Field-missingness IS the receipt: `outcome=None` on a finalized run's row means that row never reached the verdict stage. The display layer derives "Never Ran" from `outcome IS NULL` plus the run's finalized state — see [step manifest](step-manifest.md#never-ran).

## Where each outcome gets stamped

The implementation tables below name the producer of each outcome at each level, with `file:line` references. Useful for tracing back from a row in parquet to the code path that set it. **If you're an operator reading a report, you can stop reading here — the plain-meaning section above is enough.**

Each table is ordered worst → least severe (the cascade direction). Each outcome appears once per level.

### Measurement level

`Measurement.outcome` (default `None`, `data/models.py:180`). `Measurement.check_limit()` (`data/models.py:193`) is the recorder; `verify._compute_outcome()` (`execution/verify.py:97`) is the judgment-only sibling that returns an Outcome without mutating.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level)* | — |
| `TERMINATED` | *(never produced at this level)* | — |
| `ERRORED` | • `Measurement(value=None).check_limit()` — caller passed `None`, typically a driver returned `None` silently<br>• `verify("name", None)` — same cause via the judgment-only path | `data/models.py:212`<br>`execution/verify.py:109` |
| `FAILED` | Value set, limit reconstructed, `value in limit` is `False` | `data/models.py:226`<br>`execution/verify.py:110` |
| `PASSED` | Value set, limit reconstructed, `value in limit` is `True` | `data/models.py:226`<br>`execution/verify.py:110` |
| `DONE` | • Value set but no `low`/`high`/`nominal` (no limit to judge against)<br>• `LitmusClient.measure()` with no limits and a non-None value | `data/models.py:224`<br>`client.py:123` |
| `SKIPPED` | *(never produced at this level)* | — |
| `None` | Default; row constructed but `check_limit` / `_compute_outcome` not invoked | `data/models.py:180` |

An exception in a called function (e.g. driver raises a VISA timeout) does **not** produce an ERRORED measurement — it produces no measurement record at all, and the enclosing step lands ERRORED instead.

### Vector level

`TestVector.outcome` (default `None`, `data/models.py:262`).

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level)* | — |
| `TERMINATED` | *(never produced at this level)* | — |
| `ERRORED` | • Cascade from `measurement.outcome == ERRORED` via `log_measurement`<br>• `harness.run_vector()` body raises any non-Assertion exception<br>• `LitmusClient` builder: vector measurement landed ERRORED and current isn't already FAILED | `execution/logger.py:852`<br>`execution/harness.py:1140`<br>`client.py:130-131` |
| `FAILED` | • Cascade from `measurement.outcome == FAILED`<br>• `harness.run_vector()` body raises `AssertionError`<br>• `LitmusClient` builder: vector measurement landed FAILED<br>• `VectorBuilder.fail(...)` explicit call | `execution/logger.py:852`<br>`execution/harness.py:1131`<br>`client.py:128-129`<br>`client.py:137` |
| `PASSED` | Cascade from a PASSED measurement | `execution/logger.py:852` |
| `DONE` | Cascade from a recorded-but-unjudged measurement | `execution/logger.py:852` |
| `SKIPPED` | `VectorBuilder.skip(...)` explicit call (`LitmusClient` builder path only — not produced by the runtime cascade) | `client.py:143` |
| `None` | Default; vector ran but recorded nothing and didn't raise | `data/models.py:262` |

### Step level

`TestStep.outcome` (default `None`, `data/models.py:299`). Cascades both *up* from measurements (logger) and *down* from runner signals (pytest plugin). Every plugin-side stamp goes through `_escalate_step_and_run` so a worse outcome already on the step isn't weakened.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level — only the run-level materializer fallback stamps `"aborted"`)* | — |
| `TERMINATED` | `pytest_keyboard_interrupt` fires — operator hit Ctrl-C, or SIGTERM was converted to `KeyboardInterrupt` by the SIGTERM handler | `pytest_plugin/hooks.py:1424` |
| `ERRORED` | • Test body raised any non-Assertion, non-skip exception<br>• Setup or teardown phase raised any non-skip exception<br>• Cascade from a measurement / vector ERRORED<br>• Harness vector cascade where vector ERRORED<br>• `LitmusClient` builder: vector ERRORED and step isn't already FAILED | `pytest_plugin/hooks.py:1346`<br>`pytest_plugin/hooks.py:1383`<br>`execution/logger.py:853`<br>`execution/harness.py:1249`<br>`client.py:186-187` |
| `FAILED` | • Test body raised `AssertionError` (rewritten or bare)<br>• Cascade from a measurement / vector FAILED<br>• Harness vector cascade where vector FAILED<br>• `LitmusClient` builder: vector FAILED<br>• `StepBuilder.fail(...)` explicit call<br>• `_finish` reconciles default vector that landed FAILED<br>• Parquet readback fallback when no `step_outcome` column AND any contained vector has `outcome == FAILED` | `pytest_plugin/hooks.py:1344`<br>`execution/logger.py:853`<br>`execution/harness.py:1249`<br>`client.py:185`<br>`client.py:237`<br>`client.py:253`<br>`data/backends/parquet.py:1018` |
| `PASSED` | • Test body exited cleanly AND `_STEP_JUDGMENT_INTENT` contains the step id<br>• Cascade from a PASSED measurement<br>• Parquet readback fallback when no `step_outcome` column AND no FAILED vectors | `pytest_plugin/hooks.py:1338`<br>`execution/logger.py:853`<br>`data/backends/parquet.py:1020` |
| `DONE` | Test body exited cleanly with **no** verdict intent (no asserts ran, no measurements with limits) | `pytest_plugin/hooks.py:1338` |
| `SKIPPED` | • Test body raised `pytest.skip.Exception` (`pytest.skip(...)`, `@pytest.mark.skip`, `skipif`)<br>• Setup-phase skip exception (e.g. `skipif` from a fixture)<br>• `StepBuilder.skip(...)` explicit call | `pytest_plugin/hooks.py:1342`<br>`pytest_plugin/hooks.py:1381`<br>`client.py:243` |
| `None` | Default; step opened but call hook never escalated (e.g. test never ran due to upstream failure) | `data/models.py:299` |

### Run level

`TestRun.outcome` (default `None`, `data/models.py:439`). Cascade rolls up through every measurement and every step-level escalation; the materializer writes the final `run_outcome` column.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` (string `"aborted"`) | Materializer fallback when `materialize_run_to_parquet` is asked to write a run with `outcome=None` because the daemon never saw a `RunEnded`. Bypasses the cascade. `LitmusClient.RunBuilder.abort()` also stamps `Outcome.ABORTED` on the in-memory `TestRun`, but does not save it — only the materializer-fallback path writes an ABORTED row to parquet automatically. | `data/backends/parquet.py:662`; in-memory only via `client.py:339` |
| `TERMINATED` | Cascade rollup from any step that landed TERMINATED via `pytest_keyboard_interrupt` | `execution/logger.py:854` |
| `ERRORED` | Cascade rollup from any ERRORED step or measurement | `execution/logger.py:854` |
| `FAILED` | Cascade rollup from any FAILED step or measurement | `execution/logger.py:854` |
| `PASSED` | Cascade rollup from PASSED step(s) with nothing worse anywhere | `execution/logger.py:854` |
| `DONE` | Cascade rollup from step(s) that ran cleanly without verdict intent | `execution/logger.py:854` |
| `SKIPPED` | Cascade rollup where the only contained outcomes were SKIPPED | `execution/logger.py:854` |
| `None` | Default; emitted via `RunEnded.outcome=None` only when the cascade never escalated. The materializer treats this case as `"aborted"` (see top row). | `execution/logger.py:1155` |

#### Persistence path

The materializer is a free function in the runs daemon, not an in-runner subscriber. Flow:

1. The runs daemon accumulates events (`SessionStarted`, `RunStarted`, `StepStarted`, `MeasurementRecorded`, etc.) into per-run `EventAccumulator` instances inside `AccumulatorPool`.
2. On `RunEnded`, the daemon calls `materialize_run_to_parquet(acc, runs_dir, outcome=outcome)` (`data/backends/parquet.py:637`, called from `_runs_duckdb_daemon.py:1426`). The cascade-derived outcome rides through `RunEnded.outcome`.
3. If the daemon's `close()` runs before any `RunEnded` for a still-open run, the materializer is invoked with `outcome=None` and stamps `"aborted"` as the fallback (`parquet.py:662`).

The cascade never produces ABORTED. The materializer fallback above is the only path that writes an ABORTED row to parquet automatically. `LitmusClient.RunBuilder.abort()` (`client.py:339`) also stamps `Outcome.ABORTED` on the run object it returns, but does not save it; an external runner that wants to land that row has to call `_backend.save_test_run(...)` on the returned object explicitly.

### Multi-DUT slot orchestrator

Per-slot child outcomes are computed from `subprocess.Popen.returncode` by `SlotRunner._monitor_slot`. These are **strings, not the Outcome enum** — they aggregate process exit codes, not the per-run cascade.

| `SlotResult.outcome` | Triggering condition | Source |
|---|---|---|
| `"errored"` | `SlotResult` initialized; child never exited cleanly (orphan, killed by orchestrator finally) | `execution/slot_runner.py:230` |
| `"failed"` | Child pytest exited non-zero | `execution/slot_runner.py:338` |
| `"passed"` | Child pytest exited with code 0 | `execution/slot_runner.py:338` |

The orchestrator's `SessionEnded.outcome` is the worst of these across slots (`slot_runner.py:622`). Each child still writes its own `RunEnded` carrying its cascade-derived Outcome from the per-run tables above. For the operational guide, see [multi-DUT testing](../how-to/multi-dut-testing.md).

## See also

- [Step hierarchy](step-hierarchy.md) — the measurement / vector / step / run levels the cascade walks
- [Step manifest](step-manifest.md) — how `outcome IS NULL` rows show as "Never Ran" in finalized runs
- [Event log](event-log.md) — `RunEnded` / `StepEnded` events that carry the cascade-derived outcome
- [Multi-DUT testing](../how-to/multi-dut-testing.md) — slot orchestrator outcomes in practice
- [Limits](../how-to/limits.md) — how a measurement gets a limit attached (the trigger for measurement-level PASSED/FAILED and for step-level verdict intent)
- [Models reference](../reference/models.md) — `Outcome` enum source-of-truth and field tables for `Measurement` / `TestVector` / `TestStep` / `TestRun`
- [Parquet schema](../reference/parquet-schema.md) — column-level definitions of `run_outcome`, `step_outcome`, `measurement_outcome`
