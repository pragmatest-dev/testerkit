# Exploration: Sequences as Files

> **Status:** design exploration, not a plan.
> Written as an alternative framing to evaluate against the current
> YAML-driven `ResolvedStep` plan-tree model on `feat/sequence-hierarchy`.

## Premise

A sequence is a Python **class** in its own file. The filesystem is the catalog.
A run is a list of sequence files. There is no nesting — if you want a
composed sequence, you write a new sequence class that composes. Vectors come
from a YAML sidecar next to the file, and `pytest_generate_tests` feeds them
into standard pytest parametrize.

Convention: **one class per file, class name derived from filename**.
Enforce with a lightweight collection-time check — error if a sequence file
has zero or more than one `LitmusSequence` subclass.

## Directory shape

```
sequences/
  conftest.py                    # shared fixtures (smu, chamber, dut, station)
  test_power_on.py               # class TestPowerOn(LitmusSequence): ...
  test_power_on.vectors.yaml     # outer vectors for the file
  test_stress.py                 # class TestStress(LitmusSequence): ...
  test_stress.vectors.yaml
```

The `test_` prefix stays for IDE/pytest runnability. Can be relaxed to `seq_`
with a minor `pytest_collect_file` override.

## Example sequence file

```python
"""Power-on sequence."""
from litmus import LitmusSequence, context
import pytest


class TestPowerOn(LitmusSequence):
    """Exercise power rails, clock lock, and boot across outer envelopes."""

    # Static, greppable surface — what this sequence needs and produces
    context_inputs = {"temp": float, "voltage_class": str}
    context_produces = {"boot_time_ms": float}
    required_fixtures = ("dut", "chamber")

    # OUTER vectors arrive via `outer_vec` fixture (class-scoped)
    # INNER vectors arrive via `inner_vec` fixture (method-scoped)

    def test_voltage_rails(self, outer_vec, dut, litmus_step):
        litmus_step.measure("v_3v3", dut.read_3v3(), limits=(3.2, 3.4))

    def test_clock_lock(self, outer_vec, dut, litmus_step):
        assert dut.pll_locked()

    @pytest.mark.parametrize("inner_vec", [
        {"freq": 1e6}, {"freq": 10e6}, {"freq": 100e6},
    ], indirect=True)
    def test_clock_stability(self, outer_vec, inner_vec, dut, litmus_step):
        litmus_step.measure("jitter_ps", dut.measure_jitter(inner_vec["freq"]))

    def test_boot(self, outer_vec, dut, litmus_step):
        t = dut.boot()
        context.set("boot_time_ms", t.ms)
```

## Vector YAML sidecar

Three authoring modes, pick per file:

**List form (explicit):**
```yaml
sequence: test_power_on
vectors:
  - {temp: 25, voltage_class: low}
  - {temp: 55, voltage_class: low}
  - {temp: 85, voltage_class: high}
```

**Product form (cartesian):**
```yaml
sequence: test_power_on
vectors:
  product:
    temp: [25, 55, 85]
    voltage_class: [low, high]
# 6 outer iterations
```

**Zip form (lock-step):**
```yaml
sequence: test_power_on
vectors:
  zip:
    temp: [25, 55, 85]
    voltage_class: [low, low, high]
# 3 outer iterations
```

Missing sidecar → class runs once with no outer vector.

## How the plugin wires it

Three small hooks, one fixture. Total < 80 lines.

### 1. `pytest_generate_tests` — load outer vectors from sidecar

```python
def pytest_generate_tests(metafunc):
    if not (metafunc.cls and issubclass(metafunc.cls, LitmusSequence)):
        return
    yaml_path = Path(metafunc.module.__file__).with_suffix(".vectors.yaml")
    if not yaml_path.exists():
        return
    cfg = load_vectors_yaml(yaml_path)
    vectors = [Vector(v, index=i) for i, v in enumerate(expand(cfg))]
    if "outer_vec" in metafunc.fixturenames:
        metafunc.parametrize("outer_vec", vectors,
                             scope="class", indirect=True,
                             ids=[v.short_id() for v in vectors])
```

