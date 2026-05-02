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

You may not need a `conftest.py` at all. The Litmus plugin auto-registers instrument role fixtures from your station config (e.g., `dmm`, `psu`, `eload`) — no boilerplate needed.

You only need `conftest.py` when you want to:
- Override an auto-registered fixture with custom setup/teardown
- Add pin-based fixtures for DUT traceability
- Define project-specific test utilities

## Bench-bringup escape hatch (no station YAML)

For a brand-new board where you don't yet have a station / product / fixture YAML, skip all of that and define instrument fixtures directly in `conftest.py`. `litmus init --tier=bringup` scaffolds this for you; the pattern is:

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
from litmus.models.test_config import Limit


def test_rail(dmm, verify):
    verify(
        "v_rail",
        float(dmm.measure_dc_voltage()),
        limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    )
```

Rows land in parquet with `meas_value`, `meas_limit_low/high`, and `outcome` populated. Traceability columns (`meas_dut_pin`, `meas_instrument_channel`, `meas_net`, `meas_spec_ref`) stay null until you graduate to a station + product + fixture — at which point the test bodies don't change.

See `examples/01-bringup/` for a runnable example.

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
- You don't have a station YAML yet. Either scaffold one
  (`litmus init --tier=bench`) or use the bench-bringup escape hatch
  above — define the fixture directly in `tests/conftest.py` with
  `MagicMock` (or a real driver). `litmus init --tier=bringup`
  does this for you.

## What You Learned

- How to create a pytest test file
- How to run tests with pytest
- Basic project structure for Litmus tests

## Next Step

Now let's make the test actually do something useful.

[Step 2: Mock Instruments →](02-mock-instruments.md)
