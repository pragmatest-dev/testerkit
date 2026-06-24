# Read and write the test context

The `context` fixture is the test's view of what's active right now: the run record, the station, the part, the current sweep iteration's params, the resolved limits, and the active fixture connections. It also stamps two kinds of side data onto the row — `configure()` for the stimulus inputs you set, `observe()` for the environmental readings you take.

Take `context` as a test argument when you need any of that. If a test only takes a single measurement against a single setpoint and never sweeps, you can skip it.

```python
def test_rails(self, context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin"))
    verify("output_voltage", dmm.measure_dc_voltage())
```

UUT identity is at `context.run.uut` — the bare `uut` fixture is a different thing (the live driver). See [Litmus fixtures](../../reference/pytest/fixtures.md) for the full per-test entry points.

## Skip expensive setup across a sweep

`context.changed("name")` returns `True` only when that parameter rolled over from the previous iteration. Gate slow hardware reconfig on it so a 30-minute thermal soak runs twice, not twelve times.

```python
@pytest.mark.litmus_sweeps([
    {"temperature": [25, 85]},        # outer (slow)
    {"vin": [4.5, 5.0, 5.5]},          # middle
    {"load": [0.1, 0.4]},              # inner (fast)
])
def test_rails(temperature, vin, load, context, psu, chamber, uut_load, dmm, verify):
    if context.changed("temperature"):
        chamber.set_temperature(temperature)
        chamber.wait_for_stable()      # 20 min — skipped when temperature unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
    uut_load.set(load)
    verify("output_voltage", dmm.measure_dc_voltage())
```

The 2 × 3 × 2 sweep above runs 12 cases. Without `changed("temperature")`, the chamber resoaks 12 times. With it, twice.

`changed("name")` is `True` on the first iteration — there is no previous value to compare against. If you want "second iteration onward", check `context.last("name") is not None` instead.

See [Test vectors](vector-expansion.md) for sweep shapes, axis ordering, and how `litmus_sweeps` cross-products into iterations.

## Read sweep / parametrize values

The fixture is source-agnostic — `litmus_sweeps`, `pytest.mark.parametrize`, and `@pytest.fixture(params=...)` all land in `context.params` the same way.

```python
def test_rails(self, context, psu, dmm, verify):
    vin = context.get_param("vin", default=5.0)
    psu.set_voltage(vin)
    verify("output_voltage", dmm.measure_dc_voltage())
```

You can also take the param as a regular pytest argument (`def test_rails(self, vin, context, ...)`) and skip `get_param` — the two forms see the same value. Pick whichever reads cleaner.

`context.get_param(name, default)` returns the default if no sweep / parametrize was declared. `context.params[name]` raises `KeyError` instead — pick by whether a missing param is an error or just absent.

## Read run, station, and part

Three properties surface the entities that are active for this test.

```python
def test_serial_stamp(self, context, verify):
    serial = context.run.uut.serial            # str (UUT.serial is required); context.run itself is None outside a run
    verify("serial_present", bool(serial))
```

| Attribute         | Type                       | Equivalent fixture       |
|-------------------|----------------------------|--------------------------|
| `context.run`     | `TestRun \| None`          | (no fixture — read here) |
| `context.station` | `StationConfig \| None`    | `station_config`         |
| `context.part` | `Part \| None`   | `part`        |

Each returns `None` when the corresponding tier is absent. Bringup tests (no `stations/` YAML) get `context.station is None`; tests that don't load a part get `context.part is None`. Guard with `if context.station:` before reaching for fields, or take the typed fixture (`station_config`) when the test only runs with a station present — pytest will skip it otherwise.

See [Stations](../../concepts/configuration/stations.md) and [Parts](../../concepts/configuration/parts.md) for the underlying entities.

## Record stimulus inputs with `configure()`

When a stimulus value isn't already a sweep param — for example, the PSU's *actual* output voltage after readback — stamp it with `configure()` so it lands on the row alongside the values pytest already knows about.

