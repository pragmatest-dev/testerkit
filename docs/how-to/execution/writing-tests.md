# Writing Tests

Litmus tests are **plain pytest** — pytest classes or loose module-level functions that consume a few Litmus-provided fixtures. For everything that isn't Litmus-specific (fixtures, conftest, CLI, markers, the basics of parametrize for vanilla projects), refer to the official pytest docs at <https://docs.pytest.org/>.

## `verify` vs `measure` — pick one

Both produce identical rows on PASS. They differ only on FAIL:

- **`verify(name, value)`** — records the measurement row (value, units, limits, traceability), resolves a limit, stamps `measurement_outcome`, and **raises `AssertionError`** when the value is out of range. Same record-side effect as `measure`; the only difference is that `verify` raises on FAIL.
- **`measure(name, value)`** — records a row with `outcome = DONE` and **never raises**. Use this for characterization sweeps where you want all points captured regardless of pass/fail.

Rule of thumb: _would a fail here stop the line?_ → `verify`. Else → `measure`.

`verify` also raises `MissingLimitError` when no limit can be resolved for the measurement — markers, sidecar, profile, and part spec are all checked, and an empty result is a config bug rather than an "unchecked" path. Switch to `measure` if you intentionally want to record a value without judging it.

## The core per-test fixtures

| Fixture   | Role                                         | Typical verbs |
|-----------|----------------------------------------------|---------------|
| `context` | Ambient test context — run / UUT / station / vector params (when sweeping) / observations / fixture-connection state. Always available, whether the test is parametrized or not. | `get_param`, `changed`, `last`, `observe`, `configure` |
| `verify`  | Limit check + record + raise on FAIL         | `verify(name, value, limit=..., characteristic=...)` |
| `measure` | Record-only measurement — no judgment, never raises | `measure(name, value, limit=...)` |

Data flow is one-way: `test → spec → measurement row`. `verify` and `measure` automatically stamp each row with the active run, station, UUT, and instruments — you don't pass them in.

## Minimum viable test

```python
class TestPowerUp:
    def test_output_voltage(self, context, psu, dmm, verify):
        psu.set_voltage(context.get_param("vin"))
        psu.enable_output()
        verify("output_voltage", dmm.measure_dc_voltage())
```

`verify` resolves the limit from the part YAML, writes a measurement row, and raises `AssertionError` on fail. Instrument fixtures (`psu`, `dmm`) are auto-registered from the station config — define a same-named `conftest.py` fixture only if you need custom setup/teardown.

## Test classes are sequences

A pytest test class is a hardware-test **sequence** — a named, ordered group of methods that run together. Each class run logs its own step start/end events, with the methods nested under it. The worst result wins: a failed measurement fails its test, which fails the class, which fails the run.

It's the sequence you'd write by hand: `for each voltage: warmup → load test → cooldown`.

```python
@pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])      # class becomes the outer loop
class TestPowerSequence:
    def test_warmup(self, voltage, psu, uut):
        psu.set_voltage(voltage)
        ...
    def test_load_regulation(self, voltage, eload, dmm, verify):
        ...
    def test_cooldown(self, voltage, psu):
        psu.disable_output()
```

Result: 9 step executions in **condition-first** order — full sequence per voltage:

```
voltage=1: warmup → load_regulation → cooldown
voltage=2: warmup → load_regulation → cooldown
voltage=3: warmup → load_regulation → cooldown
```

See the [step hierarchy concepts page](../../concepts/execution/step-hierarchy.md) for the data model — how container/method/measurement events compose and how `step_path` identifies each level.

## Sweeping inputs (test vectors)

`@pytest.mark.litmus_sweeps(...)` declares one or more nested loops
that drive the test through every combination of conditions. Each
combination is one **test vector** — pytest runs the test once per
combination, and `context.get_param("name")` reads the active value:

