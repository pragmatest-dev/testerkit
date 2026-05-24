# pytest as the primary runner

Litmus is a hardware test platform; the bundled pytest plugin is its primary runner integration. OpenHTF and the LabVIEW / TestStand results API are the alternatives — see [Integrations](../../integration/). Tests under the pytest path are **plain pytest** — no decorator, no base class. The plugin contributes [20 fixtures](../../reference/pytest/fixtures.md) (`context`, `verify`, `logger` are the three a test hits every time), [seven markers](../../reference/pytest/markers.md), and a [sidecar YAML](../../reference/configuration.md); everything else is stock pytest.

The choice carries the rest of the pytest ecosystem with it. The sections below name what pytest already provides (so the platform doesn't reimplement it), what the platform adds on top, and why this division benefits AI-assisted authoring.

## Shape of a Litmus test

```python
class TestPowerBoard:
    def test_voltage(self, context, dmm, verify):
        verify("output_voltage", dmm.measure_dc_voltage())
```

Plain pytest collection — no proprietary IDE, no test DSL. Runs with the `pytest` command, shows up in the IDE test explorer, works alongside every pytest plugin.

## What stock pytest provides

- **Test discovery and selection** — `pytest -k`, `-m`, node IDs, `--lf`/`--ff` for last-failed / failures-first
- **Markers for classification** — `@pytest.mark.smoke`, `@pytest.mark.slow`, etc.; Litmus adds hardware-specific flags on top
- **Fixtures** — `yield`-based setup/teardown, scope resolution, automatic composition
- **Parametrize** — `@pytest.mark.parametrize` first-class (`context.get_param(...)` works on it directly)
- **Rich failure output** — assertion rewriting, stack traces, `--tb` control
- **Plugin ecosystem** — `pytest-xdist` (parallel), `pytest-timeout`, `pytest-rerunfailures` (retries), `pytest-dependency`, `pytest-html`
- **IDE integration** — click-to-run, breakpoints, inline results in PyCharm / VS Code
- **CI/CD** — `pytest --junitxml=...`, exit codes, `pytest-html`

## What Litmus adds on top

| Concern                           | Litmus addition                                      |
|-----------------------------------|------------------------------------------------------|
| Measurement/event persistence     | `logger.measure(name, v, ...)` → parquet, traceable  |
| Product-spec-driven limits + pins | `verify(name, v)` resolves from product YAML     |
| Vector parameters + change detect | `context.get_param(k)`, `context.changed(k)`         |
| Operator-editable sweeps          | Sidecar `test_<module>.yaml` `sweeps:` overrides     |
| Instrument role fixtures          | Station config → `dmm`, `psu`, `scope` auto-fixtures |
| Mock mode                         | `--mock-instruments`, sidecar `mocks:`, `pytest-mock` |
| Session flags                     | `--station`, `--product`, `--operator`, `--dut-serial`, `--test-phase` |
| Per-test-imposed limits           | `@pytest.mark.litmus_limits(name={...})`             |

Retries and explicit test dependencies are **ecosystem plugins**, not Litmus additions — use `@pytest.mark.flaky(reruns=N)` (`pytest-rerunfailures`) and `@pytest.mark.dependency(depends=[...])` (`pytest-dependency`).

## Implication for AI-assisted authoring

LLMs are trained on the pytest documentation and on the millions of public test suites that use it. By riding on pytest, the platform inherits that training: an AI assistant only has to learn Litmus's added vocabulary on top — the [20 fixtures](../../reference/pytest/fixtures.md) (most often `context`, `verify`, `logger`, `pins`, `instruments`, plus the per-instrument-role fixtures from the active station) and the [seven markers](../../reference/pytest/markers.md) (`litmus_limits`, `litmus_sweeps`, `litmus_mocks`, `litmus_characteristics`, `litmus_connections`, `litmus_retry`, `litmus_prompts`). A custom test runner would have to be taught from scratch.

## See also

- [Writing Tests](../../how-to/execution/writing-tests.md) — end-to-end patterns
- [Litmus fixtures](../../reference/pytest/fixtures.md) — all 20 plugin fixtures
- [Litmus markers](../../reference/pytest/markers.md) — the seven `litmus_*` markers
- [pytest-native reference](../../reference/overview/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [pytest docs](https://docs.pytest.org/en/stable/) — official reference for everything that isn't Litmus-specific
