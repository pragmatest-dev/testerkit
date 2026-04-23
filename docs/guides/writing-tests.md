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

Any of these work and all feed `context.get_param(...)`:

```python
import pytest

# Litmus marker — stacks with native parametrize, discoverable in sidecar
@pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5], load=[0.1, 0.4, 0.8])
def test_rails(context, spec, psu, dut_load, dmm): ...

# Native pytest parametrize — first-class, no wrapping
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.parametrize("load", [0.1, 0.4, 0.8])
def test_rails(context, spec, psu, dut_load, dmm): ...

# Sidecar YAML (operator-editable, no code change) — test_<module>.yaml
# vectors:
#   vin: [4.5, 5.0, 5.5]
#   load: [0.1, 0.4, 0.8]
def test_rails(context, spec, psu, dut_load, dmm): ...
```

### Skip expensive reconfiguration with `context.changed()`

Hardware reconfig dominates multi-parameter sweeps (PSU settle 500 ms, DMM range switch 1 s, chamber soak 5–30 min). `context.changed(key)` returns `True` only when the parameter differs from the previous parametrize iteration:

```python
@pytest.mark.litmus_vectors(vin=[5.0, 5.5], temp=[25, 85], load=[0.1, 0.4])
def test_rails(context, psu, chamber, dut_load, dmm, spec):
    if context.changed("temp"):
        chamber.set_temperature(context.get_param("temp"))
        chamber.wait_for_soak()          # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(context.get_param("vin"))
    dut_load.set(context.get_param("load"))
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

## Limits

When `logger.measure(name, value)` is called without `limit=`, resolution is:

1. Explicit kwargs — `logger.measure("v", val, low=..., high=..., units=...)`
2. Method-level `@pytest.mark.litmus_limits(name={...})`
3. Class-level `litmus_limits` marker
4. Sidecar YAML `limits:` block
5. Product spec (`ref: "<name>"` delegation)
6. None — unchecked, recorded anyway (characterization mode)

```python
@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "units": "V"},
    efficiency={"ref": "efficiency"},          # delegate to product spec
)
def test_rails(context, spec, logger, dmm):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
    spec.check("efficiency", compute_eff(...))
```

## Five markers (all registered — `--strict-markers` safe)

| Marker                        | Scope         | Purpose                                            |
|-------------------------------|---------------|----------------------------------------------------|
| `litmus_vectors(**kwargs)`    | method, class | Parametrize inline (compiles to `parametrize`)     |
| `litmus_limits(**by_name)`    | method, class | Inject limits by measurement name                  |
| `litmus_spec(product="...")`  | method, class | Override session-wide spec for this test           |
| `litmus_mocks({...})`         | method, class | Patch instrument methods for the test              |
| `litmus_independent`          | method        | Opt **out** of the implicit prereq chain           |

Method-level markers merge over class-level (method wins on conflicts).

## Sidecar YAML

A sibling `test_<module>.yaml` can carry any combination of three optional blocks:

```yaml
# test_power_board.yaml
vectors:
  vin: [4.5, 5.0, 5.5]
  load_current: [0.1, 0.4, 0.8]
limits:
  efficiency:      {low: 55, high: 100, units: "%"}
  output_voltage:  {ref: "output_voltage"}    # delegates to product spec
mocks:
  dmm.measure_dc_voltage: 3.3
```

Sidecar values merge under markers — markers win on key conflicts.

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

Tests can now `from drivers import DMM`. No packaging ceremony. This is what `demo/advanced/conftest.py` does.

**2. `pyproject.toml` package (stable).** Put drivers under `src/<project>/drivers/`, declare the project in `pyproject.toml`, and `uv sync`. Tests `from <project>.drivers import DMM`. More up-front work, but no `sys.path` surprises.

The conftest shim is the fastest route from "I have a folder of tests" to "green runs." Graduate to the pyproject layout when you need the drivers reusable across projects.

## Retries & test dependencies — use the pytest ecosystem

Litmus **does not** ship its own retry or skip-on-failure markers. Use the mature ecosystem plugins instead:

| Concern                  | Use                                                                 |
|--------------------------|---------------------------------------------------------------------|
| Retry transient failures | `@pytest.mark.flaky(reruns=N, reruns_delay=T)` — `pytest-rerunfailures` |
| Skip when a dep failed   | `@pytest.mark.dependency(depends=["test_a"])` — `pytest-dependency`     |

The implicit Litmus prereq chain (in source order within a class, if `test_a` fails, `test_b` is skipped) is the zero-config default. Opt out per test with `@pytest.mark.litmus_independent`.

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
2. Use `logger.measure` with inline kwargs or sidecar `limits:` for procedure-only measurements
3. Use `context.changed()` to skip expensive reconfig across parametrize iterations
4. Prefer markers for code-owned sweeps; sidecar YAML for operator-edited sweeps
5. Keep one measurement focus per test — let parametrize expand sweeps, not in-function loops
6. Never hardcode limits in `assert` — put them in `litmus_limits`, sidecar, or the product spec

## Same tests, different labs

When the same test tree needs to run under different conditions — a quick
validation sweep, a full production sweep with retries, a debug profile —
declare **profiles** in `litmus.yaml` and select one with
`--litmus-profile=<name>`. See [Profiles guide](profiles.md).

## Next Steps

- [pytest-native reference](../reference/pytest-native.md) — concise reference card
- [Profiles](profiles.md) — named config sets for the same test tree
- [Limits guide](limits.md) — all limit forms and resolution order
- [Simulation Mode](simulation-mode.md) — running without hardware
- [Official pytest docs](https://docs.pytest.org/en/stable/) — parametrize, fixtures, conftest, markers