```python
import pytest

# Single loop
@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5]}])
def test_rails(vin, context, verify, psu, dmm): ...

# Nested loops (cross-product). Top entry = outer/slowest.
@pytest.mark.litmus_sweeps([
    {"temp": [25, 85]},              # outer (slow to change)
    {"vin": [4.5, 5.0, 5.5]},        # inner (fast to change)
])
def test_rails(temp, vin, context, verify, psu, chamber, dmm): ...

# Paired values (input/expected lists step together). List lengths must match.
@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0, 5.5], "expected": [3.30, 3.31, 3.30]}])
def test_rails(vin, expected, ...): ...
```

The same shape works in YAML — operator-editable, no code change:

```yaml
# test_rails.yaml
tests:
  test_rails:
    sweeps:
      - {temp: [25, 85]}                # outer
      - {vin: [4.5, 5.0, 5.5]}          # inner
```

See the [Test Vectors guide](vector-expansion.md) for the full
shape, generators (`linspace`, `arange`, …), and the loop-ordering
note for migrating projects from `@pytest.mark.parametrize`.

### Skip expensive reconfiguration with `context.changed()`

Hardware reconfig dominates multi-parameter sweeps (PSU settle 500 ms, DMM range switch 1 s, chamber soak 5–30 min). `context.changed(key)` returns `True` only when the parameter differs from the previous test case. Pair this with the top-to-bottom outer-to-inner ordering so the slow setup only runs when it actually rolls over:

```python
@pytest.mark.litmus_sweeps([
    {"temp": [25, 85]},               # outer (3 changes)
    {"vin": [5.0, 5.5]},              # middle
    {"load": [0.1, 0.4]},             # inner
])
def test_rails(temp, vin, load, context, psu, chamber, uut_load, dmm, verify):
    if context.changed("temp"):
        chamber.set_temperature(temp)
        chamber.wait_for_soak()          # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
    uut_load.set(load)
    verify("output_voltage", dmm.measure_dc_voltage())
```

### Self-loop mode — `vectors` fixture

Sometimes you want to own the iteration yourself: amortize an expensive
per-test setup, stream samples into one measurement, or skip interior
rows conditionally. Ask for the ``vectors`` fixture in your signature
and Litmus consolidates every source (inline + sidecar + profile) into
one matrix. The test executes as **one** pytest case; you iterate the
matrix inside:

```python
@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5]}])
def test_rails_sweep(vectors, psu, dmm, verify):
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

Each iteration pushes the active row's values so `verify`, `context.changed`,
and row stamping behave the same as in parametrized mode.

## Limits

When `measure(name, value)` is called without `limit=`, resolution
walks the marker merge cascade (see *Merge cascade* below) looking for
the closest `litmus_limits` entry by measurement name, falling back to:

1. Explicit limit — `measure("v", val, limit={"low": ..., "high": ..., "unit": "V"})` (dict literal or `Limit(...)` both work)
2. Any `litmus_limits` marker (inline decorator, sidecar, profile) whose
   key matches `name`
3. Part spec via `characteristic: "<name>"` delegation
4. None — unchecked, recorded anyway (characterization mode)

```python
@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "unit": "V"},
    efficiency={"characteristic": "efficiency"},   # delegate to part spec
)
def test_rails(context, verify, measure, dmm):
    measure("output_voltage", dmm.measure_dc_voltage())
    verify("efficiency", compute_eff(...))
