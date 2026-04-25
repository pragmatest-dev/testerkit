# Writing Tests

Litmus tests are **plain pytest**. There is no Litmus base class, no `@litmus_test` decorator — just pytest classes or loose module-level functions that consume a few Litmus-provided fixtures. For everything that isn't Litmus-specific (parametrize, fixtures, conftest, CLI, markers), refer to the official pytest docs at <https://docs.pytest.org/>.

## `verify` vs `logger.measure` — pick one

Both produce identical rows on PASS. They differ only on FAIL:

- **`verify(name, value)`** — resolves a limit, stamps `outcome`, and **raises `LimitFailure`** when the value is out of range. Use this when a fail should stop the line.
- **`logger.measure(name, value)`** — records a row with `outcome = DONE` and **never raises**. Use this for characterization sweeps where you want all points captured regardless of pass/fail.

Rule of thumb: _would a fail here stop the line?_ → `verify`. Else → `logger.measure`.

## The three fixtures

| Fixture   | Role                                         | Typical verbs |
|-----------|----------------------------------------------|---------------|
| `context` | Vector inputs + run/dut/station metadata     | `get_param`, `changed`, `last`, `observe` |
| `spec`    | Product characteristics → limits + pin info  | `check(name, value, **conditions)` |
| `logger`  | Measurement/event sink                       | `measure(name, value, ...)`, `record(k, v)` |

Data flow is one-way: `test → spec → logger`. Logger snapshots ambient ContextVars (run id, station, DUT, active instruments) at write time.

## Minimum viable test

```python
class TestPowerUp:
    def test_output_voltage(self, context, psu, dmm, spec):
        psu.set_voltage(context.get_param("vin"))
        psu.enable_output()
        spec.check("output_voltage", dmm.measure_dc_voltage())
```

`spec.check` resolves the limit from the product YAML, writes a measurement via `logger`, and raises `AssertionError` on fail. Instrument fixtures (`psu`, `dmm`) are auto-registered from the station config — define a same-named `conftest.py` fixture only if you need custom setup/teardown.

## Parametrizing a sweep

Two mechanisms, both feed `context.get_param(...)`:

```python
import pytest

# Native pytest parametrize — first-class, no wrapping
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.parametrize("load", [0.1, 0.4, 0.8])
def test_rails(context, spec, psu, dut_load, dmm): ...

# Sidecar YAML (operator-editable, no code change) — test_<module>.yaml
# tests:
#   test_rails:
#     markers:
#       - parametrize: ["vin", [4.5, 5.0, 5.5]]
#       - parametrize: ["load", [0.1, 0.4, 0.8]]
def test_rails(context, spec, psu, dut_load, dmm): ...
```

### Skip expensive reconfiguration with `context.changed()`

Hardware reconfig dominates multi-parameter sweeps (PSU settle 500 ms, DMM range switch 1 s, chamber soak 5–30 min). `context.changed(key)` returns `True` only when the parameter differs from the previous parametrize iteration:

```python
@pytest.mark.parametrize("load", [0.1, 0.4])
@pytest.mark.parametrize("temp", [25, 85])
@pytest.mark.parametrize("vin", [5.0, 5.5])
def test_rails(vin, temp, load, context, psu, chamber, dut_load, dmm, spec):
    if context.changed("temp"):
        chamber.set_temperature(temp)
        chamber.wait_for_soak()          # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
    dut_load.set(load)
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

### Self-loop mode — `vectors` fixture

Sometimes you want to own the iteration yourself: amortize an expensive
per-test setup, stream samples into one measurement, or skip interior
rows conditionally. Ask for the ``vectors`` fixture in your signature
and Litmus consolidates **every** parameter source (native parametrize,
sidecar `parametrize:` markers, profile markers) into a single matrix.
The test executes as **one** pytest case; you iterate the matrix inside:

```python
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_rails_sweep(vectors, psu, dmm, verify):
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

Each iteration pushes the active row's params so `verify`, `context.changed`,
and row stamping (`meas_vector_index`, `in_*` columns) behave the same
as in parametrize mode.

## Limits

When `logger.measure(name, value)` is called without `limit=`, resolution
walks the marker merge cascade (see *Merge cascade* below) looking for
the closest `litmus_limits` entry by measurement name, falling back to:

1. Explicit kwargs — `logger.measure("v", val, low=..., high=..., units=...)`
2. Any `litmus_limits` marker (inline decorator, sidecar, profile) whose
   key matches `name`
3. Product spec via `ref: "<name>"` delegation
4. None — unchecked, recorded anyway (characterization mode)

```python
@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "units": "V"},
    efficiency={"ref": "efficiency"},          # delegate to product spec
)
def test_rails(context, spec, logger, dmm):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
    spec.check("efficiency", compute_eff(...))
```

## Five Litmus markers (`--strict-markers` safe)