```python
def test_rails(self, context, psu, dmm, verify):
    psu.set_voltage(5.0)
    context.configure("psu.actual_voltage", psu.read_voltage(), unit="V")
    verify("output_voltage", dmm.measure_dc_voltage())
```

`unit=` is optional and is stored in the parquet `inputs` column alongside the value. Use bare names that match spec condition keys (`temperature`, `load`) when the value drives a band lookup; use a fixture prefix (`psu.actual_voltage`, `dmm.sample_count`) for implementation detail. Whatever you record is visible to `context.last("psu.actual_voltage")` on the next iteration.

## Record environmental readings with `observe()`

`observe()` is the sibling for readings that are *context* for the measurement rather than the measurement itself — chamber temperature, humidity, raw waveform snapshots, anything you'd want to plot alongside the value but wouldn't gate a verdict on.

```python
def test_output_voltage(self, context, dmm, temp_probe, verify):
    context.observe("temp_probe.temperature", temp_probe.read(), unit="°C")
    context.observe("temp_probe.humidity",    temp_probe.read_humidity(), unit="%RH")
    verify("output_voltage", dmm.measure_dc_voltage())
```

`unit=` is optional and is stored in the parquet `outputs` column alongside the value. Large numeric arrays (raw waveforms, sample blocks) route to the [channel store](../data/querying-channels.md) automatically — `observe()` writes the array and stashes a `channel://` URI on the row. Scalars go straight onto the row.

Inside a `context.connections` loop, `observe()` auto-stamps `uut_pin` from the active connection — the same automatic pinning that `verify` gets. A raw capture (`observe("scope.cap", wf)`) recorded while iterating pins lands with the active pin's identity so you can later filter observations by `uut_pin`.

For how inputs and outputs land on measurement rows and how to query them by role and name, see [Traceability](traceability.md).

## Read back what you set last iteration

`context.last("name")` returns whatever you set (via `configure` or `observe`) on the previous sweep iteration of the same test. It is **not** a measurement log — `last("output_voltage")` returns `None` if you `verify`d that value but never `configure`d or `observe`d it.

```python
def test_drift(self, context, dmm, verify):
    now = dmm.measure_dc_voltage()
    prev = context.last("output_voltage")
    context.observe("output_voltage", now)
    if prev is not None:
        verify("drift_v", abs(now - prev))
```

Returns `None` on the first iteration (no previous context to read from) and when the key was never stashed.

## Resolve a limit by name with `get_limit()`

`context.limits["name"]` returns the raw limit entry as it was written in a marker or sidecar — useful only if you want to inspect what was declared. `context.get_limit("name")` runs the full resolver and gives you back a `Limit` with concrete `low` / `high` / `nominal` numbers, evaluated for the active sweep iteration.

Reach for `get_limit` when test logic needs to *react* to a limit — adaptive sample counts, conditional setup, decision branches:

```python
def test_adaptive(self, context, dmm, verify):
    limit = context.get_limit("output_voltage")
    # Take more samples when the spec window is tight (< 5% of nominal).
    tight = (
        limit is not None
        and limit.low is not None and limit.high is not None
        and limit.nominal is not None
        and (limit.high - limit.low) < 0.05 * limit.nominal
    )
    samples = 10 if tight else 5
    readings = [dmm.measure_dc_voltage() for _ in range(samples)]
    verify("output_voltage", sum(readings) / len(readings))
```

