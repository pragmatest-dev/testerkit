# AI-Assisted Test Development with Litmus V2

Use Litmus with Claude Desktop, Cursor, Cline, or other AI tools via the MCP server.

## Overview

Litmus exposes an MCP (Model Context Protocol) server with **6 tools** that let AI assistants orchestrate the complete datasheet-to-test workflow. The platform does NOT call LLMs itself — it exposes tools so that AI agents can drive the process.

**Architecture:** Spec-driven testing with SpecBands, vector-based test parameters, and automated limit derivation.

## Setup

### Claude Desktop (Recommended)

```bash
litmus setup claude-desktop
```

Detects WSL and configures Claude Desktop to connect to Litmus MCP server. Restart Claude Desktop to connect.

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

## The 6 MCP Tools

| Tool | Purpose |
|------|---------|
| `litmus(action=...)` | Unified CRUD: init, list, get, save, read |
| `litmus_discover()` | Scan for connected VISA instruments |
| `litmus_match()` | Find compatible instruments and stations |
| `litmus_run()` | Execute tests and return results |
| `litmus_open()` | Get browser URL for viewing/editing |
| `litmus_schema()` | Get JSON Schema for YAML types |

### litmus — Unified CRUD Operations

```python
# Initialize project (CALL FIRST!)
result = litmus(action="init", path="~/my-project")
project = result["project_root"]  # Use in all subsequent calls

# List entities
litmus(action="list", type="product", project=project)
litmus(action="list", type="station", project=project)

# Get entity details
litmus(action="get", type="product", id="tps54302", project=project)

# Save entity (content validated against schema)
litmus(action="save", type="product", id="tps54302", content={...}, project=project)

# Read file or template
litmus(action="read", path="products/tps54302/spec.yaml", project=project)
litmus(action="read", path="template:test", project=project)  # Get test template
```

**Entity types:** product, station, fixture, sequence, test

### litmus_discover — Find Instruments

```python
# Scan for VISA instruments on all protocols
instruments = litmus_discover()
# Returns: [{"resource": "GPIB0::5::INSTR", "type": "dmm", "idn": "Keysight 34461A"}]
```

### litmus_match — Find Compatible Instruments

```python
# Recommend catalog instruments for capability requirements
matches = litmus_match(
    requirements=[
        {"function": "dc_voltage", "direction": "output", "range_max": 50, "units": "V"},
        {"function": "dc_current", "direction": "output", "range_max": 10, "units": "A"}
    ],
    project=project
)
# Returns: [{"model": "keysight_e36312a", "coverage": 0.95, "accuracy": "..."}]
```

### litmus_run — Execute Tests

```python
result = litmus_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project
)
# Returns: run_id, status (PASS/FAIL), measurements, errors
```

### litmus_open — Browser URL

```python
info = litmus_open(type="product", id="tps54302")
# Returns: {"url": "http://localhost:8000/products/tps54302"}
```

### litmus_schema — Get JSON Schema

```python
schema = litmus_schema(yaml_type="product")
# Returns: JSON Schema for product spec validation
```

## The 5-Step Workflow (V2)

### Step 0: Initialize Project

```python
result = litmus(action="init", path="~/my-hardware-tests")
project = result["project_root"]
```

Creates `pyproject.toml`, `conftest.py`, directories. Run `uv sync` after.

### Step 1: Create Product Spec from Datasheet

**Goal:** Extract electrical characteristics and specifications from datasheet.

**Key concepts:**
- **Characteristic:** A measurable property (output_voltage, quiescent_current, etc.)
- **SpecBand:** One specification with test conditions, nominal value, and accuracy
- **Conditions:** Test parameters (temperature, load, frequency, etc.) that determine which SpecBand applies

```python
litmus(action="save", type="product", id="tps54302", content={
    "product": {
        "id": "tps54302",
        "name": "TPS54302 3A Synchronous Buck Converter",
        "manufacturer": "Texas Instruments",
        "part_number": "TPS54302DSGR"
    },
    "pins": {
        "VIN": {"name": "Pin 1", "net": "VIN", "role": "power"},
        "VOUT": {"name": "Pin 5", "net": "VOUT_3V3", "role": "power"},
        "FB": {"name": "Pin 3", "net": "FB", "role": "signal"}
    },
    "characteristics": {
        "output_voltage": {
            "function": "dc_voltage",
            "direction": "output",
            "units": "V",
            "pin": "VOUT",
            "specs": [
                {
                    "conditions": {"temperature": 25, "load": 0.5},
                    "value": 3.3,
                    "accuracy": {"pct_reading": 1.5}
                },
                {
                    "conditions": {"temperature": 25, "load": 3.0},
                    "value": 3.3,
                    "accuracy": {"pct_reading": 2.0}
                },
                {
                    "conditions": {"temperature": 85, "load": 3.0},
                    "value": 3.3,
                    "accuracy": {"pct_reading": 3.0}
                }
            ]
        },
        "quiescent_current": {
            "function": "dc_current",
            "direction": "input",
            "units": "mA",
            "pin": "VIN",
            "specs": [
                {
                    "conditions": {"temperature": 25, "load": 0},
                    "value": 5,
                    "accuracy": {"absolute": 0.5}
                }
            ]
        }
    }
}, project=project)
```

