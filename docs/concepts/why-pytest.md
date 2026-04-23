# Why pytest?

Litmus uses pytest as its test runner. Tests are **plain pytest** — no decorator, no base class. Litmus contributes three fixtures (`context`, `spec`, `logger`), a handful of markers, and a sidecar YAML; everything else is stock pytest.

This page explains what you get for free — features you'd otherwise build and maintain yourself. For the pytest fundamentals (discovery, markers, fixtures, parametrize, CLI), the official docs at <https://docs.pytest.org/> are authoritative.

## You already know the basics

```python
class TestPowerBoard:
    def test_voltage(self, context, dmm, spec):
        spec.check("output_voltage", dmm.measure_dc_voltage())
```

No proprietary IDE. No new test language. No vendor lock-in. Runs with `pytest`, shows up in the IDE test explorer, works with every pytest plugin.

## What pytest handles for you (free)

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
| Product-spec-driven limits + pins | `spec.check(name, v)` resolves from product YAML     |
| Vector parameters + change detect | `context.get_param(k)`, `context.changed(k)`         |
| Operator-editable sweeps          | Sidecar `test_<module>.yaml` `vectors:` overrides    |
| Instrument role fixtures          | Station config → `dmm`, `psu`, `scope` auto-fixtures |
| Mock mode                         | `--mock-instruments`, sidecar `mocks:`, `pytest-mock` |
| Session flags                     | `--station`, `--product`, `--operator`, `--dut-serial`, `--test-phase` |
| Per-test-imposed limits           | `@pytest.mark.litmus_limits(name={...})`             |

Retries and explicit test dependencies are **ecosystem plugins**, not Litmus additions — use `@pytest.mark.flaky(reruns=N)` (`pytest-rerunfailures`) and `@pytest.mark.dependency(depends=[...])` (`pytest-dependency`).

## Why this matters for AI assistants

When an AI writes or debugs your tests, pytest is the framework it knows best. LLMs have read the pytest docs thousands of times. Custom sequencers require teaching the tool the API from scratch every time. By building on pytest, Litmus inherits all of that training for free — the only things the AI needs to learn are the three fixtures (`context`, `spec`, `logger`) and one marker (`litmus_limits`).

## Next steps

- [Writing Tests](../guides/writing-tests.md) — end-to-end patterns
- [pytest-native reference](../reference/pytest-native.md) — fixture card
- [pytest docs](https://docs.pytest.org/en/stable/) — official reference for everything that isn't Litmus-specific