`scope="class"` is the critical piece — pytest groups test execution so all
methods run with `outer_vec[0]` before advancing to `outer_vec[1]`. That's
"rerun the whole sequence per outer vector."

### 2. Indirect fixtures wrap values in `Vector`

```python
@pytest.fixture
def outer_vec(request):
    return request.param  # already a Vector object from generate_tests

@pytest.fixture
def inner_vec(request):
    raw = request.param
    return raw if isinstance(raw, Vector) else Vector(raw, index=0)

@pytest.fixture(autouse=True)
def _litmus_outer_context(outer_vec):
    """Expose outer_vec via context.get() for the duration of each test."""
    token = context.push(outer_vec)
    yield
    context.pop(token)
```

### 3. Implicit prereq chain (optional, per Future Thread #5)

Autouse fixture that reads the prior test's outcome and skips descendants on
failure — unless `@pytest.mark.independent` overrides it.

```python
@pytest.fixture(autouse=True)
def _litmus_prereq_chain(request):
    if request.node.get_closest_marker("independent"):
        return
    cls_state = request.cls._litmus_state
    if cls_state.get("prior_failed"):
        pytest.skip("prior test in sequence failed")
    yield
    # record outcome for next test
```

Opt-out via `pytest.mark.independent` on the method.

### 4. `context.declare(...)` / class attributes — static validation

At collection time, compare `context_inputs` against vector YAML keys. Error
on typo (`temperature` in YAML vs `temp` declared on class).

## How each requirement maps

| Requirement | Mechanism |
|---|---|
| Ordered execution | Source order in class body (pytest default) |
| Implicit prereq chain | Autouse fixture reading prior outcome; `@mark.independent` opts out |
| Outer vectors, whole-class repeats | `pytest_generate_tests` → class-scoped parametrize |
| Inner vectors, per-method sweep | Method-level `@pytest.mark.parametrize` or generate_tests |
| Config from outside the code | YAML sidecar read at collection |
| Static context surface | `context_inputs` class attribute — greppable, lintable |
| Shared state across steps | Module/session fixtures in `conftest.py` |
| Portable artifact | `.py` + `.vectors.yaml` as a pair |
| Per-call-site identity | Class parametrize values = distinct call sites (free) |
| Composition without nesting | Root run = list of class files; compose by writing a new class |
| Sequence reuse | `class TestExtendedPowerOn(TestPowerOn)` — real inheritance |
| Retry | `pytest-rerunfailures` `@pytest.mark.flaky` |

## Mixing outer + inner parametrize

Both dimensions are first-class, both loadable from config, both pytest-native:

```python
@pytest.mark.parametrize("temp", [25, 55], scope="class")       # OUTER
class TestPowerOn:
    @pytest.mark.parametrize("voltage", [3.3, 3.6])             # INNER
    def test_voltage_rails(self, temp, voltage, dut, litmus_step):
        ...
```

### Execution order (with `scope="class"` on outer)

```
test_voltage_rails[25-3.3]
test_voltage_rails[25-3.6]
test_clock_lock[25]
── temp=25 done ──
test_voltage_rails[55-3.3]
test_voltage_rails[55-3.6]
test_clock_lock[55]
```

Outer varies slowest (class-scoped), inner nests inside. Stacked parametrize
on a method = cartesian product. Tuple-list parametrize = zip. Standard pytest.

## Sequence looping

A third axis of variation alongside outer vectors (whole-class) and inner
vectors (per-method): **repeat the whole sequence N times, or for a duration,
or until a condition**. Relevant for soak testing, stress testing, burn-in,
and convergence runs.

Parametrize covers the count-based case trivially, maps to time-based with a
truncation timer, and fundamentally cannot express condition-based without
leaving pytest's collection model. Handle each honestly.

