# Litmus — Hardware Test Platform

Litmus is a Python-native hardware test platform for the AI-assisted era. It provides the infrastructure layer for hardware testing — configuration management (products, stations, fixtures, sequences), instrument discovery and access (via PyVISA/PyMeasure), structured test data storage (Parquet), and AI tool integration (MCP server). Tests are standard pytest functions; Litmus adds the hardware-specific context, data pipeline, and operator UI. Data flows from YAML config → pytest execution → Parquet results → reports/analytics.

## Folder Convention

The project uses a 7-folder structure. Entity-aligned folders contain YAML configuration files:
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

## Reference Documentation

Read these on demand — don't load them all upfront:

| Topic | File |
|-------|------|
| Writing `@litmus_test` functions | `{LITMUS_REFS}/test-writing.md` |
| Limits, comparators, pass/fail | `{LITMUS_REFS}/limits.md` |
| Station YAML | `{LITMUS_REFS}/station.md` |
| Product spec YAML | `{LITMUS_REFS}/product.md` |
| Fixture YAML (pin routing) | `{LITMUS_REFS}/fixture.md` |
| Sequence YAML (test order) | `{LITMUS_REFS}/sequence.md` |
| Instrument capabilities | `{LITMUS_REFS}/capability.md` |
| Enum values (units, functions) | `{LITMUS_REFS}/enums.md` |
| CLI commands | `{LITMUS_REFS}/cli.md` |
