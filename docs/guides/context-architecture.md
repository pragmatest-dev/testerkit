# Context Architecture

The Litmus `context` fixture is the **hierarchical roll-up of everything a test needs to know**: run metadata, station, DUT, active spec, instruments, vector parameters, resolved limits. Each scope of pytest's fixture graph contributes one layer; the per-test aggregate is read-only.

```
session   → SessionLayer   run_id, station, operator, dut, instruments    frozen
module    → ModuleLayer    + module markers, + spec default                frozen, inherits
class     → ClassLayer     + class litmus_limits markers                   frozen, inherits
function  → Context        + method markers, + params, + resolved limits   fresh per test
```

Upper layers are frozen dataclasses built by scope-matched pytest fixtures. The function-scope `context` fixture reads from them and adds method-level fields. No test can mutate another test's view.

## Read / write split

| Fixture   | Direction  | Role                                                |
|-----------|------------|-----------------------------------------------------|
| `context` | **Read-only** | Vector inputs + metadata; aggregate of all layers |
| `logger`  | **Write**     | Measurement and event sink                        |
| `spec`    | **Read + check** | Product spec → limits, pin info; also writes via `check` |

`logger` is deliberately separate from `context`. Seeing `logger` in a test signature flags "this test records to the audit trail"; tests without `logger` are pure reads. `grep logger.measure` finds every write.

## Two shapes, one result

Both forms resolve to the same cached fixture instances:

```python
# Aggregate: everything from context
def test_rails(self, context, logger, dmm, verify):
    vin = context.get_param("vin")
    verify("output_voltage", dmm.measure_dc_voltage())

# Destructured: only what's needed
def test_rails(self, dmm, verify, logger):
    verify("output_voltage", dmm.measure_dc_voltage())
```

`context.params["vin"]` and native `request.node.callspec.params["vin"]` point at the same dict.

## `context` at a glance

```python
context.run.id                  # session
context.dut.serial              # session
context.station.name            # session
context.instruments             # session + station + catalog
context.params["vin"]           # function (litmus_sweeps / pytest parametrize)
context.limits["output_v"]      # function (resolved from markers + sidecar + spec)
context.get_param("vin")        # read a param (raises if missing, accepts default)
context.changed("temperature")  # did this param differ from the previous iteration?
context.last("output_voltage")  # last recorded value of this measurement name
context.observe("dut_temp", 42.3)  # record an environmental observation
```

## Merge semantics

| Field     | Method + class combination                          | Where merging happens                  |
|-----------|-----------------------------------------------------|----------------------------------------|
| `params`  | Cartesian product (pytest rejects duplicate argnames) | pytest, via `metafunc.parametrize()` |
| `spec`    | Session-scoped (single product per run)              | `--product` / `litmus.yaml` / profile  |
| `limits`  | Dict merge by name; method keys override class keys  | `_litmus_push_limits` autouse         |
| `mocks`   | Sidecar `mocks:` block; per-test via `pytest-mock`   | `_litmus_install_mocks` autouse       |

## Prior-context memory (for `changed()` / `last()`)

Stored as a dict on the method's **parent stash node** (class for class methods, module for loose functions) via `pytest.StashKey`:

- No cross-talk — `TestA::test_foo` and `TestB::test_foo` land in different parent stashes
- Auto-teardown when pytest is done with the class/module — no manual clear
- Scoped to the same level as the test's container, matching the containment structure
- Stable across sweep cases of the same method

## The payoff: `context.changed()`

Hardware reconfig dominates multi-parameter sweeps. `context.changed("temp")` returns `True` only when that parameter differs from the previous sweep iteration:

```python
@pytest.mark.litmus_sweeps(temperature=[25, 85])    # outer (slow)
@pytest.mark.litmus_sweeps(vin=[4.5, 5.0, 5.5])      # middle
@pytest.mark.litmus_sweeps(load=[0.1, 0.4])          # inner (fast)
def test_rails(temperature, vin, load, context, psu, chamber, dut_load, dmm, verify):
    if context.changed("temperature"):
        chamber.set_temperature(temperature)
        chamber.wait_for_stable()     # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
    dut_load.set(load)
    verify("output_voltage", dmm.measure_dc_voltage())
```

Without `changed()`, a 2 × 3 × 2 sweep (12 vectors) reconfigures the chamber 12 times. With it, the chamber changes twice.

## Mutable scratch state across sweep iterations

`context.params` is read-only. For a mutable scratchpad across iterations, use a `scope="class"` fixture — native pytest, zero new API:

```python
class TestPowerBoard:
    @pytest.fixture(scope="class")
    def xstate(self):
        return {"first_calibration_ts": None}

    def test_rails(self, context, xstate, dmm, verify):
        if xstate["first_calibration_ts"] is None:
            xstate["first_calibration_ts"] = time.time()
        verify("output_voltage", dmm.measure_dc_voltage())
```

## Data flow to parquet

Each `verify` / `logger.measure` call produces one measurement row containing:

| Category         | Fields                                                         |
|------------------|----------------------------------------------------------------|
| Measurement      | name, value, units, limits, outcome, comparator                |
| Signal path      | DUT pin, fixture point, instrument name, channel, resource     |
| DUT              | serial, product, revision, lot                                 |
| Station          | id, name, type, location                                       |
| Context          | operator, phase, sequence id, git commit, param values, attempt, timestamp |

All traceability fields are injected by the plugin — the test body only calls `verify(name, v)` or `logger.measure(name, v, ...)`.

## See also

- [Writing Tests](writing-tests.md) — end-to-end patterns
- [Test Vectors guide](vector-expansion.md) — sweep shapes, generators, loop ordering
- [pytest-native reference](../reference/pytest-native.md) — fixture card
