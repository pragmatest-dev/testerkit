# Step Hierarchy — runs, containers, steps, vectors, measurements

This page is the single reference for Litmus's run-data hierarchy: what each level represents, how the levels nest, and how they're identified in the event log and the materialized tables. Pair it with [Outcomes](outcomes.md) for what each level's verdict means and where it gets set, and [Step Manifest](step-manifest.md) for the planned-vs-executed reconciliation.

## The hierarchy

```
TestRun                              ← one per pytest session
└── Step                             ← class container (when the class is swept, one per outer iteration)
    └── Step                         ← test method (one per pytest item)
        └── TestVector               ← one per inner iteration (1 for normal swept tests; N for `vectors`-fixture tests)
            └── Measurement          ← one per `logger.measure` / `verify` call
```

Each level emits its own event in the run log. Each level rolls its outcome up to the next level via the severity-max ladder (see [Outcomes](outcomes.md)).

## What each level is

### TestRun

One run = one pytest session. Wraps a session_id, run_id, dut_serial, station, fixture, operator — all of it is the run-level context.

Events: `RunStarted` at session start, `RunEnded` at session end. The session also emits `SessionStarted` / `SessionEnded`, but those are session-scoped (could span multiple runs in a multi-slot harness).

### Step — the unit of "one thing the test did"

A step is a named, ordered unit. Two kinds, but they share one event type and one shape:

1. **Container step (class container).** Synthesized by the pytest plugin when execution enters a test class. Methods inside the class push beneath it on the step stack. When the class is swept (class-level `@pytest.mark.litmus_sweeps`), one container step is emitted **per outer iteration** — each with its own `vector_index` and `inputs={outer_param: value}`.

2. **Method step.** One per pytest-collected item. The test function's body is the step's work.

Container vs method is **structural** — not flagged. A step is a container iff at least one other step in the run references it as `parent_path`. This matches OpenTAP's recursive TestStep model.

Events: `StepStarted` when the step opens, `StepEnded` when it closes. `parent_path` on both events names the enclosing step (empty string for root-level).

Identity: `(step_path, vector_index)` is unique per executed step instance within a run. For a method run as 3 parametrize variants, you get 3 `StepStarted` events with the same `step_path` (e.g., `"TestPower/test_voltage"`) and distinct `vector_index` 0/1/2.

### TestVector — one inner iteration

For normal swept tests (one pytest item per variant), each step has exactly one TestVector. The vector carries the sweep parameter values (`vin=5.0`) and ends up as that step's `inputs`.

For tests using the `vectors` fixture, the test body iterates the matrix itself. Each iteration appends a new TestVector to the step. The step has one outer identity, but N internal vectors and N measurements.

### Measurement — one recorded value

A `logger.measure("vin_voltage", 3.30)` or a `verify(...)` call. Carries the value, units, limit, characteristic_id, and dut_pin / instrument_resource / fixture_connection traceability fields.

Events: `MeasurementRecorded`. Carries the full effective `inputs` dict — outer step params **merged with** the current vector's inner params — so analytics queries can filter on either dimension without joining back to the step.

## Identity fields

| Field | Where it's set | Used for |
|---|---|---|
| `step_path` | Logger derives from `_step_stack` (e.g., `TestPower/test_voltage`) | Hierarchical identity; rolls up via `parent_path` |
| `parent_path` | Same — `step_stack[:-1]` joined | Walk parent→children without JOIN |
| `step_index` | Pre-assigned per logical step at collection time (`assign_indices`) | Sequence-relative ordering within a parent bucket |
| `vector_index` | Pre-assigned at collection time for swept items; 0 for plain steps | Distinguishes sweep variants of the same logical step |
| `step_name` | The function or class name | Display |
| `inputs` (on `StepStarted`) | Outer sweep params from `callspec.params` | Step row's commanded conditions |
| `inputs` (on `MeasurementRecorded`) | Outer step inputs + active vector params | Full per-row sweep context |

`(step_path, vector_index)` is the per-step-instance identity. For container steps it's `(class_name, outer_iteration_index)`; for method steps it's `(class_name/method_name, per-method-counter)`.

## Worked example — swept class with method-level inner sweep