### Step 2: Setup Test Station

**Goal:** Select instruments and create station configuration.

```python
# Find compatible instruments for your characteristics
matches = litmus_match(
    requirements=[
        {"function": "dc_voltage", "direction": "output", "range_max": 20, "units": "V"},
        {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"}
    ],
    project=project
)

# Save station configuration
litmus(action="save", type="station", id="bench_1", content={
    "station": {
        "id": "bench_1",
        "name": "Development Bench"
    },
    "instruments": {
        "psu": {
            "type": "psu",
            "driver": "drivers.PSU",
            "resource": "GPIB0::1::INSTR",
            "catalog_ref": "keysight_e36312a",
            "mock": True,
            "mock_config": {
                "set_voltage": None,
                "measure_voltage": 12.0
            }
        },
        "dmm": {
            "type": "dmm",
            "driver": "drivers.DMM",
            "resource": "GPIB0::5::INSTR",
            "catalog_ref": "keysight_34461a",
            "mock": True,
            "mock_config": {
                "measure_dc_voltage": 3.3
            }
        }
    }
}, project=project)
```

### Step 3: Generate Test Files

**Goal:** Create pytest tests and configuration with vector sweep and limits.

Always read the template first:

```python
template = litmus(action="read", path="template:test", project=project)
```

**Test code** (`tests/test_tps54302.py`):

```python
litmus(action="save", type="test", id="tests/test_tps54302.py", content={
    "code": '''
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    """Verify output voltage across temperature and load conditions."""
    # Get test parameters from vector
    temperature = context.get_in("temperature", 25)
    load = context.get_in("load", 0.5)

    # Set up stimulus
    vin = context.get_in("vin", 12.0)
    psu.set_voltage(vin)
    psu.enable_output()

    # Measure and return - framework checks limits
    return dmm.measure_dc_voltage()

@litmus_test
def test_quiescent_current(context, psu, dmm):
    """Verify quiescent current with no load."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_current()
'''
}, project=project)
```

**Test configuration** (`tests/config.yaml`):

```python
litmus(action="save", type="test", id="tests/config.yaml", content={
    "code": '''
test_output_voltage:
  vectors:
    expand: product              # Use product characteristics
    temperature: [25, 85]        # Sweep conditions
    load: [0.1, 0.5, 0.8, 3.0]
    vin: [10.5, 12.0, 15.0]
  limits:
    output_voltage:
      ref: "output_voltage"      # Auto-derive from SpecBand at vector conditions
      guardband_pct: 10          # Manufacturing margin
      comparator: GELE           # Greater-or-equal AND less-or-equal

test_quiescent_current:
  vectors:
    - temperature: 25
      load: 0
      vin: 12.0
  limits:
    quiescent_current:
      ref: "quiescent_current"
      guardband_pct: 15
      comparator: LE             # Less-or-equal
'''
}, project=project)
```

**What happens at runtime:**
1. For each vector (e.g., temperature=25, load=0.5), the framework:
   - Finds matching SpecBand using conditions
   - Calls `derive_limit(characteristic, conditions)` to get nominal ± accuracy
   - Applies guardband to get production limits
   - Runs test and checks against limits
   - Records pass/fail

### Step 4: Execute and Analyze

```python
result = litmus_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project
)

# Results include measurements, pass/fail status, and traceability
print(result["status"])  # "PASS" or "FAIL"
print(result["summary"])  # Test statistics
```

## Key Concepts

### Characteristics vs Conditions vs Specs

**Characteristic** — What you're testing (output_voltage, quiescent_current)
```yaml
output_voltage:          # Characteristic name
  function: dc_voltage   # What measurement function to use
  direction: output      # DUT outputs this signal
  units: V
  pin: VOUT
```

**Conditions** — Test parameters that determine which SpecBand applies (NOT specification values)
```yaml
conditions:
  temperature: 25        # Temperature in °C
  load: 0.5              # Load in Amps
  frequency: 1000        # Frequency in Hz
```

**SpecBand** — One specification: conditions + nominal value + accuracy
```yaml
specs:
  - conditions: {temperature: 25, load: 0.5}
    value: 3.3                               # Nominal
    accuracy: {pct_reading: 2.0}             # ±2% of reading
```

