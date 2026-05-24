# Writing Tests

Litmus tests are **plain pytest** — pytest classes or loose module-level functions that consume a few Litmus-provided fixtures. For everything that isn't Litmus-specific (fixtures, conftest, CLI, markers, the basics of parametrize for vanilla projects), refer to the official pytest docs at <https://docs.pytest.org/>.

## `verify` vs `logger.measure` — pick one

Both produce identical rows on PASS. They differ only on FAIL:

- **`verify(name, value)`** — records the measurement row (value, units, limits, traceability), resolves a limit, stamps `measurement_outcome`, and **raises `AssertionError`** when the value is out of range. Same record-side effect as `logger.measure`; the only difference is that `verify` raises on FAIL.
- **`logger.measure(name, value)`** — records a row with `outcome = DONE` and **never raises**. Use this for characterization sweeps where you want all points captured regardless of pass/fail.

Rule of thumb: _would a fail here stop the line?_ → `verify`. Else → `logger.measure`.

`verify` also raises `MissingLimitError` (from `litmus.execution.verify`) when no limit can be resolved for the measurement — markers, sidecar, profile, and product spec are all checked, and an empty result is a config bug rather than an "unchecked" path. Switch to `logger.measure` if you intentionally want to record a value without judging it.

## The core per-test fixtures

| Fixture   | Role                                         | Typical verbs |
|-----------|----------------------------------------------|---------------|
| `context` | Ambient test context — run / DUT / station / vector params (when sweeping) / observations / fixture-connection state. Always available, whether the test is parametrized or not. | `get_param`, `changed`, `last`, `observe`, `configure` |
| `verify`  | Limit check + record + raise on FAIL         | `verify(name, value, limit=..., characteristic=...)` |
| `logger`  | Measurement/event sink                       | `measure(name, value, ...)`, `record(k, v)` |

Data flow is one-way: `test → spec → logger`. Logger snapshots ambient [ContextVars](https://docs.python.org/3/library/contextvars.html) (Python's built-in async-safe scoped state — Litmus uses them for run id, station, DUT, active instruments) at write time.

## Minimum viable test

```python
class TestPowerUp:
    def test_output_voltage(self, context, psu, dmm, verify):
        psu.set_voltage(context.get_param("vin"))
        psu.enable_output()
        verify("output_voltage", dmm.measure_dc_voltage())
```

`verify` resolves the limit from the product YAML, writes a measurement via `logger`, and raises `AssertionError` on fail. Instrument fixtures (`psu`, `dmm`) are auto-registered from the station config — define a same-named `conftest.py` fixture only if you need custom setup/teardown.

## Test classes are sequences

A pytest test class is a hardware-test **sequence** — a named, ordered group of methods that run together. Litmus treats it as a first-class step container: each class iteration emits its own `StepStarted` / `StepEnded` events in the run log, and the methods nest underneath it. Outcomes roll up via the severity-max ladder (worst child wins), so a failed measurement inside `test_output_voltage` propagates to `TestPowerUp`'s container outcome to the run outcome.

This matches the way TestStand (National Instruments' commercial test executive), OpenTAP (Keysight's open-source test sequencer), and Spintop OpenHTF (a community OpenHTF wrapper) model test sequences — and how a hardware engineer would naturally write the equivalent pseudocode (`for each voltage: run the warmup → load test → cooldown sequence`).

```python
@pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])      # class becomes the outer loop
class TestPowerSequence:
    def test_warmup(self, voltage, psu, dut):
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

See the [step hierarchy concepts page](../concepts/execution/step-hierarchy.md) for the data model — how container/method/measurement events compose and how `step_path` / `parent_path` identify each level.

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
def test_rails(temp, vin, load, context, psu, chamber, dut_load, dmm, verify):
    if context.changed("temp"):
        chamber.set_temperature(temp)
        chamber.wait_for_soak()          # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
    dut_load.set(load)
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

When `logger.measure(name, value)` is called without `limit=`, resolution
walks the marker merge cascade (see *Merge cascade* below) looking for
the closest `litmus_limits` entry by measurement name, falling back to:

1. Explicit limit — `logger.measure("v", val, limit={"low": ..., "high": ..., "units": "V"})` (dict literal or `Limit(...)` both work)
2. Any `litmus_limits` marker (inline decorator, sidecar, profile) whose
   key matches `name`
3. Product spec via `characteristic: "<name>"` delegation
4. None — unchecked, recorded anyway (characterization mode)

```python
@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "units": "V"},
    efficiency={"characteristic": "efficiency"},   # delegate to product spec
)
def test_rails(context, verify, logger, dmm):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
    verify("efficiency", compute_eff(...))