```

## Litmus markers

| Marker                            | Purpose                                                       |
|-----------------------------------|---------------------------------------------------------------|
| `litmus_sweeps([{argname: values, ...}, ...])` | Sweep one or more parameters across values (multiple keys in one dict = zipped; multiple dicts = cross-product) |
| `litmus_limits(**by_name)`        | Limits by measurement name (supports `when:`-keyed bands)     |
| `litmus_characteristics([<id>, ...])` | Attach the test to one or more part characteristics (limits + UUT pin auto-resolve) |
| `litmus_connections([name, ...])` or `litmus_connections(**by_instrument)` | Select fixture-connection names (positional list, like `litmus_characteristics`) or raw instrument channels (kwargs by instrument, like `litmus_limits`) |
| `litmus_mocks([{target: ..., ...}, ...])` | Patch one or more methods for the test (uses `unittest.mock.patch.object`) |
| `litmus_prompts(message=...)`      | Pause for manual operator setup before, during, or after a test |
| `litmus_retry(max_retries=N)`     | Retry on transient failure (N retries beyond original; translates to `flaky` for pytest) |

Markers can be authored three ways and all merge into the same cascade:

1. Inline Python — `@pytest.mark.litmus_limits(...)` on the method/class
2. Sidecar YAML — marker fields at file / class / per-test scope
3. Profile YAML — marker fields in a profile under `profiles/*.yaml`

For pytest's own markers and ecosystem plugins:

| Concern                    | Native / ecosystem                                          |
|----------------------------|-------------------------------------------------------------|
| Vanilla pytest sweeps      | `@pytest.mark.parametrize(...)` — kept working unchanged    |
| Test-to-test dependencies  | `@pytest.mark.dependency(...)` — `pytest-dependency`        |
| Retry transient failures   | `@pytest.mark.flaky(reruns=N)` — `pytest-rerunfailures` (or use `litmus_retry` for runner-neutral form) |

Part is session-global: pick it with `--part=<id>` (looks up
`parts/<id>.yaml`) or `--part=<path>` (explicit path). There is no
per-test part override marker.

## Binding a test to characteristics or connections

`litmus_characteristics` and `litmus_connections` select which pins/connections a test iterates over via `context.connections`. The common bindings:

- **Bind to a part characteristic** — limits and UUT pins resolve from the part spec:
  `@pytest.mark.litmus_characteristics(["output_voltage"])`
- **Bind to named fixture connections** — needs a fixture YAML so the names resolve:
  `@pytest.mark.litmus_connections(["vout_1", "vout_2"])`
- **Early bringup, before a fixture YAML exists** — bind raw instrument channels:
  `@pytest.mark.litmus_connections(dmm=["1", "2"])`

The two markers compose — a characteristic constrains which connections are valid. If you declare `litmus_connections` but never iterate `context.connections` in the test body, the test fails, so the binding can't be silently ignored. For the full resolution rules — every combination of marker presence and fixture state — see the [markers reference](../../reference/pytest/markers.md).

## Sidecar YAML

A sibling `test_<module>.yaml` adds marker config without touching code. Fields at the top apply to every test in the file; nest under `tests:` to scope to a class or method. (`runner:` and `tests:` are reserved keys; everything else is a Litmus marker name.)

```yaml
# test_power_board.yaml
limits:                                           # applied to every test in file
  output_voltage: {characteristic: output_voltage}   # delegates to part spec
mocks:
  - {target: "dmm.measure_dc_voltage", return_value: 3.3}

tests:
  TestPowerRails:                                 # class branch
    sweeps:
      - {vin: [4.5, 5.0, 5.5]}                    # outer loop
      - {load_current: [0.1, 0.4, 0.8]}           # inner loop
    tests:
      test_efficiency:                            # nested method
        limits:
          efficiency: {low: 55, high: 100, unit: "%"}

  test_standalone:                                # module-level test (leaf)
    runner:
      markers:
        - skipif: "not os.getenv('HAS_BENCH')"
```

Merge order, least → most specific: file-root → class → per-test → inline
decorators → profile chain → CLI. Later entries override earlier ones
key-by-key.

Limits that depend on the active sweep values can use a **list of
condition-indexed bands** instead of a flat dict:

```yaml
tests:
  test_rails:
    limits:
      output_voltage:
        bands:
          - {when: {vin: 5.0}, low: 3.234, high: 3.366}
          - {when: {vin: 3.3}, low: 3.1,   high: 3.5}
```

The first band whose `when:` matches the current row wins; no match raises `pytest.UsageError`. See [Test Limits → Condition-indexed bands](limits.md#condition-indexed-bands) for details.

## Structuring drivers across multiple test folders

When a project has several demo or test directories that share driver wrappers, two patterns work:

**1. `conftest.py` `sys.path` shim (fastest path).** Put drivers in a sibling folder (`project/drivers/`) and prepend the parent to `sys.path` from the test folder's `conftest.py`:

```python
# tests/conftest.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```

Tests can now `from drivers import DMM`. No packaging ceremony. This is what `examples/03-profiles/conftest.py` does.

**2. `pyproject.toml` package (stable).** Put drivers under `src/<project>/drivers/`, declare the project in `pyproject.toml`, and `pip install -e .`. Tests `from <project>.drivers import DMM`. More up-front work, but no `sys.path` surprises.

The conftest shim is the fastest route from "I have a folder of tests" to "green runs." Graduate to the pyproject layout when you need the drivers reusable across projects.

## Retries & test dependencies — use the pytest ecosystem

Litmus **does not** ship its own retry or skip-on-failure markers. Use the mature ecosystem plugins instead:

| Concern                  | Use                                                                 |
|--------------------------|---------------------------------------------------------------------|
| Retry transient failures | `@pytest.mark.flaky(reruns=N, reruns_delay=T)` — `pytest-rerunfailures` |
| Skip when a dep failed   | `@pytest.mark.dependency(depends=["test_a"])` — `pytest-dependency`     |

Tests are independent by default — there is no implicit prereq chain. Reach for `pytest-dependency` when you need explicit "if test_a fails, skip test_b" behavior.

## Duplicate-name guard

Each step rejects a duplicate measurement name: `verify("v")` followed by a stray `measure("v", ...)` raises `DuplicateMeasurementError`. For intentional repeats, opt in with `allow_repeat=True`.

## Graceful degradation

All three config sources are independent — tests work under any combination:

| Sidecar | Spec | Shape                                                          |
|---------|------|----------------------------------------------------------------|
| —       | —    | `measure("v", val, limit={"low": ..., "high": ..., "unit": "V"})` — explicit |
| —       | ✓    | `verify("output_voltage", val)`                            |
| ✓       | —    | `measure("efficiency", eff)` — auto-resolves            |
| ✓       | ✓    | `verify` for characteristics; `measure` for procedure |
| —       | —    | `assert 3.2 <= val <= 3.4` — pure pytest, no Litmus machinery  |

## Instrument access

Three shapes — all feed the same cached instances:

```python
# Auto-registered role fixture (most common)
def test_a(psu, dmm, verify): ...

# By role name via accessor
def test_b(instrument):
    dmm = instrument("dmm")

# By UUT pin (requires a fixture YAML)
def test_c(pins, verify):
    pins["VIN"].set_voltage(5.0)
    verify("output_voltage", pins["VOUT"].measure_voltage())
```

## CLI

```bash
pytest tests/ \
  --uut-serial=SN12345 \
  --station=bench_1 \
  --operator="Jane Doe" \
  --test-phase=production \
  --mock-instruments \
  -v
```

Everything else is standard pytest — see <https://docs.pytest.org/en/stable/reference/reference.html>.

## Best practices

1. Prefer `verify(name, v)` when a part spec exists — limits, UUT pin, and spec ref resolve automatically
2. Use `measure` with inline kwargs or a sidecar `litmus_limits` marker for procedure-only measurements
3. Use `context.changed()` to skip expensive reconfig across sweep iterations
4. Prefer inline `@pytest.mark.litmus_limits` for code-owned sweeps; sidecar YAML for operator-edited sweeps
5. Keep one measurement focus per test — let `litmus_sweeps` expand sweeps, not in-function loops
6. Never hardcode limits in `assert` — put them in a `litmus_limits` marker, sidecar, or part spec

## Same tests, different labs

When the same test tree needs to run under different conditions — a quick
validation sweep, a full production sweep, a debug scenario — declare
**profiles** as one-file-per-scenario under `profiles/*.yaml`. CLI facet
flags (e.g. `--test-phase=production`) select exactly one. See
[Profiles guide](profiles.md).

## Next Steps

- [Litmus fixtures](../../reference/pytest/fixtures.md) — all the fixtures with signatures and examples
- [Litmus markers](../../reference/pytest/markers.md) — the seven `litmus_*` markers
- [pytest-native reference](../../reference/overview/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Profiles](profiles.md) — named config sets for the same test tree
- [Limits guide](limits.md) — all limit forms and resolution order
- [Simulation Mode](../configuration/mock-mode.md) — running without hardware
- [Official pytest docs](https://docs.pytest.org/en/stable/) — fixtures, conftest, markers
