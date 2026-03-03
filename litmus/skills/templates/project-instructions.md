# Litmus — Hardware Test Platform

Litmus is a Python-native hardware test platform. It provides configuration management, instrument drivers, data storage, and AI tool integration for hardware testing.

## Folder Convention

Entity-aligned folders contain YAML configuration files:
- `products/` — DUT specifications and test limits
- `stations/` — Bench configurations (instruments + roles)
- `fixtures/` — DUT-to-instrument pin mappings
- `sequences/` — Test order, vectors, and limits
- `catalog/` — Instrument capability definitions

Code folders contain Python scripts:
- `tests/` — pytest test files
- `drivers/` — Custom instrument drivers (if needed)

## Common Commands

```bash
pytest                         # Run tests
pytest --mock-instruments      # Run with mock instruments
pytest --station=my_bench      # Run against specific station

litmus serve                   # Operator UI (localhost:8000)
litmus serve --reload          # Dev mode with auto-reload
litmus runs                    # List recent test runs
litmus show <run_id>           # Show run details
litmus show <run_id> -f html   # Generate HTML report
litmus discover                # Scan for instruments
```

## YAML Configuration

All configuration uses YAML files with Pydantic validation. Edit YAML directly or use the operator UI (`litmus serve`).

- **Products** define what you're testing: capabilities, limits, specs
- **Stations** define your bench: which instruments, what roles they play
- **Fixtures** map DUT pins to instrument channels
- **Sequences** define test execution order and parameters

## Writing Tests

Tests are standard pytest functions using the `@litmus_test` decorator:

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    """Verify output voltage is within spec."""
    psu.set_voltage(3.3)
    psu.enable_output()

    return dmm.measure_voltage()
```

## MCP Tools

Litmus exposes MCP tools for AI agents:
- `litmus` — CRUD operations on products, stations, fixtures, instruments, sequences
- `litmus_discover` — Discover instruments on VISA bus
- `litmus_match` — Check if a station can test a product
- `litmus_run` — Execute tests and get results
