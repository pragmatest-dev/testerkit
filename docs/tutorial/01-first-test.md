# Step 1: Run Something

**Goal:** Write and run your first Litmus test.

## What You'll Build

A simple test that passes. Nothing fancy yet — just getting the basics working.

## Prerequisites

```bash
# Clone and install
git clone https://github.com/pragmatest-dev/litmus.git
cd litmus
uv sync
```

## The Code

Create a test file:

```python
# tests/test_hello.py

def test_litmus_works():
    """Verify Litmus is installed and pytest runs."""
    assert True
```

Run it:

```bash
pytest tests/test_hello.py -v
```

Expected output:
```
tests/test_hello.py::test_litmus_works PASSED
```

## What's Happening

This is a plain pytest test. Nothing Litmus-specific yet. We're just verifying:

1. Your Python environment is set up
2. pytest discovers and runs tests
3. The test passes

## Project Structure

Your project should look like:

```
my_project/
├── tests/
│   ├── __init__.py      # (optional) marks as package
│   └── test_hello.py    # your test
├── pyproject.toml       # or requirements.txt
└── ...
```

## Why Start Simple?

Hardware testing can get complex fast. Starting with the simplest possible test ensures:

- Your environment works
- You can iterate quickly
- Problems are easy to diagnose

Once this works, we'll add actual measurements.

## About conftest.py

This step uses a `conftest.py` to define the `dmm` (and later `psu`) fixtures. That's the same pattern you'd use in any pytest project — Litmus does not require its own configuration to get started.

Later steps will introduce a **[station YAML](../concepts/stations.md)** — a single file that declares the bench's instruments. When that exists, Litmus auto-registers an instrument-role fixture (for each instrument declared in the station YAML, a pytest fixture by that name is provided to your tests automatically) such as `dmm`, `psu`, etc., and you can drop the corresponding `conftest.py` fixtures. For step 1, ignore station YAML entirely.

## Bench-bringup pattern

For a brand-new board, the smallest scaffold is just a `conftest.py` fixture and one test. `litmus init --tier=bringup` creates this layout. (Forward references: [`Limit`](../reference/models.md) is Litmus's pass/fail-bound model, [`verify`](../reference/litmus-fixtures.md#verify--function) is the fixture that records a measurement and checks it against a limit — both introduced fully in step 3 / step 4. [PyVISA](https://pyvisa.readthedocs.io/) and [PyMeasure](https://pymeasure.readthedocs.io/) are the external instrument-driver libraries you'd swap into the fixture for real hardware.)

```python
# tests/conftest.py
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def dmm() -> MagicMock:
    inst = MagicMock()
    inst.measure_dc_voltage.return_value = 3.3
    return inst  # swap MagicMock for your PyVISA / PyMeasure driver
```

```python
# tests/test_smoke.py
def test_rail(dmm, verify):
    verify(
        "v_rail",
        float(dmm.measure_dc_voltage()),
        limit={"low": 3.2, "high": 3.4, "nominal": 3.3, "units": "V"},
    )
```

Rows land in parquet with `measurement_value`, `limit_low` / `limit_high`, and `measurement_outcome` populated. [Traceability](../how-to/traceability.md) columns (`dut_pin`, `instrument_channel`, `fixture_connection`, `spec_ref`) stay NULL until you graduate to a [station](../concepts/stations.md) + [product](../concepts/products.md) + [fixture](../concepts/fixtures.md) — at which point the test bodies don't change.

See [`examples/01-vanilla`](https://github.com/pragmatest-dev/litmus/tree/main/examples/01-vanilla) for a runnable example.

## Verify the Setup

Run tests with verbose output:

```bash
pytest tests/ -v --collect-only
```

This shows what pytest discovered without running tests.

## Troubleshooting

**"pytest: command not found"**
```bash
# Activate your virtual environment
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate     # Windows
```

**"No tests collected"**
- Check that test file starts with `test_`
- Check that function starts with `test_`

**"fixture 'dmm' not found" (or any instrument role)**
- Define the fixture in `tests/conftest.py` — see the bench-bringup pattern above. `litmus init --tier=bringup` creates this scaffold for you. Later tutorial steps will lift the fixture definition into a station YAML, at which point the role fixture is auto-registered.

## What You Learned

- How to create a pytest test file
- How to run tests with pytest
- Basic project structure for Litmus tests

## Continue

Now let's make the test actually do something useful.

← [Quick Start](00-quickstart.md)  |  [Step 2: Mock Instruments →](02-mock-instruments.md)
