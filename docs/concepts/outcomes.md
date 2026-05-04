# Outcomes — what each value means and where it gets set

Every measurement, vector, step, and run carries an `Outcome` (or
`None` if never judged). This page does two things:

1. **Plain meaning** — what each outcome value tells the operator,
   and what code condition produces it.
2. **Implementation tables** — every site in the source tree that
   sets each outcome, by level, with `file:line` references.

Tables are derived from the code, not from intent.

## Plain meaning — when does each outcome happen?

### `PASSED` — judged good

A verdict ran and was satisfied.
- **Measurement-level**: a value was checked against a limit and was in range (`value in limit` returned True).
- **Step-level**: the test body exited cleanly AND at least one verdict ran (a passing rewritten `assert`, or a measurement with limits — both register "verdict intent").
- **Run-level**: cascaded up from a step that landed `PASSED` and nothing worse happened anywhere else in the run.

### `FAILED` — judged bad

A verdict ran and was violated.
- **Measurement-level**: value was in range failed → `value in limit` returned False.
- **Step-level**: the test body raised `AssertionError` (rewritten or bare). Or any contained measurement landed `FAILED` and cascaded up.
- **Run-level**: cascade rollup from any failing step.

### `DONE` — ran cleanly, no verdict

Code executed without error, but **no judgment was ever declared**.
- **Measurement-level**: a value was recorded but no `low`/`high`/`nominal`/`comparator` was attached. Pure recording, not testing.
- **Step-level**: the test body exited cleanly AND `_STEP_JUDGMENT_INTENT` is empty for this step — i.e. no `assert` ran AND no limit-bearing measurement was recorded. The step ran code, got data, never decided pass-or-fail.
- **Run-level**: rollup from a step that landed `DONE`, with nothing worse anywhere.

This is the "I logged data" outcome, not a "good" outcome.

### `SKIPPED` — explicitly not run

The runner was told to skip before / during execution.
- **Step-level**: `pytest.skip(...)`, `@pytest.mark.skip`, `@pytest.mark.skipif` triggered, or a setup-phase skip exception. The test body never ran (or stopped early via `pytest.skip`).
- **Run-level**: rare — a run with all-skipped steps doesn't usually emit anything meaningful here; cascade still uses the worst severity.
- The vector / client builder also exposes explicit `.skip(message)` calls that stamp this directly.

### `ERRORED` — unhandled exception

Something blew up that wasn't a verdict failure.

The same word lands at different levels for different reasons — and they're not interchangeable:

- **Step-level**: the test body raised any exception that is **not** `AssertionError` and not `pytest.skip.Exception` — a `ValueError`, a `RuntimeError`, a `pyvisa.VisaIOError`, etc. The driver crashed, an instrument timed out, code tripped on a None — and **no** `Measurement` was recorded for the broken call. The step is ERRORED, not the (non-existent) measurement.
- **Measurement-level**: this is much narrower than the step case. A `Measurement` lands as ERRORED **only** when its `value is None`. That happens when:
  - `verify("vout", instr.measure_voltage())` was called and `measure_voltage()` *returned* `None` (broken driver, mock not configured, swallowed timeout). The call did not raise; it returned None silently.
  - A direct `Measurement(value=None)` was constructed and `check_limit()` was called on it. Rare.
  - **Not** from an exception — exceptions never produce a measurement record; they produce a step-level ERRORED with no measurement row.
- **Run-level**: cascade rollup from any ERRORED step or measurement.

### `TERMINATED` — operator stopped the run, cleanup ran

A SIGTERM (or Ctrl-C) reached pytest, the SIGTERM-to-`KeyboardInterrupt` handler converted it, `pytest_keyboard_interrupt` fired, fixture teardowns ran, instruments went to safe state, the parquet was flushed.

The rig **is** in a known state. The run was stopped on purpose, with cleanup. TestStand convention.

### `ABORTED` — process died before cleanup

The parquet subscriber's `close()` was called **without** the subscriber ever seeing a `RunEnded` event. That means `logger.finalize()` never ran — the cleanup chain didn't complete.

- The process was killed mid-flight (SIGKILL, segfault, OOM kill, host shutdown).
- An exception bypassed `_teardown_logger` before `finalize()` could run.

The rig state is **unknown**. An operator seeing `ABORTED` should physically check the bench. This is a strict superset of "we don't know what happened" — it's not produced by the cascade, it's stamped by the parquet subscriber as a fallback when the normal close path didn't fire.

### `None` — never judged, never finalized

The row exists (it was collected, or a step was opened) but no outcome was ever set.

- A pytest test that pytest collected but never ran (e.g. earlier failure aborted the session, or `--exitfirst` cut things short before this test) — the manifest carries it with `outcome IS NULL`.
- A vector that ran but recorded nothing and didn't raise.