| Marker                            | Purpose                                                       |
|-----------------------------------|---------------------------------------------------------------|
| `litmus_limits(**by_name)`        | Limits by measurement name (supports `when:`-keyed bands)     |
| `litmus_spec(characteristic=...)` | Bind the test to a product characteristic (spec context)      |
| `litmus_connections(...)`         | Bind to explicit fixture connections or instrument-channel ranges |
| `litmus_mock(target=..., ...)`    | Patch a method for the test (routes through `pytest-mock`)    |
| `litmus_prompt(message=...)`      | Manual operator setup at a lifecycle point                    |

Markers can be authored three ways and all merge into the same cascade:

1. Inline Python — `@pytest.mark.litmus_limits(...)` on the method/class
2. Sidecar YAML — `markers:` list at file / class / per-test scope
3. Profile YAML — `markers:` list in a profile under `profiles/*.yaml`

For everything else, use native pytest primitives or ecosystem plugins:

| Concern                    | Native / ecosystem                                          |
|----------------------------|-------------------------------------------------------------|
| Parametrize                | `@pytest.mark.parametrize(...)` or sidecar `parametrize:`   |
| Test-to-test dependencies  | `@pytest.mark.dependency(...)` — `pytest-dependency`        |
| Retry transient failures   | `@pytest.mark.flaky(reruns=N)` — `pytest-rerunfailures`     |

Product is session-global: pick it with `--product=<id>` or set
`default_product:` in `litmus.yaml` / a profile. There is no per-test
product override marker.

## `litmus_spec` × `litmus_connections` resolution

`litmus_spec` and `litmus_connections` are independent markers that
compose into the iterable connection set on `ctx.connections`. Behavior depends
on which markers are present and whether a fixture YAML is loaded for
the run:

| Case | `litmus_spec` | `litmus_connections` | Fixture loaded? | Result |
|------|---------------|----------------------|-----------------|--------|
| 1 | — | — | any | No markers → `ctx.connections` is `None`; test runs once with no connection context. |
| 2 | `characteristic: X` | — | yes | Iterate every fixture connection whose `dut_pin` (or `net`) is in `X.resolved_pins`. Fixture-order. |
| 3 | `characteristic: X` | — | no | Empty iterator (no connections to bind to). Test still iterates `ctx.connections` and gets zero rounds. |
| 4 | — | `connections: [a, b, …]` | yes | Iterate the listed connections in user-listed order. Unknown name → `UsageError`. |
| 5 | — | `connections: [a, b, …]` | no | `UsageError` — connection names are nonsense without a fixture YAML. |
| 6 | — | `instrument_channels: {inst: [ch, …]}` | yes | Match each `(inst, ch)` against fixture connections; user-listed order. No match → `UsageError`. `'all'` → all connections on that instrument. |
| 7 | — | `instrument_channels: {inst: [ch, …]}` | no | Synthesize `FixtureConnection` stubs (`name=f"{inst}_ch{ch}"`, no `dut_pin`). Iterable for early bringup. `'all'` → `UsageError` (nothing to enumerate). |
| 8 | `characteristic: X` | `connections: [a, b, …]` | yes | Resolve as case 4, then validate every selected connection's `dut_pin` ∈ `X.resolved_pins`. Out-of-set → `UsageError`. User-listed order wins. |
| 9 | `characteristic: X` | `connections: [a, b, …]` | no | `UsageError` (case 5 — fixture required for connection names). |
| 10 | `characteristic: X` | `instrument_channels: {…}` | yes | Resolve as case 6, then validate every match's `dut_pin` ∈ `X.resolved_pins`. Out-of-set → `UsageError`. User-listed order wins. |
| 11 | `characteristic: X` | `instrument_channels: {…}` | no | Synthesize stubs (case 7). No `dut_pin` mapping exists, so spec membership cannot be enforced — stubs pass through. |

Invariants across the matrix:

- **Missing spec context** (cases 2/3/8/10/11 with no product loaded): `UsageError`.
- **Unknown characteristic** on the product: `UsageError`.
- **Iteration order**: when `litmus_connections` is present, follows the
  user-listed order; spec-only (case 2) follows fixture iteration order.
- **Zero remaining connections** after spec × connections filtering:
  `UsageError` (no silent skip).
- **Declared but un-iterated**: if `litmus_connections` is present and the test body
  never iterates `ctx.connections`, the test fails with `AssertionError`.

## Sidecar YAML

A sibling `test_<module>.yaml` carries markers in a recursive tree
mirroring pytest's `file::Class::method` node ids: a file-level
`markers:` list plus a `tests:` dict where each entry is either a
function leaf (`markers:` only) or a class branch (`markers:` plus
nested `tests:` for its methods).

```yaml
# test_power_board.yaml
markers:                                          # applied to every test in file
  - litmus_limits:
      output_voltage: {ref: output_voltage}       # delegates to product spec
  - litmus_mock: {target: "dmm.measure_dc_voltage", return_value: 3.3}

tests:
  TestPowerRails:                                 # class branch
    markers:
      - parametrize: ["vin", [4.5, 5.0, 5.5]]
      - parametrize: ["load_current", [0.1, 0.4, 0.8]]
    tests:
      test_efficiency:                            # nested method
        markers:
          - litmus_limits:
              efficiency: {low: 55, high: 100, units: "%"}

  test_standalone:                                # module-level test (leaf)
    markers:
      - skipif: "not os.getenv('HAS_BENCH')"
```