```python
@pytest.mark.litmus_sweeps(voltage=[1, 2, 3])      # class-level → outer
class TestPower:
    def test_warmup(self, voltage):
        logger.measure("vin_warmup", voltage)

    @pytest.mark.litmus_sweeps(current=[4, 5, 6])  # method-level → inner
    def test_load(self, voltage, current):
        logger.measure("vout_load", voltage * 1.1)

    def test_cooldown(self, voltage):
        logger.measure("vin_cooldown", 0)
```

Event stream (condition-first):

```
StepStarted ("TestPower",            vi=0, inputs={voltage:1},              parent_path="")
  StepStarted ("test_warmup",        vi=0, inputs={voltage:1},              parent_path="TestPower")
  MeasurementRecorded("vin_warmup",                inputs={voltage:1})
  StepEnded   ("test_warmup",        vi=0)
  StepStarted ("test_load",          vi=0, inputs={voltage:1, current:4},   parent_path="TestPower")
  MeasurementRecorded("vout_load",                 inputs={voltage:1, current:4})
  StepEnded   ("test_load",          vi=0)
  StepStarted ("test_load",          vi=1, inputs={voltage:1, current:5},   parent_path="TestPower")
  ...
  StepStarted ("test_cooldown",      vi=0, inputs={voltage:1},              parent_path="TestPower")
  ...
StepEnded   ("TestPower",            vi=0, outcome=<rolled-up>)
StepStarted ("TestPower",            vi=1, inputs={voltage:2}, ...)
...
```

3 container iterations × (3 methods, with `test_load` unrolling to 3 inner variants) = 15 method `StepStarted` events under 3 container iterations.

`step_index` for the container is 0 (root-level), for each method is 0/1/2 within the `TestPower` class bucket — so `(step_index=1, vector_index=0)` uniquely points to `test_load[voltage=1, current=4]`.

## `vectors` fixture — one step, many inner vectors

When the method uses the `vectors` fixture, pytest sees ONE item per outer iteration. The step has ONE outer identity (matching the outer-iteration position) and N inner TestVectors from the matrix:

```python
@pytest.mark.litmus_sweeps(voltage=[1, 2, 3])
class TestPower:
    @pytest.mark.litmus_sweeps(current=[4, 5, 6])
    def test_load(self, voltage, vectors, logger):
        for v in vectors:
            logger.measure("vout", voltage * v["current"])
```

Event stream:

```
StepStarted("test_load",          vi=0, inputs={voltage:1})    # outer position
MeasurementRecorded("vout", vi=0, inputs={voltage:1, current:4})
MeasurementRecorded("vout", vi=1, inputs={voltage:1, current:5})
MeasurementRecorded("vout", vi=2, inputs={voltage:1, current:6})
StepEnded  ("test_load",          vi=0, vector_outcome=<aggregate across all inner vectors>)
```

`StepStarted.vector_index` and `StepEnded.vector_index` agree (the outer iteration position). Measurements carry their own `vector_index` (inner counter 0/1/2) plus the full effective `inputs`.

## Outcome rollup chain

```
measurement.outcome
   ↓ escalate (severity-max ladder)
TestVector.outcome  (per inner iteration)
   ↓ escalate
StepEnded.outcome   (the method's aggregate verdict)
   ↓ escalate (via container's `_stamp_container_outcome` walking its iteration's children)
container.outcome   (the class iteration's verdict, on its StepEnded)
   ↓ escalate
TestRun.outcome     (the run's overall verdict, on RunEnded)
```

Severity ladder: `ABORTED > TERMINATED > ERRORED > FAILED > PASSED > DONE > SKIPPED`. Worst wins at every level. Full mapping and producer sites in [Outcomes](outcomes.md).

## Materialized table identity

The runs daemon materializes step events into a `steps_materialized` DuckDB table with primary key `(run_id, step_path, vector_index)`. Container steps and method steps share the table — discriminate by `parent_path`:

- `parent_path = ''` → root-level (run-level test functions or class containers)
- `parent_path = '<class_name>'` → method directly under a class container
- `parent_path = '<class>/<method>'` → would be a nested step (uncommon today; only via `harness.step()` self-loops)

`MAX(severity)` over rows sharing a `step_path` aggregates "did this class ever fail in this run" across its iterations. See the [results storage reference](../reference/parquet-schema.md) for the full column schema.
