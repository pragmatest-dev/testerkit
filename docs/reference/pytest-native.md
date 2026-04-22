# pytest-native: The Three-Object Split

Litmus's pytest-native mode is the default test-authoring path going forward.
Tests are plain pytest classes (or loose module-level functions) that consume
up to three fixtures — `context`, `spec`, `logger` — each with a single,
distinct responsibility. There is no base class to inherit and no
`@litmus_test` wrapper; the plugin enforces Litmus conventions from the
outside via pytest hooks.

## The three fixtures

| Fixture  | What it holds                                  | Verbs                                       | Source                                           |
|----------|------------------------------------------------|---------------------------------------------|--------------------------------------------------|
| `context`| Vector inputs + observations                   | `get_param`, `changed`, `observe`              | Sidecar YAML `vectors:` / `@pytest.mark.parametrize` |
| `spec`   | Product characteristics → Limits + pin/fixture | `check(name, value, **conditions)`          | `--spec=products/<name>.yaml`                    |
| `logger` | Event persistence                              | `measure(name, value, limit=...)`, `record` | Always present                                   |

Data-flow rule: **test → spec → logger**. Logger reads ambient ContextVars
at write time (one-way snapshot); otherwise the three objects do not call
into each other.

## Minimum viable test

```python
from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger
from litmus.products.context import SpecContext


class TestPowerUp:
    def test_output_voltage(
        self,
        context: Context,
        psu,
        dmm,
        spec: SpecContext,
    ) -> None:
        psu.set_voltage(context.get_param("vin"))
        psu.enable_output()
        spec.check("output_voltage", dmm.measure_dc_voltage())
```

`spec.check` resolves the limit from the product YAML, calls
`logger.measure`, and raises `AssertionError` if the outcome is `FAIL`.

## Unified sidecar YAML

Each test module may have a sibling `test_<name>.yaml` with three
optional blocks:

```yaml
# test_power_board_smoke.yaml
vectors:
  vin: [4.5, 5.0, 5.5]
  load_current: [0.1, 0.4, 0.8]

limits:
  efficiency:      {low: 55,  high: 100, units: "%"}
  startup_current: {high: 50, units: "mA", comparator: LE}
  output_voltage:  {ref: "output_voltage"}   # delegates to product spec

mocks:
  dmm.measure_dc_voltage: 3.3
```

Blocks are independent — a test may use any combination, and `ref:` entries
resolve against the active `SpecContext` if one is configured.

## Limit resolution chain

When `logger.measure(name, value)` is called without an explicit `limit=`:

1. **Explicit `limit=` kwarg** — used directly
2. **Sidecar `limits:` entry** — pushed by the plugin into
   `_active_limits_var` for the running test
3. **Product spec** — `get_active_spec_context().get_limit(name)`
4. **None** — recorded as unchecked

## Native `@pytest.mark.parametrize`

`@pytest.mark.parametrize` is first-class. `context.get_param(name)` reads
`request.node.callspec.params` regardless of whether the vectors came from
sidecar YAML, a `@pytest.fixture(params=[...])` declaration, or stacked
`parametrize` markers. Range strings like `"4.5:5.5:0.5"` are accepted in
sidecar vectors.

## Implicit prereq chain

Methods within a test class run in source order. If method `test_a` fails
for any parametrize instance, subsequent method `test_b` is skipped for
all of its parametrize instances. The chain is method-level; per-case
matching via `callspec.id` is out of scope. Loose module-level
`def test_*` functions are exempt from the implicit chain.

## Duplicate-name guard

`logger.measure` maintains a per-step `seen_names` set. A second call
with the same name within one step raises `DuplicateMeasurementError`.
To stream samples under one name, pass `allow_repeat=True`:

```python
for _ in range(100):
    logger.measure("voltage_sample", dmm.measure_dc_voltage(),
                   limit=..., allow_repeat=True)
```

## Graceful degradation

The three input sources are independent. Tests work under any combination:

| Sidecar | Spec | Test shape                                                |
|---------|------|-----------------------------------------------------------|
| —       | —    | `logger.measure("v", val, limit=Limit(...))`              |
| —       | ✓    | `spec.check("output_voltage", val)`                       |
| ✓       | —    | `logger.measure("efficiency", eff)` — auto-resolves       |
| ✓       | ✓    | spec.check for characteristics; logger.measure for procedure |
| —       | —    | `assert 3.2 <= val <= 3.4` — pure pytest, no Litmus YAML  |

## Relationship to `@litmus_test`

`@litmus_test` still works and is not deprecated; the plugin supports both
authoring styles simultaneously. New work should prefer pytest-native.
