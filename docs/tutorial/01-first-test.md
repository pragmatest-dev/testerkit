# Step 1: Run Something

**Goal:** Write and run your first Litmus test.

## What You'll Build

A simple test that passes. Nothing fancy yet — just getting the basics working.

## Prerequisites

```bash
# Clone and install
git clone https://github.com/your-org/litmus.git
cd litmus
uv sync  # or: pip install -e .
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

## What You Learned

- How to create a pytest test file
- How to run tests with pytest
- Basic project structure for Litmus tests

## Next Step

Now let's make the test actually do something useful.

[Step 2: Mock Instruments →](02-mock-instruments.md)
