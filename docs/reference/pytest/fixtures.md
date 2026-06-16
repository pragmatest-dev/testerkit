# Litmus fixtures

The bundled pytest plugin registers **20 public fixtures**, defined in `src/litmus/pytest_plugin/__init__.py`. Take any of them in a test's signature; pytest resolves and injects them by name. Names beginning with `_` (e.g. `_route_manager`, `_litmus_push_params`) are internal and may change without notice.

This page is the comprehensive reference. For a guided introduction see the [tutorial](../../tutorial/); for the seven `@pytest.mark.litmus_*` markers see [Litmus markers](litmus-markers.md).

## At a glance

Grouped by what you reach for the fixture **for**:

| Group | What you'd reach for it for | Fixtures |
|---|---|---|
| Recording measurements | Write a measurement row, resolve a limit, raise on FAIL, prompt the operator | `verify`, `measure`, `limits`, `prompt` |
| Talking to instruments | Get a driver instance, route a signal, hit a UUT pin | `instruments`, `instrument`, `instrument_records`, `uut`, `pins`, `routes`, `fixture_manager` |
| Reading per-test state | Active sweep params, observations, the connection currently being iterated | `context`, `connections` |
| Reading loaded configuration | The typed YAML / CLI that shaped this run | `part`, `station_config`, `fixture_config`, `run_context`, `mock_instruments` |
| Flow control | Drive the test body's iteration / synchronization | `vectors`, `sync` |

