# pytest as the primary runner

TesterKit is a hardware test platform; the bundled pytest plugin is its primary runner integration. OpenHTF and the LabVIEW / TestStand results API are the alternatives — see [Integrations](../../integration/). Tests under the pytest path are **plain pytest** — no decorator, no base class. The plugin contributes a set of [fixtures](../../reference/pytest/fixtures.md) (`context`, `verify`, `measure` are the three a test hits every time), [seven markers](../../reference/pytest/markers.md), and a [sidecar YAML](../../reference/configuration.md); everything else is stock pytest.

The choice carries the rest of the pytest ecosystem with it. The sections below cover what pytest already gives you, what TesterKit adds on top, and why that split helps when an AI assistant writes the tests.

## Shape of a TesterKit test

```python
class TestPowerBoard:
    def test_voltage(self, context, dmm, verify):
        verify("output_voltage", dmm.measure_dc_voltage())
```

Plain pytest collection — no proprietary IDE, no test DSL. Runs with the `pytest` command, shows up in the IDE test explorer, works alongside every pytest plugin.

## What stock pytest provides

- **Test discovery and selection** — `pytest -k`, `-m`, node IDs, `--lf`/`--ff` for last-failed / failures-first
- **Markers for classification** — `@pytest.mark.smoke`, `@pytest.mark.slow`, etc.; TesterKit adds hardware-specific markers on top
- **Fixtures** — `yield`-based setup/teardown, scope resolution, automatic composition
- **Parametrize** — `@pytest.mark.parametrize` first-class (`context.get_param(...)` works on it directly)
- **Rich failure output** — assertion rewriting, stack traces, `--tb` control
- **Plugin ecosystem** — `pytest-xdist` (parallel), `pytest-timeout`, `pytest-rerunfailures` (retries), `pytest-dependency`, `pytest-html`
- **IDE integration** — click-to-run, breakpoints, inline results in PyCharm / VS Code
- **CI/CD** — `pytest --junitxml=...`, exit codes, `pytest-html`

## What TesterKit adds on top

| Concern                           | TesterKit addition                                      |
|-----------------------------------|------------------------------------------------------|
| Measurement/event persistence     | `measure(name, v, ...)` → parquet, traceable  |
| Part-spec-driven limits + pins | `verify(name, v)` resolves from part YAML     |
| Vector parameters + change detect | `context.get_param(k)`, `context.changed(k)`         |
| Operator-editable sweeps          | Sidecar `test_<module>.yaml` `sweeps:` overrides     |
| Instrument role fixtures          | Station config → `dmm`, `psu`, `scope` auto-fixtures |
| Mock mode                         | `--mock-instruments`, sidecar `mocks:`, `pytest-mock` |
| Session flags                     | `--station`, `--part`, `--operator`, `--uut-serial`, `--test-phase` |
| Per-test-imposed limits           | `@pytest.mark.testerkit_limits(name={...})`             |

Retries and explicit test dependencies are **ecosystem plugins**, not TesterKit additions — use `@pytest.mark.flaky(reruns=N)` (`pytest-rerunfailures`) and `@pytest.mark.dependency(depends=[...])` (`pytest-dependency`).

## Implication for AI-assisted authoring

AI assistants already know pytest well, so an assistant only has to learn TesterKit's added vocabulary on top — the [fixtures](../../reference/pytest/fixtures.md) (most often `context`, `verify`, `measure`, `pins`, `instruments`, plus the per-instrument-role fixtures from the active station) and the [seven markers](../../reference/pytest/markers.md) (`testerkit_limits`, `testerkit_sweeps`, `testerkit_mocks`, `testerkit_characteristics`, `testerkit_connections`, `testerkit_retry`, `testerkit_prompts`). A custom test runner would have to be taught from scratch.

## See also

- [Writing Tests](../../how-to/execution/writing-tests.md) — end-to-end patterns
- [TesterKit fixtures](../../reference/pytest/fixtures.md) — all the plugin fixtures
- [TesterKit markers](../../reference/pytest/markers.md) — the seven `testerkit_*` markers
- [pytest-native reference](../../reference/overview/pytest-native.md) — how TesterKit tests use pytest's own collection / fixtures / markers
- [pytest docs](https://docs.pytest.org/en/stable/) — official reference for everything that isn't TesterKit-specific
