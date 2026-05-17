# Context Architecture

The Litmus `context` fixture is a **read-only ambient roll-up** of the run-, station-, product-, and vector-level state a test needs. Per-test access goes through `context.run` / `context.station` / `context.product` plus the iteration-state attributes (`context.params`, `context.limits`, `context.connections`, etc.). All values are sourced from ContextVars seeded by session fixtures; tests cannot mutate the shared view.

DUT identity intentionally lives at `context.run.dut` — there is no `context.dut` attribute because the bare `dut` fixture is the live DUT driver (a different concept). For the same reason `context.instruments` is not exposed: take the `instruments` fixture as a test argument when you need it.

## Read / write split

| Fixture   | Direction     | Role                                                       |
|-----------|---------------|------------------------------------------------------------|
| `context` | **Read-only** | Run / station / product / vector state                     |
| `verify`  | **Write**     | Limit check + record; raises `AssertionError` on FAIL      |
| `logger`  | **Write**     | Pure recorder (no raise); used for characterization rows   |

`verify` and `logger` are deliberately separate from `context`. Seeing either in a test signature flags "this test records to the audit trail"; tests without them are pure reads. `grep -E 'verify\(|logger\.measure'` finds every write.

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
context.run                     # TestRun model: id, started_at, dut, station_id, ...
context.run.dut.serial          # DUT identity (bare `dut` fixture is the live driver)
context.station                 # StationConfig | None (fixture: `station_config`)
context.product                 # ProductContext | None (fixture: `product_context`)
context.params["vin"]           # function (litmus_sweeps / pytest parametrize)
context.limits["output_v"]      # function (resolved from markers + sidecar + product)
context.connections             # iterator of FixtureConnection (litmus_characteristics / litmus_connections)
context.get_param("vin")        # read a param (returns default if missing)
context.changed("temperature")  # did this param differ from the previous iteration?
context.last("output_voltage")  # last recorded value of this measurement name
context.observe("dut_temp", 42.3)  # record an environmental observation
```

## Where each value comes from

| Attribute      | Source ContextVar / fixture                                    |
|----------------|----------------------------------------------------------------|
| `run`          | `get_current_logger().test_run`                                |
| `station`      | `get_active_station_config()` — seeded by `station_config` fixture |
| `product`      | `get_active_product_context()` — seeded by `product_context` fixture |
| `params`       | merged with parent chain; pytest's `callspec.params` + sweeps   |
| `limits`       | `get_active_limits()` — seeded by `_litmus_push_limits` autouse |
| `connections`  | `_litmus_resolve_connections` autouse populates `ctx.connections` |

## Prior-context memory (for `changed()` / `last()`)

Stored as a dict on the method's **parent stash node** (class for class methods, module for loose functions) via `pytest.StashKey`:

- No cross-talk — `TestA::test_foo` and `TestB::test_foo` land in different parent stashes
- Auto-teardown when pytest is done with the class/module — no manual clear
- Scoped to the same level as the test's container, matching the containment structure
- Stable across sweep cases of the same method

## The payoff: `context.changed()`

Hardware reconfig dominates multi-parameter sweeps. `context.changed("temp")` returns `True` only when that parameter differs from the previous sweep iteration:

```python
@pytest.mark.litmus_sweeps([
    {"temperature": [25, 85]},        # outer (slow)
    {"vin": [4.5, 5.0, 5.5]},          # middle
    {"load": [0.1, 0.4]},              # inner (fast)
])
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
| Measurement      | name, value, units, limits, outcome, limit_comparator          |
| Signal path      | DUT pin, fixture point, instrument name, channel, resource     |
| DUT              | serial, product, revision, lot                                 |
| Station          | id, name, type, location                                       |
| Context          | operator, phase, pytest node id, git commit, param values, retry, timestamp |

All traceability fields are injected by the plugin — the test body only calls `verify(name, v)` or `logger.measure(name, v, ...)`.

## See also

- [Writing Tests](writing-tests.md) — end-to-end patterns
- [Test Vectors guide](vector-expansion.md) — sweep shapes, generators, loop ordering
- [Litmus fixtures](../reference/litmus-fixtures.md) — all 20 plugin fixtures with signatures