Merge order, least → most specific: file-root → class → per-test → inline
decorators → profile chain → CLI. Later markers override earlier ones
key-by-key.

Limits that depend on the active parametrize values can use a **list of
condition-indexed bands** instead of a flat dict:

```yaml
tests:
  test_rails:
    markers:
      - litmus_limits:
          output_voltage:
            - when: {vin: 5.0}
              low: 3.234
              high: 3.366
            - when: {vin: 3.3}
              low: 3.1
              high: 3.5
```

The first band whose `when:` matches the current row wins; no match raises `pytest.UsageError`. See [Test Limits → Condition-indexed bands](limits.md#condition-indexed-bands-when) for details.

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

**2. `pyproject.toml` package (stable).** Put drivers under `src/<project>/drivers/`, declare the project in `pyproject.toml`, and `uv sync`. Tests `from <project>.drivers import DMM`. More up-front work, but no `sys.path` surprises.

The conftest shim is the fastest route from "I have a folder of tests" to "green runs." Graduate to the pyproject layout when you need the drivers reusable across projects.

## Retries & test dependencies — use the pytest ecosystem

Litmus **does not** ship its own retry or skip-on-failure markers. Use the mature ecosystem plugins instead:

| Concern                  | Use                                                                 |
|--------------------------|---------------------------------------------------------------------|
| Retry transient failures | `@pytest.mark.flaky(reruns=N, reruns_delay=T)` — `pytest-rerunfailures` |
| Skip when a dep failed   | `@pytest.mark.dependency(depends=["test_a"])` — `pytest-dependency`     |

Tests are independent by default — there is no implicit prereq chain. Reach for `pytest-dependency` when you need explicit "if test_a fails, skip test_b" behavior.

## Duplicate-name guard

`logger.measure` maintains a `seen_names` set per step. A second call with the same name raises `DuplicateMeasurementError` — typical trigger is `spec.check("v")` followed by a stray `logger.measure("v", ...)`. For intentional streaming, opt in with `allow_repeat=True`.

## Graceful degradation

All three config sources are independent — tests work under any combination:

| Sidecar | Spec | Shape                                                          |
|---------|------|----------------------------------------------------------------|
| —       | —    | `logger.measure("v", val, low=..., high=...)` — explicit       |
| —       | ✓    | `spec.check("output_voltage", val)`                            |
| ✓       | —    | `logger.measure("efficiency", eff)` — auto-resolves            |
| ✓       | ✓    | `spec.check` for characteristics; `logger.measure` for procedure |
| —       | —    | `assert 3.2 <= val <= 3.4` — pure pytest, no Litmus machinery  |

## Instrument access

Three shapes — all feed the same cached instances:

```python
# Auto-registered role fixture (most common)
def test_a(psu, dmm, spec): ...

# By role name via accessor
def test_b(instrument):
    dmm = instrument("dmm")

# By DUT pin (requires a fixture YAML)
def test_c(pins, spec):
    pins["VIN"].set_voltage(5.0)
    spec.check("output_voltage", pins["VOUT"].measure_voltage())
```

## CLI

```bash
pytest tests/ \
  --dut-serial=SN12345 \
  --station=bench_1 \
  --operator="Jane Doe" \
  --test-phase=production \
  --mock-instruments \
  -v
```

Everything else is standard pytest — see <https://docs.pytest.org/en/stable/reference/reference.html>.

## Best practices

1. Prefer `spec.check(name, v)` when a product spec exists — limits, DUT pin, and spec ref resolve automatically
2. Use `logger.measure` with inline kwargs or a sidecar `litmus_limits` marker for procedure-only measurements
3. Use `context.changed()` to skip expensive reconfig across parametrize iterations
4. Prefer inline `@pytest.mark.litmus_limits` for code-owned sweeps; sidecar YAML for operator-edited sweeps
5. Keep one measurement focus per test — let parametrize expand sweeps, not in-function loops
6. Never hardcode limits in `assert` — put them in a `litmus_limits` marker, sidecar, or product spec

## Same tests, different labs

When the same test tree needs to run under different conditions — a quick
validation sweep, a full production sweep, a debug scenario — declare
**profiles** as one-file-per-scenario under `profiles/*.yaml`. CLI facet
flags (e.g. `--test-phase=production`) select exactly one. See
[Profiles guide](profiles.md).

## Next Steps

- [pytest-native reference](../reference/pytest-native.md) — concise reference card
- [Profiles](profiles.md) — named config sets for the same test tree
- [Limits guide](limits.md) — all limit forms and resolution order
- [Simulation Mode](simulation-mode.md) — running without hardware
- [Official pytest docs](https://docs.pytest.org/en/stable/) — parametrize, fixtures, conftest, markers