### Count-based loop

The loop is just another dimension of the vector list. Expand before parametrize:

```yaml
# test_soak.vectors.yaml
sequence: test_soak
loop:
  repeat: 100     # class runs 100 times
```

Plugin expansion:

```python
def expand_loop(cfg):
    if loop := cfg.get("loop", {}).get("repeat"):
        return [{"iteration": i} for i in range(loop)]
    return [{}]
```

Mixed with real outer vectors (cartesian product across vector × loop):

```yaml
sequence: test_power_on
class:
  product:
    temp: [25, 55]
loop:
  repeat: 50       # 50 iterations per (temp) combination → 100 class runs total
```

Expansion chains loop onto class vectors: `[(25,0), (25,1), …, (25,49),
(55,0), …, (55,49)]`. Pytest runs 100 class iterations. The class body reads
`context.get("temp")` and `context.get("iteration")` uniformly.

### Time-based loop

Collection needs a list of items, but we don't know how many will fit in a
time budget. Solution: collect to a conservative upper bound, truncate at
runtime via a session timer.

```yaml
loop:
  duration: "30m"
  max_iterations: 10000   # safety upper bound for collection
```

Plugin behavior:

- At `pytest_generate_tests`: expand to `max_iterations` class runs.
- At session start: record wall-clock start time.
- At `pytest_runtest_setup`: if the current class iteration's start time is
  past `budget_start + duration`, call `pytest.exit("time budget exhausted")`
  (or skip remaining items and let the session end cleanly).

Reports show "ran N iterations out of M collected; M-N aborted." That's
acceptable — the loop's count was genuinely unknown in advance.

### Condition-based loop

`until: <expression>` is incompatible with parametrize because the item count
depends on results that don't exist until tests run. Two messy paths:

**(a) Outer pytest-loop wrapper.** A bash/Python wrapper re-invokes pytest
until the condition holds. Each invocation is a fresh session. Reports are
fragmented; locks are acquired/released per invocation. Simple to implement,
ugly for reporting.

```bash
# conceptual — a litmus-native wrapper would handle the details
while ! python -c "check_condition()"; do
    pytest sequences/test_soak.py
done
```

**(b) In-session re-entry via `pytest_runtestloop`.** After the normal loop
finishes, a custom hook inspects results and resubmits the collected items if
the condition isn't met. Keeps one session, one results file. But it fights
pytest's model — parametrize IDs collide, fixture finalization runs then
re-runs, item state gets confused. Ends up re-implementing call-site
synthesis inside pytest, which is exactly what we're trying to get rid of.

**Recommendation:** don't build condition-based looping in-plugin. When a
concrete use case arrives, solve it with (a) as a Litmus CLI wrapper
(`litmus loop --until=<expr> sequences/test_soak.py`). In-session re-entry is
not worth the complexity.

### Unified YAML schema

One YAML covers all three axes orthogonally:

```yaml
sequence: test_power_on

class:                       # whole-class iteration (outer vectors)
  product:
    temp: [25, 55]
    voltage_class: [low, high]

methods:                     # per-method sweeps (inner vectors)
  test_clock_stability:
    product:
      freq: [1e6, 10e6, 100e6]

loop:                        # repetition of each class combination
  repeat: 10
  # OR
  # duration: "30m"
  # max_iterations: 5000
```

Expansion combines them: `class_vectors × loop_count` at class scope,
`method_vectors` at method scope. Pytest runs `len(class_vectors) × loop_count`
class iterations; each class iteration runs every method, and methods with
inner sweeps iterate through their own vectors inside each class iteration.

### What parametrize does **not** cover

| Loop kind | Collection knows count? | Supported? |
|---|---|---|
| Count-based (`repeat: 100`) | yes | ✓ parametrize |
| Time-based (`duration: "30m"`) | upper-bound only | ✓ parametrize + runtime truncation |
| Condition-based (`until: pass_streak >= 10`) | no | ✗ requires outer wrapper |
| Adaptive (next iter depends on prior result) | no | ✗ requires runtime re-collection |