```

## Litmus markers (`--strict-markers` safe)

| Marker                            | Purpose                                                       |
|-----------------------------------|---------------------------------------------------------------|
| `litmus_sweeps([{argname: values, ...}, ...])` | Sweep one or more parameters across values (multiple keys in one dict = zipped; multiple dicts = cross-product) |
| `litmus_limits(**by_name)`        | Limits by measurement name (supports `when:`-keyed bands)     |
| `litmus_characteristics([<id>, ...])` | Bind the test to one or more product characteristics (limits + DUT pin auto-resolve) |
| `litmus_connections([name, ...])` or `litmus_connections(**by_instrument)` | Bind to fixture-connection names (positional list, like `litmus_characteristics`) OR to raw instrument channels (kwargs by instrument, like `litmus_limits`) |
| `litmus_mocks([{target: ..., ...}, ...])` | Patch one or more methods for the test (uses `unittest.mock.patch.object`) |
| `litmus_prompts(message=...)`      | Manual operator setup at a lifecycle point                    |
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

Product is session-global: pick it with `--product=<id>` (looks up
`products/<id>.yaml`) or `--product=<path>` (explicit path). There is no
per-test product override marker.

## `litmus_characteristics` × `litmus_connections` resolution

The two markers below are the two halves of selecting which pins/connections a test iterates over: `litmus_characteristics` says *which characteristic* on the product, and `litmus_connections` says *which fixture connections* to bind. The matrix exists because they're independent — every combination of "present / absent / by-name / by-channel / fixture loaded / fixture absent" gets a defined behaviour. Skim the table for the case that matches your test.

`litmus_characteristics` and `litmus_connections` are independent markers that
compose into the iterable connection set on `ctx.connections`. `litmus_connections`
takes one of two shapes — Pydantic discriminates by structure at YAML load:

* **`connections: [name, ...]`** — bind by fixture-connection name (matches
  `litmus_characteristics`' positional-list shape). Requires a fixture YAML
  so the names resolve.
* **`connections: {instrument: channels, ...}`** — bind by instrument →
  channel selectors (matches `litmus_limits`' kwargs-by-name shape). Works
  pre-fixture-config for early bringup; synthesizes connection stubs.

Behavior depends on which markers are present and whether a fixture YAML is
loaded for the run:

| Case | `litmus_characteristics` | `litmus_connections` | Fixture loaded? | Result |
|------|---------------|----------------------|-----------------|--------|
| 1 | — | — | any | No markers → `ctx.connections` is `None`; test runs once with no connection context. |
| 2 | `characteristic: X` | — | yes | Iterate every fixture connection whose `dut_pin` (or `net`) is in `X.resolved_pins`. Fixture-order. |
| 3 | `characteristic: X` | — | no | Empty iterator (no connections to bind to). Test still iterates `ctx.connections` and gets zero rounds. |
| 4 | — | `[a, b, …]` (by name) | yes | Iterate the listed connections in user-listed order. Unknown name → `UsageError`. |
| 5 | — | `[a, b, …]` (by name) | no | `UsageError` — connection names are nonsense without a fixture YAML. |
| 6 | — | `{inst: [ch, …]}` (by channel) | yes | Match each `(inst, ch)` against fixture connections; user-listed order. No match → `UsageError`. `'all'` → all connections on that instrument. |
| 7 | — | `{inst: [ch, …]}` (by channel) | no | Synthesize `FixtureConnection` stubs (`name=f"{inst}_ch{ch}"`, no `dut_pin`). Iterable for early bringup. `'all'` → `UsageError` (nothing to enumerate). |
| 8 | `characteristic: X` | `[a, b, …]` (by name) | yes | Resolve as case 4, then validate every selected connection's `dut_pin` ∈ `X.resolved_pins`. Out-of-set → `UsageError`. User-listed order wins. |
| 9 | `characteristic: X` | `[a, b, …]` (by name) | no | `UsageError` (case 5 — fixture required for connection names). |
| 10 | `characteristic: X` | `{inst: [ch, …]}` (by channel) | yes | Resolve as case 6, then validate every match's `dut_pin` ∈ `X.resolved_pins`. Out-of-set → `UsageError`. User-listed order wins. |
| 11 | `characteristic: X` | `{inst: [ch, …]}` (by channel) | no | Synthesize stubs (case 7). No `dut_pin` mapping exists, so spec membership cannot be enforced — stubs pass through. |

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

A sibling `test_<module>.yaml` carries config in a recursive tree
mirroring pytest's `file::Class::method` node ids: file-level marker
fields plus a `tests:` dict where each entry is either a function leaf
(marker fields only) or a class branch (marker fields plus nested
`tests:` for its methods). Reserved keys at every level are `runner:`
(opaque per-runner config) and `tests:`; everything else is a Litmus
marker name.

```yaml
# test_power_board.yaml
limits:                                           # applied to every test in file
  output_voltage: {characteristic: output_voltage}   # delegates to product spec
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
          efficiency: {low: 55, high: 100, units: "%"}

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

