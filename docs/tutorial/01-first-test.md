# Step 1: Run Something

**Goal:** Install Litmus and run your first test against a mock instrument.

## What You'll Build

A tiny project with one mock instrument and a passing measurement test — no hardware, and no station or part YAML yet. This is the **bench-bringup** scaffold: the smallest thing that records a real measurement.

## Install

```bash
pip install litmus-test
```

That installs the `litmus` CLI and the pytest plugin — your tests are ordinary pytest functions; the plugin adds the hardware-test pieces.

## Scaffold a project

```bash
litmus init my_project --tier=bringup
cd my_project
```

The `bringup` tier is the smallest scaffold: mock instrument fixtures in a `conftest.py`, one smoke test, and one sidecar — no station, catalog, or part YAML. It creates:

```
my_project/
├── litmus.yaml          # project config
├── pyproject.toml
├── tests/
│   ├── conftest.py      # mock dmm / psu fixtures
│   ├── test_smoke.py    # measurement tests
│   └── test_smoke.yaml  # sidecar limits
└── reports/
```

## Run it

```bash
pytest -v
```

Expected output:

```
tests/test_smoke.py::test_rail_inline PASSED
tests/test_smoke.py::test_rail_sidecar PASSED
tests/test_smoke.py::test_current_draw PASSED
```

Three measurements recorded against mock instruments, each checked against a limit.

## What's in the scaffold

The `conftest.py` defines instrument fixtures with `MagicMock` standing in for a real driver:

```python
# tests/conftest.py
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def dmm() -> MagicMock:
    """Bench DMM. Replace MagicMock with a real driver."""
    inst = MagicMock()
    inst.measure_dc_voltage.return_value = 3.3
    return inst
```

`test_smoke.py` ships three measurement tests; here's the first — it takes the `dmm` fixture and records a measurement:

```python
# tests/test_smoke.py
def test_rail_inline(dmm, verify) -> None:
    verify(
        "v_rail",
        float(dmm.measure_dc_voltage()),
        limit={"low": 3.2, "high": 3.4, "nominal": 3.3, "unit": "V"},
    )
```

`verify` is a fixture the Litmus plugin provides (installed with `litmus-test`): it records the measurement and checks it against the limit, failing the test if the value is out of band. You'll meet `verify` and limits properly in [Step 3](03-fixtures.md) and [Step 4](04-limits.md) — for now, you've run a test that captures a real measurement. Swap the `MagicMock` for a [PyVISA](https://pyvisa.readthedocs.io/) or [PyMeasure](https://pymeasure.readthedocs.io/) driver when you move to the bench; the test body doesn't change.

## About conftest.py

Right now the instruments come from `conftest.py` fixtures — the same pattern you'd use in any pytest project. Litmus doesn't need its own configuration to get started.

Later steps introduce a [station YAML](../concepts/configuration/stations.md) — one file that declares the bench's instruments. When it exists, Litmus auto-registers an instrument-role fixture for each instrument it declares (`dmm`, `psu`, …), and you delete the matching `conftest.py` fixtures. The test bodies stay the same.

## Results

Each measurement is recorded to Litmus's **run store** — the value `verify` captured, not just a pass/fail. Viewing and querying runs comes in later steps; the point for now is that the test recorded a real measurement.

## Troubleshooting

**"pytest: command not found"** — make sure `litmus-test` installed into the active environment, and if you use a virtualenv, that it's activated.

**"No tests collected"** — check the test file name starts with `test_` and each function starts with `test_`.

**"fixture 'dmm' not found"** — the fixture lives in `tests/conftest.py`, which `litmus init --tier=bringup` creates. Later steps lift the fixture into a station YAML, where the role fixture is auto-registered.

## What You Learned

- Install Litmus with `pip install litmus-test`
- Scaffold the smallest project with `litmus init --tier=bringup`
- Run measurement tests against mock instruments with `pytest`

## Continue

Next, run the same tests in Litmus's mock mode and control the returned values from config.

← [Quick Start](quickstart.md)  |  [Step 2: Mock Instruments →](02-mock-instruments.md)
