# Step Hierarchy — runs, steps, vectors, measurements

This page is the single reference for Litmus's run-data hierarchy: what each level represents, how the levels nest, and how they're identified in the event log and the materialized tables. Pair it with [Outcomes](outcomes.md) for what each level's verdict means and where it gets set, and [Step Manifest](step-manifest.md) for the planned-vs-executed reconciliation.

## The hierarchy

```
TestRun                              ← one per pytest session
└── Step                             ← class container (one per class; one per outer iteration if the class is swept)
    └── Step                         ← test method (one per pytest item)
        └── TestVector               ← one per inner iteration (1 for normal swept tests; N for `vectors`-fixture tests)
            └── Measurement          ← one per `measure` / `verify` call
```

Each level emits its own event in the run log. Each level rolls its outcome up to the next level via the severity-max ladder (see [Outcomes](outcomes.md)). `verify` and `measure` are pytest [fixtures](../../reference/pytest/fixtures.md); `vectors` is the [self-loop fixture](../../how-to/execution/vector-expansion.md).

## What each level is

### TestRun

One run = one pytest session. Wraps a session_id, run_id, uut_serial, station, fixture, operator — all of it is the run-level context.

Events: `RunStarted` at session start, `RunEnded` at session end. The session also emits `SessionStarted` / `SessionEnded`, but those are session-scoped (could span multiple runs in a multi-slot harness).

### Step — the unit of "one thing the test did"

A step is a named, ordered unit. Two kinds, but they share one event type and one shape:

1. **Container step (class container).** Synthesized by the pytest plugin when execution enters a test class. Methods inside the class push beneath it on the step stack. When the class is swept (class-level `@pytest.mark.litmus_sweeps`), one container step is emitted **per outer iteration** — each with its own `vector_index` and `inputs={outer_param: value}`.

2. **Method step.** One per pytest-collected item. The test function's body is the step's work.

Whether a step is a class container or a method isn't a separate flag — it's implied by the nesting: a step is a container when at least one other step names it as its `parent_path`.

Events: `StepStarted` when the step opens, `StepEnded` when it closes. `parent_path` on both events names the enclosing step (empty string for root-level).

Identity: `(step_path, vector_index)` is unique per executed step instance within a run. For a method run as 3 parametrize variants, you get 3 `StepStarted` events with the same `step_path` (e.g., `"TestPower/test_voltage"`) and distinct `vector_index` 0/1/2.

### TestVector — one inner iteration

For normal swept tests (one pytest item per variant), each step has exactly one TestVector. The vector carries the sweep parameter values (`vin=5.0`) and ends up as that step's `inputs`.

For tests using the `vectors` fixture, the test body iterates the matrix itself. Each iteration appends a new TestVector to the step. The step has one outer identity, but N internal vectors and N measurements.

### Measurement — one recorded value

A `measure("vin_voltage", 3.30)` or a `verify(...)` call. Carries the value, units, limit, characteristic_id, and uut_pin / instrument_resource / fixture_connection traceability fields.

Events: `MeasurementRecorded`. Carries the full effective `inputs` dict — outer step params **merged with** the current vector's inner params — so analytics queries can filter on either dimension without joining back to the step.

## Identity fields

| Field | Where it's set | Used for |
|---|---|---|
| `step_path` | Built from the chain of enclosing steps (e.g., `TestPower/test_voltage`) | Hierarchical identity; rolls up via `parent_path` |
| `parent_path` | The enclosing step's path (the parent) | Find a step's children without a database join |
| `step_index` | Assigned to each step before the run, when tests are collected | Sequence-relative ordering within a parent bucket |
| `vector_index` | Pre-assigned at collection time for swept items; 0 for plain steps | Distinguishes sweep variants of the same logical step |
| `step_name` | The function or class name | Display |
| `inputs` (on `StepStarted`) | Outer sweep params from `callspec.params` | Step row's commanded conditions |
| `inputs` (on `MeasurementRecorded`) | Outer step inputs + active vector params | Full per-row sweep context |

`(step_path, vector_index)` is the per-step-instance identity. For container steps it's `(class_name, outer_iteration_index)`; for method steps it's `(class_name/method_name, per-method-counter)`.

