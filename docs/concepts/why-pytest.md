# Why pytest?

Litmus uses pytest as its test runner. This page explains what you get for free — features you'd otherwise build and maintain yourself.

## You already know the basics

If you've written Python tests before, you can write Litmus tests today. A `@litmus_test` function is still a pytest test function — it runs with `pytest`, shows up in your IDE's test explorer, and works with every pytest plugin.

```python
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_voltage()
```

No proprietary IDE. No new test language. No vendor lock-in.

## What pytest handles for you

### Test discovery and selection

pytest finds tests automatically — scan for `test_*.py` files, collect `test_` functions. No registration, no manifest.

More importantly, you can select tests without editing code:

```bash
pytest tests/test_power.py::test_voltage_accuracy   # One test
pytest -k "voltage and not transient"                # Keyword match
pytest -m smoke                                      # By marker
pytest -m "not calibration"                          # Exclude slow tests
```

In TestStand, filtering means editing sequence files. In pytest, it's a flag.

### Markers for classification

Tag tests with metadata without changing test logic:

```python
@pytest.mark.smoke
@litmus_test(limits_from="product")
def test_power_on(context, psu):
    ...

@pytest.mark.calibration
@litmus_test(limits_from="product")
def test_full_cal(context, psu, dmm):
    ...
```

`pytest -m smoke` for quick validation, `pytest -m calibration` for full runs. Litmus adds hardware-specific options (`--station`, `--product`, `--test-phase`) on top.

### Fixtures for resource lifecycle

Fixtures handle setup/teardown with guaranteed cleanup, even after crashes:

```python
@pytest.fixture(scope="session")
def power_supply(instruments):
    ps = instruments["psu"]
    ps.output_on()
    yield ps
    ps.output_off()
```

The `yield` pattern means instruments never get left in unknown states. Fixtures compose — a `dut_powered` fixture can depend on `power_supply` and `fixture_connected`, and pytest resolves the graph automatically.

Litmus auto-registers station instrument roles as fixtures, so most tests don't need any fixture boilerplate at all.

### Parametrize

`pytest.mark.parametrize` is the standard way to run a test across multiple conditions:

```python
@pytest.mark.parametrize("voltage", [1.8, 2.5, 3.3, 5.0])
@litmus_test
def test_regulation(context, psu, dmm):
    psu.set_voltage(voltage)
    return dmm.measure_voltage()
```

Each variant runs as a separate test item within the same test run. This works, but parametrize parameters are opaque to Litmus — they don't appear in results as input columns and aren't available via `context.get_param()`.

For full traceability, Litmus vectors are the structured alternative:

```yaml
# sequences/power.yaml
steps:
  - id: regulation
    test: tests/test_power.py::test_regulation
    vectors:
      expand: product
      voltage: [1.8, 2.5, 3.3, 5.0]
```

```python
@litmus_test
def test_regulation(context, psu, dmm):
    psu.set_voltage(context.get_param("voltage"))
    return dmm.measure_voltage()
```

With vectors, each parameter is logged to results as an `in_*` column, paired with measurements, and available for change detection (`context.changed("temperature")` to skip redundant setup). Vectors also support per-vector retry and limit checking.

Both approaches work. `parametrize` is familiar and requires no new API. Vectors add traceability and structured data at the cost of learning `context`.

### Rich failure output

When tests fail, pytest shows exactly what went wrong:

```
FAILED test_power.py::test_voltage_accuracy
    assert 3.42 == pytest.approx(3.3, abs=0.05)
    E     assert 3.42 ± 5.0e-02 == 3.3 ± 5.0e-02
    E       absolute difference: 0.12
    E       tolerance: 0.05
```

No custom assertion messages needed. pytest rewrites assertions at import time to capture intermediate values.

### Plugin ecosystem

The pytest ecosystem has plugins for everything. Ones relevant to hardware testing:

| Plugin | What it does |
|--------|-------------|
| `pytest-xdist` | Parallel execution across cores/machines |
| `pytest-timeout` | Kill hung tests (instrument not responding) |
| `pytest-repeat` | Run a test N times (reliability/burn-in) |
| `pytest-benchmark` | Performance benchmarking with statistics |
| `pytest-html` | HTML test reports |
| `pytest-rerunfailures` | Automatic retry on flaky hardware |

Install with `pip install`, they work alongside Litmus with no configuration.

### CLI you don't have to build

```bash
pytest -x                  # Stop on first failure
pytest -s                  # Show print output
pytest -v                  # Verbose with test names
pytest --lf                # Re-run last failures only
pytest --ff                # Failures first, then the rest
pytest --durations=10      # Show 10 slowest tests
```

Litmus adds `--station`, `--product`, `--operator`, `--dut-serial`, `--test-phase`, and `--mock-instruments` alongside these.

### IDE integration

PyCharm, VS Code, vim — all have built-in pytest support. Click to run, set breakpoints, see inline results. No custom tooling.

## What you'd have to build yourself

| Concern | Custom sequencer | pytest + Litmus |
|---------|-----------------|-----------------|
| Test discovery | Manual registration or XML | Automatic file scanning |
| Test selection | Custom UI or config edits | `-k`, `-m`, node IDs |
| Resource lifecycle | Custom try/finally everywhere | Fixtures with `yield` |
| Failure diagnostics | `print()` debugging | Assertion introspection |
| Reporting | Build from scratch | Plugins (HTML, JUnit, Allure) |
| IDE support | None or custom | Built in everywhere |
| CI/CD | Custom integration | `pytest --junitxml=results.xml` |
| AI assistance | Teach your API every time | LLMs know pytest already |

The last row matters more than it seems. When an AI assistant writes or debugs your tests, pytest is the framework it knows best. Custom sequencers require teaching the tool your API from scratch.

## Next steps

- [Writing Tests](../guides/writing-tests.md) — Patterns and best practices
- [pytest-native Reference](../reference/pytest-native.md) — The three-object split (`context` / `spec` / `logger`) and `LitmusSequence`
- [pytest Plugin Reference](../reference/pytest-plugin.md) — Full plugin documentation (includes `@litmus_test` decorator)