Field-missingness IS the receipt: if you see `outcome=None` on a finalized run's row, that row never reached the verdict stage. The display layer translates this to "Never Ran".

## Severity ladder (cascade ordering)

`escalate_outcome(current, incoming)` returns the worse of two outcomes per this rank:

| Severity | Outcome | Source |
|---:|---|---|
| 7 | `ABORTED` | `data/models.py:126` |
| 6 | `TERMINATED` | `data/models.py:127` |
| 5 | `ERRORED` | `data/models.py:128` |
| 4 | `FAILED` | `data/models.py:129` |
| 3 | `PASSED` | `data/models.py:130` |
| 2 | `DONE` | `data/models.py:131` |
| 1 | `SKIPPED` | `data/models.py:132` |
| -1 | `None` | `escalate_outcome` at `data/models.py:167` (no-judgment placeholder) |

Cascade rule: at vector / step / run boundaries, the rollup is `escalate_outcome(rollup, child_outcome)` — worst wins.

## Implementation by level

Each table is ordered worst → least severe (the cascade direction). Each outcome appears once per level; the **Triggering conditions** column lists every code path that produces it.

### Measurement (leaf)

`Measurement.outcome` (default `None`). `Measurement.check_limit()` at `data/models.py:195` is the recorder; `verify._compute_outcome()` at `verify.py:97` is the judgment-only sibling that doesn't mutate.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level)* | — |
| `TERMINATED` | *(never produced at this level)* | — |
| `ERRORED` | • `Measurement(value=None).check_limit()` (caller passed None — typically a driver returned None silently)<br>• `verify("name", None)` — same cause via the judgment-only path | `data/models.py:212`<br>`verify.py:108` |
| `FAILED` | Value set, limit reconstructed, `value in limit` is False | `data/models.py:226`<br>`verify.py:110` |
| `PASSED` | Value set, limit reconstructed, `value in limit` is True | `data/models.py:226`<br>`verify.py:110` |
| `DONE` | • Value set but no `low`/`high`/`nominal` (no limit to judge against)<br>• `LitmusClient.measure()` with no limits and a non-None value | `data/models.py:224`<br>`client.py:123` |
| `SKIPPED` | *(never produced at this level)* | — |
| `None` | Default; row constructed but `check_limit` / `_compute_outcome` not invoked | `data/models.py:182` |

**Important**: an exception in a called function (e.g. driver raises a VISA timeout) does **not** produce an ERRORED measurement — it produces no measurement record at all, and the step lands ERRORED instead.

### Vector

`TestVector.outcome` (default `None`).

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level)* | — |
| `TERMINATED` | *(never produced at this level)* | — |
| `ERRORED` | • Cascade from `m.outcome == ERRORED` via `log_measurement`<br>• `harness.run_vector()` body raises any non-Assertion exception<br>• `LitmusClient` builder: vector measurement landed `ERRORED` and current isn't already `FAILED` | `execution/logger.py:773`<br>`execution/harness.py:1137`<br>`client.py:130` |
| `FAILED` | • Cascade from `m.outcome == FAILED`<br>• `harness.run_vector()` body raises `AssertionError`<br>• `LitmusClient` builder: vector measurement landed `FAILED`<br>• `VectorBuilder.fail(...)` explicit call | `execution/logger.py:773`<br>`execution/harness.py:1128`<br>`client.py:129`<br>`client.py:137` |
| `PASSED` | Cascade from a passing measurement | `execution/logger.py:773` |
| `DONE` | Cascade from a recorded-but-unjudged measurement | `execution/logger.py:773` |
| `SKIPPED` | `VectorBuilder.skip(...)` explicit call | `client.py:143` |
| `None` | Default; vector ran but recorded nothing and didn't raise | `data/models.py:262` |

The harness's no-logger branch performs the same measurement cascade at `execution/harness.py:900`.

### Step