**Limit** — Derived from SpecBand at runtime
```
SpecBand value: 3.3V
Accuracy: ±2.0% of 3.3 = ±0.066V
Production limit: 3.3 ± 0.066 = [3.234, 3.366]V
With 10% guardband: [3.2539, 3.3461]V
```

### Vectors and Test Parameters

Vectors define what to sweep:

```yaml
vectors:
  expand: product                    # Use product characteristics
  temperature: [25, 85]              # Sweep values
  load: [0.1, 0.5, 3.0]              # Multiple values
```

This creates **2 × 3 = 6 test vectors** (combinations).

Each vector is passed to the test function via `context`:
```python
temperature = context.get_in("temperature", 25)  # From vector
load = context.get_in("load", 0.5)               # From vector
```

### Limit Types (All 6 Patterns)

| Type | Example | When to use |
|------|---------|------------|
| **Direct** | `{low: 3.2, high: 3.4, units: V}` | Static limits |
| **Ref** | `{ref: "output_voltage", guardband_pct: 10}` | Spec-derived, auto-updated |
| **Expression** | `{expr: "nominal * 1.05", tolerance_pct: 3}` | Calculated from inputs |
| **Lookup** | `{lookup: {key: temperature, table: {25: {...}, 85: {...}}}}` | Condition-dependent |
| **Step** | `{steps: {param: load, ranges: [{below: 1.0, limit: ...}]}}` | Range-dependent |
| **Callable** | `{callable: "myproject.limits.custom_limit"}` | Complex logic |

Most common: **Ref** (spec-derived) and **Direct** (static).

## Test Code Pattern

### ✅ Correct Pattern

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    """Measure output voltage at specified conditions."""
    # 1. Get test parameters from vector (context)
    temperature = context.get_in("temperature", 25)
    load = context.get_in("load", 0.5)
    vin = context.get_in("vin", 12.0)

    # 2. Set up stimulus (instrument methods don't return anything)
    psu.set_voltage(vin)
    psu.enable_output()

    # 3. Measure and RETURN value
    # Framework automatically checks against spec-derived limits
    return dmm.measure_dc_voltage()
```

### ❌ Wrong Patterns

```python
# WRONG: Hardcoded values
def test_output():
    psu.set_voltage(12.0)  # Where does 12.0 come from?
    return dmm.measure_voltage()

# WRONG: Assertions instead of returns
def test_output():
    value = dmm.measure_voltage()
    assert value == 3.3  # Hardcoded!

# WRONG: Standalone calculation
class Converter:
    def calculate_vout(self, vin):
        return vin * (1000 / (1000 + 2000))

def test_output():
    assert Converter().calculate_vout(12.0) == 4.0  # No instrument connection!
```

## Workflow Example: Full End-to-End

**Scenario:** Test a TPS54302 buck converter

**Step 0: Init**
```python
result = litmus(action="init", path="~/tps54302-test")
project = result["project_root"]
```

**Step 1: Create spec**
```python
# (Use example from Step 1 above)
litmus(action="save", type="product", id="tps54302", content={...}, project=project)
```

**Step 2: Setup station**
```python
# (Use example from Step 2 above)
litmus(action="save", type="station", id="bench_1", content={...}, project=project)
```

**Step 3: Generate tests**
```python
# (Use examples from Step 3 above)
litmus(action="save", type="test", id="tests/test_tps54302.py", content={...}, project=project)
litmus(action="save", type="test", id="tests/config.yaml", content={...}, project=project)
```

**Step 4: Run tests**
```python
result = litmus_run(test="tests/test_tps54302.py", station="bench_1", serial="SN001", project=project)
print(result["status"])  # "PASS" or "FAIL"
```

## Checklist Before Generating Tests

- [ ] Product spec created with `specs` (not `test_requirements`)
- [ ] Characteristics have proper `specs` list with `conditions`, `value`, `accuracy`
- [ ] Station configured with real or mock instruments
- [ ] Called `litmus(action="read", path="template:test")` to see current pattern
- [ ] Test uses `@litmus_test` decorator
- [ ] Test accepts `context` and instrument fixtures
- [ ] Test gets parameters via `context.get_in("key", default)`
- [ ] Test RETURNS measured values (no assertions)
- [ ] Test config uses `expand: product` and `ref` limits
- [ ] Guardbands applied for manufacturing margin
- [ ] No hardcoded specification values in test code

## Next Steps

- [Writing Tests](writing-tests.md) — Detailed test patterns
- [Simulation Mode](simulation-mode.md) — Testing without hardware
- [Architecture](../concepts/architecture.md) — System design
- [Specification Format](../guides/spec-driven-testing.md) — SpecBand structure
