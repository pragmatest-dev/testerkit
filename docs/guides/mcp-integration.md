# AI-Assisted Test Development

Use Litmus with Claude Code, Cursor, Cline, or other AI tools via the MCP server.

## Overview

Litmus exposes an MCP (Model Context Protocol) server with **5 tools** that let AI assistants orchestrate the complete datasheet-to-test workflow. The platform does NOT call LLMs itself — it exposes tools so that AI agents can drive the process.

## Setup

### Claude Code

```bash
litmus setup claude-code
```

### Cursor

```bash
litmus setup cursor
```

### Cline (VS Code)

```bash
litmus setup cline
```

### Manual

```bash
litmus mcp serve
```

## The 5 MCP Tools

| Tool | Description |
|------|-------------|
| `litmus` | Unified CRUD: init, list, get, save, read |
| `litmus_discover` | Scan for connected VISA instruments |
| `litmus_match` | Check product/station/fixture compatibility |
| `litmus_run` | Execute tests and return results |
| `litmus_open` | Get browser URL for an entity |

### litmus — Unified CRUD

One tool for all data operations:

```python
# Initialize project (CALL FIRST!)
litmus(action="init", path="~/my-project")

# List entities
litmus(action="list", type="product")
litmus(action="list", type="station")

# Get entity details
litmus(action="get", type="product", id="tps54302")

# Save entity
litmus(action="save", type="product", id="tps54302", content={...})

# Read file or template
litmus(action="read", path="products/x/spec.yaml")
litmus(action="read", path="template:test")  # Get test template!
```

**Entity types:** product, station, fixture, sequence, instrument, run, test

### litmus_discover — Find Instruments

```python
litmus_discover()
# Returns: list of VISA resources with addresses, types, IDN strings
```

### litmus_match — Check Compatibility

```python
# Find compatible stations for a product
litmus_match(product_id="tps54302")

# Detailed compatibility check
litmus_match(product_id="tps54302", station_id="bench_1")

# Find stations for a fixture
litmus_match(fixture_id="dc_converter_fixture")
```

### litmus_run — Execute Tests

```python
litmus_run(test="products/x/tests/test_x.py", station="bench_1", serial="SN001")
# Returns: run_id, status, summary, output
```

### litmus_open — Browser URL

```python
litmus_open(type="product", id="tps54302")
# Returns: {"url": "http://localhost:8000/products/tps54302"}
```

## Mandatory 6-Step Workflow

### Step 0: Initialize Project

```python
litmus(action="init", path="~/my-hardware-tests")
```

Creates `pyproject.toml`, `conftest.py`, directory structure. Run `uv sync` after.

### Step 1: PARSE_DATASHEET

**Input:** `datasheet.md` → **Output:** `spec.yaml`

```python
litmus(action="read", path="products/{id}/datasheet.md")
litmus(action="save", type="product", id="{id}", content={...})
```

### Step 2: REVIEW_SPEC

**Input:** `spec.yaml` → **Output:** Approved spec

```python
litmus(action="get", type="product", id="{id}")
litmus_open(type="product", id="{id}")  # Human reviews
```

### Step 3: DERIVE_REQUIREMENTS

**Input:** Approved spec → **Output:** `test_requirements` added

```python
litmus(action="save", type="product", id="{id}", content={...})  # Add test_requirements
```

### Step 4: SELECT_STATION

**Input:** test_requirements → **Output:** station selection

```python
litmus_match(product_id="{id}")  # Find compatible stations
```

### Step 5: GENERATE_TESTS

**Input:** spec + station → **Output:** test files

```python
# ALWAYS read the template first!
litmus(action="read", path="template:test")
litmus(action="get", type="product", id="{id}")

# Save test
litmus(action="save", type="test", id="products/{id}/tests/test_{id}.py", content={"code": "..."})
```

### Step 6: EXECUTE_ANALYZE

**Input:** test files + station → **Output:** results

```python
litmus_run(test="products/{id}/tests/", station="{station}", serial="{serial}")
```

## Critical: Test Code Pattern

**Always call `litmus(action="read", path="template:test")` BEFORE writing tests!**

### Rule 1: NO HARDCODED VALUES