Plus **one role-named fixture per instrument the station YAML declares** (e.g. `dmm`, `psu`, `scope`). See [Per-role auto-fixtures](#per-role-auto-fixtures).

Every fixture above is available in every test — pytest will resolve any of them by name. The "what you'd reach for it for" column is intent, not availability. Several have meaningful "no project state" defaults (`part` returns `None`, `instruments` returns `{}`, `connections` returns `None`, etc.) so taking one in a vanilla project is safe.

---

## Recording measurements

The verbs you write into test bodies. Most tests need `verify` and nothing else from this group.

### `verify` — function

Callable: `verify(name, value, limit=None, characteristic=None)`. Records the measurement row (value, units, limits, traceability), resolves a limit from the active chain (sidecar / inline marker / part spec), stamps `measurement_outcome`, and **raises `AssertionError`** when the value is out of range.

`limit=` accepts either a `Limit` model or a dict literal — `verify` coerces dicts via `Limit.model_validate(...)`.

```python
def test_rail(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())            # limit resolves from sidecar/marker

def test_rail_inline(dmm, verify):
    verify("vout", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "units": "V"})         # inline dict literal
```

Same record-side effect as `measure`; the only difference is `verify` raises on FAIL. Use `verify` when a fail should stop the line. With no resolvable limit, `verify` raises `MissingLimitError` — unless the active profile sets `verify_requires_limit: false`, in which case it falls back to `measure` semantics (record-only, `Outcome.DONE`).

### `measure` — function

Callable: `measure(name, value, limit=None, characteristic=None)`. The record-only sibling of `verify` — records a measurement row with `outcome = DONE` and **never raises**, even when no limit resolves. Use it when a value should be captured but not pass/fail judged (characterization, diagnostics, sweeps you plot post-hoc).

```python
def test_voltage(dmm, measure):
    v = dmm.measure_dc_voltage()
    measure("output_voltage", v, limit={"low": 3.2, "high": 3.4, "units": "V"})
```

`limit=` accepts either a `Limit` model or a dict literal. Same recording path as `verify`, just no FAIL-side effect — use it when a failing measurement shouldn't abort the test.

### `limits` — function

Read-only `name → Limit` mapping for the current test, resolved from the same chain as `verify`. Use for ad-hoc pythonic assertions:

```python
def test_inline_check(dmm, limits):
    v = dmm.measure_dc_voltage()
    assert v in limits["output_voltage"]
```

`limits[name]` raises `KeyError` when no limit is configured — there is no silent default.

### `prompt` — function

Returns a callable that resolves operator prompts declared via `@pytest.mark.litmus_prompts`:

```python
@pytest.mark.litmus_prompts(
    inspect={"message": "Verify LED is GREEN", "prompt_type": "confirm"},
)
def test_visual(prompt, verify):
    prompt("inspect")  # blocks until operator responds
    verify("led_state", read_led_color())
```

See [`litmus_prompts`](litmus-markers.md#litmus_prompts) for the marker shape.

---

## Talking to instruments

These fixtures need a station YAML to produce useful results. Without one they return empty dicts / `None`.

### `instruments` — session

Yields `dict[role_name, driver_instance]`. Connects every instrument declared in the station YAML at session start, disconnects at session end. Auto-mocks when `--mock-instruments` is on. Identity and calibration are checked against config for real hardware.

```python
def test_voltage(instruments):
    dmm = instruments["dmm"]
    assert dmm.measure_dc_voltage() > 3.0
```

In most tests you take **role names directly** as fixtures (`def test_x(dmm, psu)`) — see [Per-role auto-fixtures](#per-role-auto-fixtures) — and never need `instruments` itself.

### `instrument` — function

Returns an `InstrumentAccessor` for role-keyed access with grouping:

```python
def test_one(instrument):
    dmm = instrument("dmm")

def test_all(instrument):
    dmms = instrument.by_type("pymeasure.instruments.keithley.Keithley2000")
```

### `instrument_records` — session

Returns `dict[role_name, InstrumentRecord]` — the resolved instrument metadata (driver class, resource string, calibration cert, mocked flag) before connection. Useful for tests that need identity or calibration info without taking the live driver.

### `uut` — session

Yields the connected UUT driver (resolved from `Part.driver` + `FixtureConfig.uut_resource`), or `None` when the part has no driver. Mocked when `--mock-instruments` is on.

```python
def test_firmware(uut):
    assert uut.get_version().startswith("2.")
```

### `pins` — session

Returns a `PinAccessor` for UUT-centric pin access. Looks up the instrument that the fixture YAML maps to each UUT pin, transparently activates the route if any switch is in the path.

```python
def test_output(pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    assert pins["VOUT"].measure_voltage() > 3.0
```

Raises `pytest.UsageError` if no fixture config or instruments are loaded.

### `routes` — function

Yields a `RouteManager` for explicit switch routing, or `None` when no routes exist:

```python
def test_vout(dmm, routes):
    with routes.for_pin("VOUT"):
        v = dmm.measure_voltage()
```

`routes.deactivate_all()` runs automatically at test teardown.

### `fixture_manager` — session

Returns the `FixtureManager` directly, for the rare test that needs advanced lookup (e.g. net-name → connection) beyond what `pins` exposes:

```python
def test_lookup(fixture_manager):
    conn = fixture_manager.get_connection_for_net("VOUT_3V3")
    inst = fixture_manager.get_instrument_for_connection(conn.name)
```

---

## Reading per-test state

The active vector's params, observations, and currently-bound connection.

### `context` — function

Returns a `Context` exposing the run / UUT / station / vector state for the active test. Resolves on every test, with empty defaults when there's nothing to expose.

| Method | Returns | Purpose |
|---|---|---|
| `context.get_param(name, default=None)` | `Any` | Read a sweep / parametrize value. |
| `context.params` | `dict` | All active params for this row. |
| `context.changed(key)` | `bool` | True if `key` differs from prior iteration. |
| `context.last(key, default=None)` | `Any` | Prior iteration's value for `key`. |
| `context.observe(key, value)` | `None` | Record a free-form observation. |
| `context.observations` | `dict` | All recorded observations. |
| `context.part` | `Part \| None` | Active part definition (= `part` fixture). |
| `context.station` | `StationConfig \| None` | Active station config (= `station_config` fixture). |
| `context.run` | `TestRun \| None` | The current `TestRun`. |
| `context.limits` | `LimitsView` | Read-only limits mapping (= `limits` fixture). |
| `context.characteristics` | `tuple[str, ...]` | Active characteristic IDs from `litmus_characteristics`. |

```python
def test_rail(context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin", 5.0))
    verify("vout", dmm.measure_dc_voltage())
```

### `connections` — function

Returns the `ConnectionIterator` resolved from `litmus_characteristics` / `litmus_connections` markers, or `None` when no markers are declared.

```python
def test_per_pin(connections, dmm):
    for conn in connections:
        v = dmm.measure_voltage()
```

---

## Reading loaded configuration

Typed accessors over the YAML / CLI that shaped this run. Each one resolves to its model OR `None` (or an empty dict / bool) — taking one in a vanilla project is safe.

### `part` — session

Returns the active `Part` definition loaded from `parts/*.yaml` (identity, pins, characteristics), or `None` if no `parts/` directory or no match. For derived limits use the `limits` fixture or `context.get_limit(name)`.

Resolution chain (first match wins):
1. `--part <id-or-path>` — `<id>` looks up `parts/<id>.yaml`; `<path>` is used directly.
2. `--uut-part-number <pn>` — content match against `part.part_number:` across `parts/*.yaml`.
3. Single-file fallback when `parts/` holds exactly one part file.
4. `None`.

```python
def test_spec(part, context, dmm, verify):
    if part:
        assert part.part_number == "DEMO-BUCK-3V3"
    verify("output_voltage", dmm.measure_dc_voltage())  # limit resolves from the part spec
```

### `station_config` — session

Returns the `StationConfig` resolved from `--station` / `stations/*.yaml`, or `None`. Also publishes the value to the active-station ContextVar so `context.station` works without taking the fixture.

### `fixture_config` — session

Returns the `FixtureConfig` resolved from `--fixture` / `fixtures/*.yaml`, or `None`. In worker mode (multi-slot), extracts just this slot's `connections` and `uut_resource`.

### `run_context` — session

Returns the `RunContext` carried on the active `TestRunLogger`. Use it to attach run-level metadata that persists across tests:

```python
def test_setup(run_context):
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("fixture_serial", "FIX-001")
```

For per-test or per-vector state, use `context` instead.

### `mock_instruments` — session

Returns `bool`. True when `--mock-instruments` was passed or `LITMUS_MOCK_INSTRUMENTS=1` is set. The same flag drives the `instruments` fixture's behavior; tests rarely take it directly except for diagnostic branches.

---

## Flow control

Two fixtures that drive the test body's iteration shape, not just expose data. `vectors` collapses pytest's per-row case multiplication into one in-body loop; `sync` blocks the body until peer workers reach the same named point.

### `vectors` — function

Taking `vectors` in the test signature switches collection to **self-loop mode**: every source of vectors (`@pytest.mark.parametrize`, `litmus_sweeps`, sidecar `sweeps:`, profile overrides) is consolidated into one matrix at collection time, and the test runs as a single pytest case. The test body iterates the matrix itself:

```python
def test_sweep(vectors, psu, dmm, measure):
    for v in vectors:
        psu.set_voltage(v["vin"])
        measure("vout", dmm.measure_dc_voltage())
```

Each `for` iteration pushes the row's params + index into active state so `measure`, `verify`, and `context` see the same row-scoped context they would in normal (one-case-per-row) mode. The fixture fails the test at teardown if the matrix is non-empty but the body iterated zero times.

Choose self-loop mode when an outer setup (thermal soak, supply ramp) shouldn't repeat per row; choose normal parametrize mode when you want pytest to report one case per row.

### `sync` — session

Yields a `SyncPoint` for multi-UUT coordination when running in worker mode (`_LITMUS_SLOT_ID` is set), or `None` in single-slot mode. `sync.wait(name, timeout=...)` blocks until every slot reaches the same name:

```python
def test_measure_hot(dmm, sync):
    if sync:
        sync.wait("thermal_soak", timeout=300)
    v = dmm.measure_voltage()
```

---

## Per-role auto-fixtures

When the plugin finds a station YAML at `pytest_configure`, it dynamically registers one session-scoped fixture per `instruments:` role. A station YAML like

```yaml
instruments:
  dmm: keithley_dmm_001
  psu: keysight_psu_002
  scope: tek_dpo_003
```

exposes `dmm`, `psu`, and `scope` as fixtures, each returning the connected driver for that role:

```python
def test_rail(dmm, psu, verify):
    psu.set_voltage(5.0)
    verify("vout", dmm.measure_dc_voltage())
```

These names are not hard-coded — they come from your station YAML at session start. Source: `src/litmus/pytest_plugin/hooks.py:232–274`.

---

## See also

- [Litmus markers](litmus-markers.md) — the seven `@pytest.mark.litmus_*` decorators and their sidecar equivalents
- [pytest-native reference](../overview/pytest-native.md) — how the bundled plugin uses pytest's own collection / fixtures / markers
- [Models](../data/models.md) — `Limit`, `MeasurementLimitConfig`, `PartContext`, `StationConfig`, `FixtureConfig` field shapes
- [Test vectors & sweeps](../../how-to/execution/vector-expansion.md) — `litmus_sweeps`, `parametrize`, and the `vectors` self-loop fixture
- [Spec-driven testing](../../how-to/execution/spec-driven-testing.md) — `litmus_characteristics` + `connections` workflow