`logger.measure` maintains a `seen_names` set per step. A second call with the same name raises `DuplicateMeasurementError` — typical trigger is `verify("v")` followed by a stray `logger.measure("v", ...)`. For intentional streaming, opt in with `allow_repeat=True`.

## Graceful degradation

All three config sources are independent — tests work under any combination:

| Sidecar | Spec | Shape                                                          |
|---------|------|----------------------------------------------------------------|
| —       | —    | `logger.measure("v", val, limit={"low": ..., "high": ..., "units": "V"})` — explicit |
| —       | ✓    | `verify("output_voltage", val)`                            |
| ✓       | —    | `logger.measure("efficiency", eff)` — auto-resolves            |
| ✓       | ✓    | `verify` for characteristics; `logger.measure` for procedure |
| —       | —    | `assert 3.2 <= val <= 3.4` — pure pytest, no Litmus machinery  |

## Instrument access

Three shapes — all feed the same cached instances:

```python
# Auto-registered role fixture (most common)
def test_a(psu, dmm, verify): ...

# By role name via accessor
def test_b(instrument):
    dmm = instrument("dmm")

# By DUT pin (requires a fixture YAML)
def test_c(pins, verify):
    pins["VIN"].set_voltage(5.0)
    verify("output_voltage", pins["VOUT"].measure_voltage())
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

1. Prefer `verify(name, v)` when a product spec exists — limits, DUT pin, and spec ref resolve automatically
2. Use `logger.measure` with inline kwargs or a sidecar `litmus_limits` marker for procedure-only measurements
3. Use `context.changed()` to skip expensive reconfig across sweep iterations
4. Prefer inline `@pytest.mark.litmus_limits` for code-owned sweeps; sidecar YAML for operator-edited sweeps
5. Keep one measurement focus per test — let `litmus_sweeps` expand sweeps, not in-function loops
6. Never hardcode limits in `assert` — put them in a `litmus_limits` marker, sidecar, or product spec

## Same tests, different labs

When the same test tree needs to run under different conditions — a quick
validation sweep, a full production sweep, a debug scenario — declare
**profiles** as one-file-per-scenario under `profiles/*.yaml`. CLI facet
flags (e.g. `--test-phase=production`) select exactly one. See
[Profiles guide](profiles.md).

## Next Steps

- [Litmus fixtures](../reference/litmus-fixtures.md) — all 20 fixtures with signatures and examples
- [Litmus markers](../reference/litmus-markers.md) — the seven `litmus_*` markers
- [pytest-native reference](../reference/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Profiles](profiles.md) — named config sets for the same test tree
- [Limits guide](limits.md) — all limit forms and resolution order
- [Simulation Mode](mock-mode.md) — running without hardware
- [Official pytest docs](https://docs.pytest.org/en/stable/) — fixtures, conftest, markers
