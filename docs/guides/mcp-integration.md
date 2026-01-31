# AI-Assisted Test Development

Use Litmus with Claude Code, Cursor, Cline, or other AI tools via the MCP server.

## Overview

Litmus exposes an MCP (Model Context Protocol) server that lets AI assistants:
- Read product specs and station configs
- Find compatible stations
- Generate test code
- Save files
- Run tests

## Setup

### Claude Code

```bash
litmus setup claude-code
```

This adds Litmus to your MCP configuration.

### Cursor

```bash
litmus setup cursor
```

### Cline (VS Code)

```bash
litmus setup cline
```

### Manual

Start the MCP server directly:

```bash
litmus mcp serve
```

## Available Tools

### Reading Context

| Tool | Description |
|------|-------------|
| `list_products` | List all product specifications |
| `get_product_spec` | Get full product spec by ID |
| `list_stations` | List all stations |
| `get_station_config` | Get station configuration |
| `list_instrument_types` | List available instrument types |
| `get_instrument_library` | Get instrument definition |

### Capability Matching

| Tool | Description |
|------|-------------|
| `derive_required_capabilities` | Get requirements from product |
| `find_compatible_stations` | Find stations that can test a product |
| `check_station_compatibility` | Check specific station compatibility |

### Writing

| Tool | Description |
|------|-------------|
| `save_product_spec` | Save a new product specification |
| `save_test_sequence` | Save a test file |

### Execution

| Tool | Description |
|------|-------------|
| `run_tests` | Execute tests |
| `get_run_status` | Check test run status |
| `list_runs` | List recent test runs |

## Workflow Example

### 1. Initialize Project

Tell the AI to set up a Litmus project:

```
"Initialize a Litmus project for testing my power converter"
```

The AI will:
1. Create project structure
2. Set up configuration files
3. Create initial templates

### 2. Parse Datasheet

Provide product information:

```
"Here's my product datasheet. Create a spec.yaml for it."
```

The AI will:
1. Extract electrical characteristics
2. Identify pins and interfaces
3. Create product specification

### 3. Review and Refine

```
"The output voltage tolerance should be ±3%, not ±5%"
```

The AI updates the spec accordingly.

### 4. Find Compatible Stations

```
"Which stations can test this product?"
```

The AI uses `find_compatible_stations` to check.

### 5. Generate Tests

```
"Generate tests for all the characteristics"
```

The AI:
1. Reads product requirements
2. Creates test functions
3. Configures limits
4. Saves files

### 6. Run Tests

```
"Run the tests in simulation mode"
```

The AI executes and reports results.

## Prompting Tips

### Be Specific About Limits

```
"Create a product spec with output voltage 3.3V ±5%, guardbanded by 10% for production"
```

### Specify Simulation

```
"Run tests with --simulate flag since I don't have hardware"
```

### Ask for Explanation

```
"Explain the capability matching result - why can't bench_2 test this product?"
```

### Request Validation

```
"Validate my station configuration and check for any issues"
```

## Example Conversation

**User:** I need to test a DC-DC converter. It takes 5V input and outputs 3.3V.

**AI:** I'll help you set up testing for your DC-DC converter. Let me create a product specification.

*Uses `save_product_spec` to create:*

```yaml
product:
  id: dc_dc_converter
  name: "5V to 3.3V DC-DC Converter"

pins:
  VIN:
    name: "Input"
    type: power
  VOUT:
    name: "Output"
    type: signal

characteristics:
  input_voltage:
    direction: input
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VIN]
    conditions:
      - nominal: 5.0
        tolerance_pct: 10

  output_voltage:
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
```

**User:** What stations can test this?

**AI:** *Uses `find_compatible_stations`:*

Found 2 compatible stations:
- bench_1 (Production Bench 1) ✓
- dev_bench (Development Bench) ✓

bench_3 is NOT compatible - missing power supply capability.

**User:** Generate tests for bench_1

**AI:** *Creates test file:*

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Verify output voltage is within specification."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.enable_output()

    return dmm.measure_voltage()
```

*Creates config.yaml:*

```yaml
test_output_voltage:
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
```

## HTTP API Alternative

All MCP tools are also available via HTTP:

```bash
# Start server
litmus serve

# List products
curl http://localhost:8000/api/products

# Find compatible stations
curl "http://localhost:8000/api/match?product_id=dc_dc_converter"

# Run tests
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"dut_serial": "SN001", "station_id": "bench_1", "test_path": "tests/"}'
```

## Best Practices

### 1. Start with Spec

Always create/review the product spec first:

```
"Show me the current product spec for power_board"
```

### 2. Validate Before Running

```
"Validate my test configuration and check for issues"
```

### 3. Use Simulation First

```
"Run tests with simulation to verify the test logic"
```

### 4. Iterate on Feedback

```
"The test failed - what went wrong and how do I fix it?"
```

### 5. Request Documentation

```
"Add docstrings explaining what each test verifies"
```

## Platform Note

Litmus exposes tools for AI agents but **never calls LLMs itself**. The platform is AI-ready but AI-independent — you can use it without any AI tools.

## Next Steps

- [API Reference](../reference/api.md) — Full MCP and HTTP API docs
- [Writing Tests](writing-tests.md) — Test patterns
- [Configuration Reference](../reference/configuration.md) — YAML schemas