The first three cover the vast majority of soak/stress use cases. The
adaptive case is rare and should be its own Python driver, not part of the
sequence file model.

## Parallelism — unchanged

Litmus's parallelism is **file-lock based, machine-global, cross-process**:
`litmus/instruments/locks.py` uses `fcntl.flock()` on
`LITMUS_HOME/locks/<resource>.lock`. Different pytest invocations, different
projects on the same machine, different CI jobs all contend on the same lock
namespace because they share physical hardware.

This lives one layer below the test shape. Whether the caller is a class
method, a module function, or a leaf in a `ResolvedStep` tree, it's the same
`acquire_resource()` call.

| Concern | Mechanism | Changes with class-per-file? |
|---|---|---|
| Cross-process instrument sharing | `locks.py` | No |
| Route conflict detection | `RouteManager` + `_instrument_channel_map` | No |
| Per-session route state | session-scoped fixture | No |
| Lock ordering (instrument → switch) | `route_manager.py` | No |
| Lock auto-release on process death | `fcntl.flock` | No |
| In-process re-entrancy | new: class-scoped acquisition is cleaner | **improves slightly** |

**xdist** is a separate concern — if used, `--dist=loadscope` keeps a class on
one worker atomically; `--dist=loadgroup` + `@pytest.mark.xdist_group("station-A")`
gives explicit station affinity. But xdist isn't the primary parallelism
mechanism — file locks are.

## What disappears from the current plugin

Conservatively ~60% of plan/plugin machinery:

- `ResolvedStep` tree model
- `build_plan`, `build_flat_plan_from_items`, `build_parent_index`
- `__litmus_callsite__` parametrize trick (class parametrize replaces it)
- `skip_on` resolution and cycle detection (pytest-dependency or implicit chain)
- Fuzzy node-id matching for per-step config
- `_load_step_aliases_and_configs` walker
- `_litmus_group_sync` autouse + `sync_groups_for_leaf` logic (class = group)
- Most of `api/runner.py:_expand_sequence`

## What stays custom to Litmus

- `pytest_generate_tests` hook (load YAML → parametrize with `Vector`)
- `Vector(dict)` runtime wrapper (index, prev, changed())
- `litmus_step` fixture (measurements, limits, aliases)
- Group-outcome aggregation per class via `pytest_runtest_logreport`
- Status tracking (`ran` / `not_run` / `skipped_by_prereq`)
- Whitelist collection when running a named subset of sequences
- `conftest.py` fixtures for instruments (station, dut, smu, chamber)
- `instruments/` package (locks, pool, route_manager, server) — untouched

## Classes vs files vs YAML-driven — tradeoffs

| Dimension | File-only (no class) | One class per file | YAML-driven plan tree (today) |
|---|---|---|---|
| Filesystem as catalog | ✓ | ✓ | ✓ (via `sequences/*.yaml`) |
| IDE run-single-step | ✓ | ✓ | ✗ |
| IDE run-whole-sequence | ✓ (file) | ✓ (class) | via custom arg |
| Whole-sequence per outer vector | needs reorder hook | ✓ (`scope="class"`) | custom tree walk |
| Per-call-site identity | awkward (two YAML sidecars) | ✓ (class parametrize) | ✓ (planned_id) |
| Sequence reuse / inheritance | copy-paste | ✓ (real inheritance) | YAML composition |
| Operator-editable authoring | YAML sidecar only | YAML sidecar + Python body | pure YAML |
| Nesting | no | no | yes (arbitrary depth) |
| Complexity of Litmus plugin | minimal | small | large |
| Familiar to pytest users | fully | fully | custom |
| Familiar to hardware test engineers (OpenHTF background) | OK | ✓ (classes map to phases) | closest to OpenHTF |