`TestStep.outcome` (default `None`). Cascades both *up* from measurements (logger) and *down* from runner signals (pytest plugin); every plugin-side stamp uses `_escalate_step_and_run` so a worse outcome already on the step is never weakened.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` | *(never produced at this level — only the run-level parquet fallback)* | — |
| `TERMINATED` | `pytest_keyboard_interrupt` fires — operator hit Ctrl-C, or SIGTERM was converted to `KeyboardInterrupt` by the SIGTERM handler | `pytest_plugin/hooks.py:963` |
| `ERRORED` | • Test body raised any non-Assertion, non-`skip` exception<br>• Setup or teardown phase raised any non-`skip` exception<br>• Cascade from a measurement / vector ERRORED<br>• Harness vector cascade where vector ERRORED<br>• `LitmusClient` builder: vector ERRORED and step isn't already FAILED | `pytest_plugin/hooks.py:885`<br>`pytest_plugin/hooks.py:922`<br>`execution/logger.py:774`<br>`execution/harness.py:1244`<br>`client.py:187` |
| `FAILED` | • Test body raised `AssertionError` (rewritten or bare)<br>• Cascade from a measurement / vector FAILED<br>• Harness vector cascade where vector FAILED<br>• `LitmusClient` builder: vector FAILED<br>• `StepBuilder.fail(...)` explicit call<br>• `_finish` reconciles default vector that landed FAILED<br>• Parquet readback fallback when any contained vector has `outcome == FAILED` | `pytest_plugin/hooks.py:883`<br>`execution/logger.py:774`<br>`execution/harness.py:1244`<br>`client.py:185`<br>`client.py:237`<br>`client.py:253`<br>`data/backends/parquet.py:1284` |
| `PASSED` | • Test body exited cleanly AND `_STEP_JUDGMENT_INTENT` contains the step id (passing rewritten `assert`, OR limit-bearing measurement was recorded)<br>• Cascade from a passing measurement<br>• Parquet readback fallback when no failed vectors | `pytest_plugin/hooks.py:874`<br>`execution/logger.py:774`<br>`data/backends/parquet.py:1287` |
| `DONE` | Test body exited cleanly with **no** verdict intent (no asserts ran, no measurements with limits) | `pytest_plugin/hooks.py:876` |
| `SKIPPED` | • Test body raised `pytest.skip.Exception` (`pytest.skip(...)`, `@pytest.mark.skip`, `skipif`)<br>• Setup-phase skip exception (e.g. `skipif` from a fixture)<br>• `StepBuilder.skip(...)` explicit call | `pytest_plugin/hooks.py:881`<br>`pytest_plugin/hooks.py:920`<br>`client.py:243` |
| `None` | Default; step opened but call hook never escalated (e.g. test never ran due to upstream failure) | `data/models.py:301` |

Verdict-intent registration sites (what populates `_STEP_JUDGMENT_INTENT`):
- `pytest_assertion_pass` fires for every passing rewritten `assert` (`pytest_plugin/hooks.py:165-171`).
- `logger.log_measurement` calls `mark_step_judgment_intent` when the measurement carries `limit_low` or `limit_high` (`execution/logger.py:782-788`).

### Run

`TestRun.outcome` (default `None`). Cascade rolls up through every measurement and every step-level escalation; the parquet subscriber writes the final `run_outcome` column.

| Outcome | Triggering conditions | Sources |
|---|---|---|
| `ABORTED` (string) | Parquet subscriber's `close()` fired **without** ever seeing `RunEnded` — process killed mid-flight, `logger.finalize()` never ran. Stamped directly as the string `"aborted"` into the column; not from cascade. | `data/backends/parquet.py:764` |
| `TERMINATED` | Cascade rollup from any step that landed TERMINATED via `pytest_keyboard_interrupt` | `pytest_plugin/hooks.py:836` |
| `ERRORED` | Cascade rollup from any ERRORED step or measurement | `pytest_plugin/hooks.py:836`, `execution/logger.py:775` |
| `FAILED` | Cascade rollup from any FAILED step or measurement | `pytest_plugin/hooks.py:836`, `execution/logger.py:775` |
| `PASSED` | Cascade rollup from PASSED step(s), nothing worse anywhere | `pytest_plugin/hooks.py:836`, `execution/logger.py:775` |
| `DONE` | Cascade rollup from step(s) that ran cleanly without verdict intent | `pytest_plugin/hooks.py:836` |
| `SKIPPED` | Cascade rollup where the only contained outcomes were SKIPPED | `pytest_plugin/hooks.py:836` |
| `None` | Default; emitted via `RunEnded.outcome=None` only when the cascade never escalated. The parquet writer treats this case as `ABORTED` (see top row). | `execution/logger.py:1080` |

Persistence path (`parquet.py:646, 771, 764`):
1. Subscriber receives `RunEnded` → calls `_write(outcome=event.outcome)`. Whatever the cascade produced is written.
2. If `close()` runs first (before any `RunEnded`), the subscriber stamps `"aborted"` as fallback.

#### Slot orchestrator (cross-process)

Per-slot child outcomes are computed by `SlotRunner._monitor_slot` from `subprocess.Popen.returncode`. These are **strings, not the enum** — they aggregate child exit codes, not pytest's per-run cascade.

| `SlotResult.outcome` | Triggering condition | Source |
|---|---|---|
| `"errored"` | `SlotResult` initialized; child never exited (orphan, killed by orchestrator finally) | `slot_runner.py:198` |
| `"failed"` | Child pytest exited non-zero | `slot_runner.py:338` |
| `"passed"` | Child pytest exited with code 0 | `slot_runner.py:338` |

The orchestrator's `SessionEnded.outcome` is the worst of these across slots (`slot_runner.py:614-620`). Each child still writes its own `RunEnded` carrying its cascade-derived outcome from the table above.
