# Litmus fixtures

The Litmus pytest plugin registers **20 public fixtures**, defined in `src/litmus/pytest_plugin/__init__.py`. Take any of them in a test's signature; pytest resolves and injects them by name. Names beginning with `_` (e.g. `_route_manager`, `_litmus_push_params`) are internal and may change without notice.

This page is the comprehensive reference. For a guided introduction see the [tutorial](../tutorial/index.md); for the seven `@pytest.mark.litmus_*` markers see [Litmus markers](litmus-markers.md).

## At a glance

| Group | Fixtures |
|---|---|
| Always available | `logger`, `context` |
| Configuration | `product_context`, `station_config`, `fixture_config`, `run_context`, `mock_instruments` |
| Hardware access | `instruments`, `instrument`, `instrument_records`, `dut`, `pins`, `routes`, `fixture_manager` |
| Per-test workflow | `verify`, `limits`, `connections`, `prompt` |
| Special modes | `vectors`, `sync` |

Plus **one role-named fixture per instrument** the station YAML declares (see [Per-role auto-fixtures](#per-role-auto-fixtures)).

---

## Always available

These two fixtures resolve on every pytest run, including vanilla `tests/test_*.py` with no station, no product, no sidecar.

### `logger` — session, autouse

Yields a `TestRunLogger`. Autouse, so even tests that don't take it as an argument get measurement logging via `verify` (which routes through the current logger). Opens the event log and Parquet subscriber at session start, flushes them at session end.

```python
def test_voltage(dmm, logger):
    v = dmm.measure_dc_voltage()
    logger.measure("output_voltage", v, units="V")
```

`logger.measure(name, value, *, units=None, limit=None)` records a measurement row without raising. See [`verify`](#verify--function) for the raising variant.

### `context` — function

Returns a fresh `Context` exposing the run/DUT/station/vector state for the active test. Always present — no station or sidecar required.

| Method | Returns | Purpose |
|---|---|---|
| `context.get_param(name, default=None)` | `Any` | Read a sweep / parametrize value. |
| `context.params` | `dict` | All active params for this row. |
| `context.changed(key)` | `bool` | True if `key` differs from prior iteration. |
| `context.last(key, default=None)` | `Any` | Prior iteration's value for `key`. |
| `context.observe(key, value)` | `None` | Record a free-form observation. |
| `context.observations` | `dict` | All recorded observations. |
| `context.product` | `ProductContext \| None` | Active product context (= `product_context` fixture). |
| `context.station` | `StationConfig \| None` | Active station config (= `station_config` fixture). |
| `context.run` | `TestRun \| None` | The current `TestRun`. |
| `context.limits` | `LimitsView` | Read-only limits mapping (= `limits` fixture). |
| `context.characteristics` | `tuple[str, ...]` | Active characteristic IDs from `litmus_characteristics`. |

```python
def test_rail(context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin", 5.0))
    verify("vout", dmm.measure_dc_voltage())
```

---

## Configuration

These fixtures expose the typed configuration models loaded from project YAML. They resolve to `None` when the relevant YAML is absent — tests can safely take them in vanilla projects.

### `product_context` — session

Returns a `ProductContext` loaded from `products/*.yaml`, or `None` if no `products/` directory or no match.

Resolution chain (first match wins):
1. `--product <id-or-path>` — `<id>` looks up `products/<id>.yaml`; `<path>` is used directly.
2. `--dut-part-number <pn>` — content match against `product.part_number:` across `products/*.yaml`.
3. Single-file fallback when `products/` holds exactly one product file.
4. `None`.

```python
def test_spec(product_context, dmm, verify):
    if product_context:
        limit = product_context.get_limit("output_voltage", temperature=25)
    verify("output_voltage", dmm.measure_dc_voltage())
```

### `station_config` — session

Returns the `StationConfig` resolved from `--station` / `stations/*.yaml`, or `None`. Also publishes the value to the active-station ContextVar so `context.station` works without taking the fixture.

### `fixture_config` — session

Returns the `FixtureConfig` resolved from `--fixture` / `fixtures/*.yaml`, or `None`. In worker mode (multi-slot), extracts just this slot's `connections` and `dut_resource`.

### `run_context` — session

Returns the `RunContext` carried on the active `TestRunLogger`. Use it to attach run-level metadata that persists across tests:

```python
def test_setup(run_context):
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("fixture_serial", "FIX-001")
```

For step- or vector-scoped state, use `context` instead.

### `mock_instruments` — session

Returns `bool`. True when `--mock-instruments` was passed or `LITMUS_MOCK_INSTRUMENTS=1` is set. The same flag drives the `instruments` fixture's behavior; tests rarely take it directly except for diagnostic branches.

---

## Hardware access

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

### `dut` — session

Yields the connected DUT driver (resolved from `Product.driver` + `FixtureConfig.dut_resource`), or `None` when the product has no driver. Mocked when `--mock-instruments` is on.

```python
def test_firmware(dut):
    assert dut.get_version().startswith("2.")
```

### `pins` — session

Returns a `PinAccessor` for UUT-centric pin access. Looks up the instrument that the fixture YAML maps to each DUT pin, transparently activates the route if any switch is in the path.

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

## Per-test workflow

The everyday fixtures for writing test bodies.

### `verify` — function

Callable: `verify(name, value, *, limit=None)`. Records the measurement row (value, units, limits, traceability), resolves a limit from the active chain (sidecar / inline marker / product spec), stamps `measurement_outcome`, and **raises `AssertionError`** when the value is out of range.

```python
def test_rail(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

Same record-side effect as `logger.measure`; the only difference is `verify` raises on FAIL. Use `verify` when a fail should stop the line.

### `limits` — function

Read-only `name → Limit` mapping for the current test, resolved from the same chain as `verify`. Use for ad-hoc pythonic assertions:

```python
def test_inline_check(dmm, limits):
    v = dmm.measure_dc_voltage()
    assert v in limits["output_voltage"]
```

`limits[name]` raises `KeyError` when no limit is configured — there is no silent default.

### `connections` — function

Returns the `ConnectionIterator` resolved from `litmus_characteristics` / `litmus_connections` markers, or `None` when no markers are declared.

```python
def test_per_pin(connections, dmm):
    for conn in connections:
        v = dmm.measure_voltage()
```

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

## Special modes

### `vectors` — function

Taking `vectors` in the test signature switches collection to **self-loop mode**: every source of vectors (`@pytest.mark.parametrize`, `litmus_sweeps`, sidecar `sweeps:`, profile overrides) is consolidated into one matrix at collection time, and the test runs as a single pytest case. The test body iterates the matrix itself:

```python
def test_sweep(vectors, psu, dmm, logger):
    for v in vectors:
        psu.set_voltage(v["vin"])
        logger.measure("vout", dmm.measure_dc_voltage())
```

Each `for` iteration pushes the row's params + index into active state so `logger.measure`, `verify`, and `context` see the same row-scoped context they would in normal (one-case-per-row) mode. The fixture fails the test at teardown if the matrix is non-empty but the body iterated zero times.

Choose self-loop mode when an outer setup (thermal soak, supply ramp) shouldn't repeat per row; choose normal parametrize mode when you want pytest to report one case per row.

### `sync` — session

Yields a `SyncPoint` for multi-DUT coordination when running in worker mode (`_LITMUS_SLOT_ID` is set), or `None` in single-slot mode. `sync.wait(name, timeout=...)` blocks until every slot reaches the same name:

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
- [pytest-native reference](pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Models](models.md) — `Limit`, `MeasurementLimitConfig`, `ProductContext`, `StationConfig`, `FixtureConfig` field shapes
- [Test vectors & sweeps](../how-to/vector-expansion.md) — `litmus_sweeps`, `parametrize`, and the `vectors` self-loop fixture
- [Spec-driven testing](../how-to/spec-driven-testing.md) — `litmus_characteristics` + `connections` workflow
