# Step 2: Running Without Hardware

**Goal:** Run your tests without real instruments by wrapping your driver classes with TesterKit's `Mock` helper.

In step 1 you wrote vanilla pytest tests against `psu` and `dmm` fixtures defined in `conftest.py`. This step shows the smallest change that lets the same tests run on a laptop with no hardware attached.

## The conftest pattern

Your `conftest.py` already returns driver instances. Wrap them in `testerkit.instruments.mocks.Mock` when the `--mock-instruments` flag is set:

```python
# tests/conftest.py
import pytest
from drivers import DMM, PSU
from testerkit import Mock


@pytest.fixture(scope="session")
def psu(mock_instruments) -> PSU:
    if mock_instruments:
        return Mock(PSU, measure_voltage=5.0, measure_current=0.042)
    return PSU(resource="TCPIP::192.168.1.101::INSTR")


@pytest.fixture(scope="session")
def dmm(mock_instruments) -> DMM:
    if mock_instruments:
        return Mock(DMM, measure_dc_voltage=3.31)
    return DMM(resource="TCPIP::192.168.1.102::INSTR")
```

`mock_instruments` is a fixture TesterKit provides — it returns `True` whenever `--mock-instruments` is on the command line or `TESTERKIT_MOCK_INSTRUMENTS=1` is set.

`Mock(DMM, measure_dc_voltage=3.31)` returns a stand-in `DMM` — every method call does nothing and returns `None` unless you give it a return value. Pass a literal for a constant reading, a `dict` to map an argument value to a reading, or a function for dynamic behavior.

This is exactly what [`examples/01-vanilla`](https://github.com/pragmatest-dev/testerkit/tree/main/examples/01-vanilla) and [`examples/02-verify`](https://github.com/pragmatest-dev/testerkit/tree/main/examples/02-verify) ship.

## Running with mocks

```bash
pytest tests/ --mock-instruments -v
```

Same test code as step 1, no hardware required.

```bash
# Or via env var
TESTERKIT_MOCK_INSTRUMENTS=1 pytest tests/ -v
```

## Mock cheat sheet

```python
# Constant return
dmm = Mock(DMM, measure_dc_voltage=3.31)

# Map by argument — different return per query string
dmm = Mock(DMM, query={"MEAS:VOLT:DC?": "3.300", "*IDN?": "Keysight,34461A,..."})

# Callable — for noise or sweeps
import random
dmm = Mock(DMM, measure_dc_voltage=lambda: 3.3 + random.gauss(0, 0.005))
```

Every method you don't configure does nothing and returns `None`. Reading an attribute you never configured raises an `AttributeError` instead — so a missing mock value fails loudly rather than silently returning nothing.

## Mocks vs real hardware

| You run | `mock_instruments` is | Test code |
|---|---|---|
| `pytest tests/` | `False` | identical |
| `pytest tests/ --mock-instruments` | `True` | identical |

The point of the wrap-in-conftest pattern: **the test code is the same on a laptop and on the bench**. Tests don't know which mode they're in.

## What you learned

- `--mock-instruments` flag + the `mock_instruments` fixture
- `Mock(DriverClass, method=return_value, ...)` wraps any driver class
- The conftest fixture decides real vs mock — tests don't change

In later steps you'll move this real-vs-mock choice out of `conftest.py` and into station YAML (step 7), so one setting serves a whole bench of tests. For now, conftest is enough.

## Continue

Now let's adopt three of [TesterKit's per-test fixtures](../reference/pytest/fixtures.md) — `context`, `verify`, `measure` — to start recording measurements with limits.

← [Step 1: Run Something](01-first-test.md)  |  [Step 3: pytest-native tests →](03-fixtures.md)