```python
# ❌ WRONG - where did 3.3 and 5.0 come from?
Mock(DMM, voltage=3.3)
Mock(PSU, voltage=5.0)

# ✅ CORRECT - values from spec
# spec.test_conditions.default_vin = 12
# spec.test_conditions.default_vout = 5
psu.set_voltage(12.0)  # From spec!
```

### Rule 2: Tests SET UP then MEASURE

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    # 1. Get conditions from spec/vector
    vin = context.get("vin", 12.0)  # From spec.test_conditions.default_vin

    # 2. Set up stimulus
    psu.set_voltage(vin)
    psu.enable_output()

    # 3. Measure and return - framework checks limits
    return dmm.measure_voltage()
```

### Wrong Pattern

```python
# DON'T create standalone simulation!
class MyDeviceModel:
    def calculate_output(self, vin): ...

def test_output():
    assert model.calculate_output(5.0) == 3.3  # Hardcoded!
```

### Key Principles

1. **Read the spec first** — Get default_vin, default_vout from spec.yaml
2. **Tests SET UP conditions** — `psu.set_voltage()`, `eload.set_current()`
3. **Tests MEASURE results** — `dmm.measure_voltage()`
4. **Tests RETURN values** — Framework checks against spec limits
5. **NO hardcoded values** — Every number should trace to the spec

## Product Folder Structure

```
products/{product_id}/
    manifest.yaml       # Workflow position
    datasheet.md        # Source document
    spec.yaml           # Extracted specification
    tests/              # Generated tests
```

## Available Templates

Access via `litmus(action="read", path="template:...")`:

| Template | Description |
|----------|-------------|
| `template:test` | Test file with `@litmus_test` |
| `template:instrument` | Python driver template |
| `template:instrument_yaml` | YAML instrument definition |
| `template:capabilities` | Capability interfaces reference |

## Workflow Example

**User:** I need to test a DC-DC converter. 5V input, 3.3V output.

**AI:**

```python
# Step 0: Init project
litmus(action="init", path="~/dc-converter-test")

# Step 1: Create spec
litmus(action="save", type="product", id="dc_converter", content={
    "product": {"id": "dc_converter", "name": "5V to 3.3V Converter"},
    "characteristics": {
        "output_voltage": {
            "direction": "output",
            "domain": "voltage",
            "signal_types": ["dc"],
            "units": "V",
            "conditions": [{"nominal": 3.3, "tolerance_pct": 5}]
        }
    },
    "test_requirements": {
        "verify_output": {
            "characteristic_ref": "output_voltage",
            "guardband_pct": 10
        }
    }
})
```

**User:** What stations can test this?

**AI:**

```python
litmus_match(product_id="dc_converter")
# Returns: compatible_stations: [bench_1, dev_bench]
```

**User:** Generate tests

**AI:**

```python
# Get the pattern first!
litmus(action="read", path="template:test")

# Save test file
litmus(action="save", type="test", id="products/dc_converter/tests/test_dc_converter.py", content={
    "code": '''
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, dmm, psu):
    psu.set_voltage(5.0)
    psu.enable_output()
    return dmm.measure_dc_voltage()
'''
})
```

## Checklist Before Generating Tests

- [ ] Product has `spec.yaml` (Step 1)
- [ ] Spec has `test_requirements` (Step 3)
- [ ] Station selected (Step 4)
- [ ] Called `litmus(action="read", path="template:test")` — see pattern
- [ ] Called `litmus(action="get", type="product", id="...")` — get spec values
- [ ] Test values come FROM THE SPEC:
  - [ ] Input voltage from `spec.test_conditions.default_vin`
  - [ ] Load current from `spec.specs.continuous_output_current`
- [ ] Test uses `@litmus_test` decorator
- [ ] Test uses instrument fixtures (psu, dmm, eload)
- [ ] Test returns values (no assertions)
- [ ] **NO hardcoded magic numbers** like `3.3` or `5.0`

## HTTP API Alternative

All tools available via HTTP when server is running:

```bash
litmus serve

curl http://localhost:8000/api/products
curl "http://localhost:8000/api/match?product_id=dc_converter"
```

## Platform Note

Litmus exposes tools for AI agents but **never calls LLMs itself**. The platform is AI-ready but AI-independent.

## Next Steps

- [Writing Tests](writing-tests.md) — Test patterns
- [Simulation Mode](simulation-mode.md) — Testing without hardware
- [Configuration Reference](../reference/configuration.md) — YAML schemas