## Worked example — swept class with method-level inner sweep

```python
@pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])      # class-level → outer
class TestPower:
    def test_warmup(self, voltage, measure):
        measure("vin_warmup", voltage)

    @pytest.mark.litmus_sweeps([{"current": [4, 5, 6]}])  # method-level → inner
    def test_load(self, voltage, current, measure):
        measure("vout_load", voltage * 1.1)

    def test_cooldown(self, voltage, measure):
        measure("vin_cooldown", 0)
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

## `vectors` fixture — one step, many in-body vectors (Mode 2)

When the method uses the `vectors` fixture, pytest sees ONE item per outer iteration. The step has ONE outer identity (matching the outer-iteration position) and N in-body vector iterations, each bracketed by `VectorStarted` / `VectorEnded` events:

```python
@pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
class TestPower:
    @pytest.mark.litmus_sweeps([{"current": [4, 5, 6]}])
    def test_load(self, voltage, vectors, measure):
        for v in vectors:
            measure("vout", voltage * v["current"])
```

Event stream:

```
StepStarted   ("test_load", vi=0, inputs={voltage:1})         # outer position
VectorStarted ("test_load", vi=0, inputs={voltage:1, current:4})
MeasurementRecorded("vout", inputs={voltage:1, current:4})
VectorEnded   ("test_load", vi=0, outcome=passed)
VectorStarted ("test_load", vi=1, inputs={voltage:1, current:5})
MeasurementRecorded("vout", inputs={voltage:1, current:5})
VectorEnded   ("test_load", vi=1, outcome=passed)
VectorStarted ("test_load", vi=2, inputs={voltage:1, current:6})
MeasurementRecorded("vout", inputs={voltage:1, current:6})
VectorEnded   ("test_load", vi=2, outcome=passed)
StepEnded     ("test_load", vi=0, outcome=<rolled-up>)
```

`StepStarted.vector_index` and `StepEnded.vector_index` agree (the outer iteration position). Each `VectorStarted`/`VectorEnded` pair is the in-body analog of a Mode-1 step boundary — it brackets one iteration's work and carries the full effective `inputs` for that iteration. In the materialized parquet, each `VectorStarted`/`VectorEnded` pair produces one `record_type = 'vector'` row; `record_type = 'step'` rows are NOT emitted for Mode-2 in-body iterations (only for the enclosing step itself).

## Outcome rollup chain

```
measurement.outcome
   ↓ escalate (severity-max ladder)
TestVector.outcome  (per inner iteration)
   ↓ escalate
StepEnded.outcome   (the method's aggregate verdict)
   ↓ escalate (the container takes the worst verdict among its children)
container.outcome   (the class iteration's verdict, on its StepEnded)
   ↓ escalate
TestRun.outcome     (the run's overall verdict, on RunEnded)
```

Severity ladder: `ABORTED > TERMINATED > ERRORED > FAILED > PASSED > DONE > SKIPPED`. Worst wins at every level. Full mapping and producer sites in [Outcomes](outcomes.md).

## Materialized record identity

The at-rest per-run parquet contains three `record_type` values: `run`, `step`, and `vector` (querying also gives you a `measurement` record type, expanded from the measurements inside each vector). Container steps and method steps share the `step` record type — tell them apart by `parent_path`:

- `parent_path = ''` → root-level (run-level test functions or class containers)
- `parent_path = '<class_name>'` → method directly under a class container
- `parent_path = '<class>/<method>'` → nested step (via `harness.step()` self-loops)

`vector` records appear only for Mode-2 in-body iterations (`vectors` fixture). They key on `(step_path, parent_path, vector_index, retry)` and sit below their enclosing `step` row. Mode-1 steps (parametrize / single) have no `vector` rows — the `step` row already carries the vector data.

`MAX(severity)` over `step` rows sharing a `step_path` aggregates "did this class ever fail in this run" across its iterations. See the [results storage reference](../../reference/data/parquet-schema.md) for the full column schema.


## See also

**Related quadrants:**

- [How-to → Execution](../../how-to/execution/index.md) — how-to entry point for this category
- [Reference](../../reference/index.md) — reference entry point for this category
- [Integration](../../integration/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