The `Limit` object exposes `low` / `high` / `nominal` / `unit` / `comparator` plus traceability fields — see [`Limit` in the models reference](../../reference/data/models.md#model-limit) for the full surface. `get_limit` returns `None` when no limit is defined for that name. For *applying* a limit to a measurement, just pass `limit=...` to `verify` — the resolver runs there automatically.

See [Limits](limits.md) for limit resolution order and [Spec-driven testing](spec-driven-testing.md) for how part specs feed in.

## Iterate active fixture connections

To take the same measurement on every rail, iterate `context.connections` (after declaring `@pytest.mark.litmus_characteristics([...])` or `@pytest.mark.litmus_connections(...)`). Each step of the loop closes the switch matrix to that connection's pin, so the same `dmm.measure_dc_voltage()` call lands on a different rail every time around — and the platform stamps the row with the connection's `uut_pin` and the matching characteristic id automatically. (What a fixture connection is: see [Fixtures](../../concepts/configuration/fixtures.md).)

```python
@pytest.mark.litmus_characteristics(["rail_3v3", "rail_5v"])
def test_all_rails(self, context, dmm, verify):
    for conn in context.connections:
        # Switch matrix is now routed to conn.uut_pin; verify stamps the row
        # with uut_pin + the matching characteristic_id automatically.
        verify("voltage", dmm.measure_dc_voltage())
```

The loop variable `conn` carries the connection's `uut_pin`, `instrument`, `instrument_channel`, and `instrument_terminal` if you need them for diagnostics or per-rail setup — but for the measurement itself, the platform handles routing and traceability stamping. Test code reads the same whether you have one rail or ten.

To walk one characteristic at a time when several are in scope, scope the iteration with `for_characteristic`:

```python
@pytest.mark.litmus_characteristics(["rail_3v3", "rail_5v"])
def test_all_rails(self, context, dmm, verify):
    for char_id in context.characteristics:
        for conn in context.connections.for_characteristic(char_id):
            verify(f"{char_id}.voltage", dmm.measure_dc_voltage())
```

If the test declares connections but never iterates them, the run fails — silent skips are worse than errors. The `connections` fixture is also available as a direct argument when you'd rather not reach through `context`.

See [Spec-driven testing](spec-driven-testing.md) for the characteristic / connection / spec workflow.

## Keep mutable state across sweep iterations

`context.params` is read-only — assigning to it has no effect. For a writable scratchpad shared across iterations of the same class, use a `scope="class"` pytest fixture — no Litmus-specific API needed:

```python
import time
import pytest

class TestPowerBoard:
    @pytest.fixture(scope="class")
    def seen(self):
        return {"max_temp_c": float("-inf")}

    def test_thermal(self, context, seen, temp_probe, verify):
        t = temp_probe.read()
        seen["max_temp_c"] = max(seen["max_temp_c"], t)
        verify("max_temp_c", seen["max_temp_c"])
```

`scope="class"` keeps the dict alive for the lifetime of the class; `scope="module"` for a whole file; `scope="session"` for the entire run. The fixture is torn down by pytest at the corresponding boundary.

## Common mistakes

- **`context.uut` is an `AttributeError`.** UUT identity is at `context.run.uut`. The bare `uut` fixture is the live driver — a different concept.
- **`context.changed("foo")` is `True` on the first iteration.** Use `context.last("foo") is not None` if you mean "from the second iteration onward."
- **`context.last("output_voltage")` returns `None` when you `verify`d but didn't `configure`/`observe`.** It reads the prior context's `configure` / `observe` stash, not the measurement log.
- **`context.limits["x"]` is the config, not the resolved limit.** Use `context.get_limit("x")` for `low` / `high` / `nominal`.
- **Reading the station inside a helper? Take the `station_config` fixture argument** instead of reaching through `context.run` — it's cleaner and lets pytest skip the helper automatically when no station is loaded.

## See also

- [Writing tests](writing-tests.md) — end-to-end pytest patterns
- [Test vectors](vector-expansion.md) — sweep shapes, axis ordering, `changed()` patterns
- [Traceability](traceability.md) — how `configure` / `observe` land as inputs and outputs on measurement rows, and how to query them by role and name
- [Limits](limits.md) — resolution order for `get_limit()`
- [Litmus fixtures](../../reference/pytest/fixtures.md) — every plugin fixture with signature
- [Parquet schema](../../reference/data/parquet-schema.md) — the row shape that holds these values
- [Fixtures concept](../../concepts/configuration/fixtures.md) — hardware fixtures vs pytest fixtures