## What you give up

1. **Arbitrary sequence nesting.** Today you can nest `sequence:` steps 3+ deep.
   Class model is flat — sub-sequences become either imports or a thin YAML
   composition layer above the classes.
2. **Pure-YAML authoring for operators.** Operators comfortable editing YAML
   now have to read Python to understand step order and measurements.
3. **Step-level config from outside.** Limits/aliases/mocks per step — either
   inline in the method body or in a companion `test_power_on.steps.yaml`.
   Inline is cleaner for dev UX, worse for operator edits.

## What you gain

1. **IDE runnability.** Every standard IDE runs classes and methods natively.
   Gutter "play" buttons work. No custom "run sequence" UX needed.
2. **Pytest-native debugging.** `pytest --pdb sequences/test_power_on.py::TestPowerOn::test_boot`
   drops you into the failing step.
3. **Pytest plugin ecosystem.** pytest-xdist, pytest-rerunfailures,
   pytest-dependency, pytest-order, pytest-html — all work without adapter code.
4. **Static analysis.** `grep -A5 "^class Test" sequences/*.py` is the full TOC.
   Class attributes like `context_inputs` are lintable.
5. **Much smaller plugin.** Fewer layers of custom machinery to maintain and
   debug. Pytest's primitives do most of the work.

## Open questions

1. **Implicit prereq semantics.** Does each parametrize iteration depend on
   the prior iteration's success, or only the prior *method*? Default: prior
   method, not prior iteration.
2. **Shared state across outer-vector iterations.** Module fixtures tear down
   between iterations (module-scoped). Do we want an opt-in "persist" scope
   for things like a DUT that shouldn't power-cycle between `temp=25` and
   `temp=55`? Session scope covers it today.
3. **Reporting the outer vector.** Today's `TestStep` has vectors at the step
   level. Now outer vectors apply at class scope. Do we add `outer_vec_*`
   columns on `TestStep`, or flatten into existing `in_*` columns? Schema
   question.
4. **DB-driven future.** If sequences become DB records, two paths:
   - **Materialize to files.** Server generates `.py` + `.vectors.yaml` into
     `sequences/` before a run. IDE works, grep works, git-review works.
   - **Virtual collection.** `pytest_collect_file` synthesizes modules at
     collection time. IDE tooling breaks (no real files to navigate to).
     Not recommended.
5. **Root of a run.** How is "run these 5 sequences" expressed? Options:
   - Plain pytest: `pytest sequences/test_power_on.py sequences/test_stress.py`
   - Config file: `run.yaml` listing files
   - Existing `--sequence` flag with a top-level composition YAML
6. **Multi-class inheritance.** Is `class TestFullPowerOn(TestPowerOn, TestStress)`
   supported? Pytest discovers inherited methods, but ordering across parents
   is MRO-dependent. Probably forbid multi-inheritance in `LitmusSequence`.

## Honest assessment

This design collapses much of the machinery we just built on `feat/sequence-hierarchy`. Treating a class as the group unit (not a `ResolvedStep` node),
parametrize values as call-sites (not `planned_id`s), and `conftest.py` as
fixture infrastructure (not `build_plan` stashes) eliminates whole categories
of custom code.

The cost is real: operator-facing pure-YAML authoring becomes hybrid (YAML for
vectors and limits, Python for structure). Nesting goes away; composition moves
to inheritance or to a thin top-level "run list" YAML. Step-level config from
config files becomes less central.

Whether those costs outweigh the benefits depends on who authors sequences.
If operators and technicians are the primary authors → YAML-driven plan tree
is the right call. If engineers who know Python are the primary authors → the
class-per-file model wins on complexity, IDE UX, and ecosystem leverage.

Today's users are primarily engineers running pytest. That argues for
one-class-per-file. But the operator-authoring case is real and a future
goal — so this question is strategic, not purely technical.
