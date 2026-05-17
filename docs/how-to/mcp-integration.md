# AI-Assisted Test Development

Use Litmus with Claude Desktop, Cursor, Cline, or other AI tools via the MCP server.

## Overview

Litmus exposes an MCP (Model Context Protocol) server with **12 tools** that let AI assistants orchestrate the complete datasheet-to-test workflow. The platform does NOT call LLMs itself — it exposes tools so AI agents can drive the process. Full tool reference in [API reference](../reference/api.md#mcp-tools).

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

## The 12 MCP Tools

| Tool | Purpose |
|------|---------|
| `litmus_project(action=...)` | Unified CRUD: init, list, get, save, read |
| `litmus_discover()` | Scan for connected VISA instruments |
| `litmus_match()` | Find compatible instruments and stations |
| `litmus_run()` | Execute tests and return results |
| `litmus_open()` | Get browser URL for viewing/editing |
| `litmus_schema()` | Get JSON Schema for YAML types |
| `litmus_events()` | Query the event store |
| `litmus_sessions()` | List sessions with metadata |
| `litmus_channels()` | Query channel data from the streaming store |
| `litmus_metrics()` | Compute yield / Pareto / Cpk / retest / time-loss |
| `litmus_runs()` | Query the runs view (filtered, paginated) |
| `litmus_steps()` | Query the steps view (one row per step execution) |

### litmus — Unified CRUD Operations

```python
# Initialize project (CALL FIRST!)
result = litmus_project(action="init", path="~/my-project")
project = result["project_root"]  # Use in all subsequent calls

# List entities
litmus_project(action="list", type="product", project=project)
litmus_project(action="list", type="station", project=project)

# Get entity details
litmus_project(action="get", type="product", id="tps54302", project=project)

# Save entity (content validated against schema)
litmus_project(action="save", type="product", id="tps54302", content={...}, project=project)

# Read file or template
litmus_project(action="read", path="products/tps54302.yaml", project=project)
litmus_project(action="read", path="template:test", project=project)  # Get test template
```

**Entity types:** product, station, fixture, catalog, instrument_asset, project, test

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
# Returns: run_id, outcome (passed/failed/errored/skipped/...), measurements, errors
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

## The five-step workflow

### Step 0: Initialize Project

```python
result = litmus_project(action="init", path="~/my-hardware-tests")
project = result["project_root"]
```

Creates `pyproject.toml`, `conftest.py`, directories. Run `uv sync` after.

### Step 1: Create Product Spec from Datasheet

**Goal:** Extract electrical characteristics and specifications from datasheet.

**Key concepts:**
- **Characteristic:** A measurable property (output_voltage, quiescent_current, etc.)
- **SpecBand:** One specification with `when` clause, nominal value, and accuracy
- **When clause:** Operating-point parameters (temperature, load, frequency, etc.) that determine which SpecBand applies

```python
litmus_project(action="save", type="product", id="tps54302", content={
    # Top-level Product fields are flat — no wrapping `product:` key.
    "id": "tps54302",
    "name": "TPS54302 3A Synchronous Buck Converter",
    "part_number": "TPS54302DSGR",
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
            "bands": [
                {
                    "when": {"temperature": 25, "load": 0.5},
                    "value": 3.3,
                    "accuracy": {"pct_reading": 1.5}
                },
                {
                    "when": {"temperature": 25, "load": 3.0},
                    "value": 3.3,
                    "accuracy": {"pct_reading": 2.0}
                },
                {
                    "when": {"temperature": 85, "load": 3.0},
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
            "bands": [
                {
                    "when": {"temperature": 25, "load": 0},
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
litmus_project(action="save", type="station", id="bench_1", content={
    # Top-level Station fields are flat — no wrapping `station:` key.
    "id": "bench_1",
    "name": "Development Bench",
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
template = litmus_project(action="read", path="template:test", project=project)
```

**Test code** (`tests/test_tps54302.py`):

```python
litmus_project(action="save", type="test", id="tests/test_tps54302.py", content={
    "code": '''
def test_output_voltage(context, psu, dmm, verify):
    """Verify output voltage across temperature and load conditions."""
    # Get test parameters from vector
    temperature = context.get_param("temperature", 25)
    load = context.get_param("load", 0.5)

    # Set up stimulus
    vin = context.get_param("vin", 12.0)
    psu.set_voltage(vin)
    psu.enable_output()

    # Measure and check - spec resolves the limit from the product YAML
    verify("output_voltage", dmm.measure_dc_voltage())


def test_quiescent_current(context, psu, dmm, verify):
    """Verify quiescent current with no load."""
    psu.set_voltage(context.get_param("vin", 12.0))
    psu.enable_output()
    verify("quiescent_current", dmm.measure_dc_current())
'''
}, project=project)
```

**Test configuration** (`tests/test_<module>.yaml`):

```python
litmus_project(action="save", type="test", id="tests/test_<module>.yaml", content={
    "code": '''
tests:
  test_output_voltage:
    sweeps:
      - {temperature: [25, 85]}     # outer loop
      - {load: [0.1, 0.5, 0.8, 3.0]} # middle loop
      - {vin: [10.5, 12.0, 15.0]}    # inner loop
    characteristics: [output_voltage]   # pull limits from the product spec
    limits:
      output_voltage:
        characteristic: output_voltage   # auto-derive from SpecBand at vector conditions
        tolerance_pct: 10                # manufacturing margin
        comparator: GELE                 # low <= value <= high

  test_quiescent_current:
    sweeps:
      - {temperature: [25], load: [0], vin: [12.0]}  # zipped single point
    characteristics: [quiescent_current]
    limits:
      quiescent_current:
        characteristic: quiescent_current
        tolerance_pct: 15
        comparator: LE                   # value <= high
'''
}, project=project)
```

**What happens at runtime:**
1. For each vector (e.g., temperature=25, load=0.5), the framework:
   - Finds the matching SpecBand by evaluating its `when:` clause against the vector parameters
   - Resolves nominal ± accuracy from that band
   - Applies the configured `tolerance_pct` to widen / tighten the production limit
   - Runs the test and checks the measurement against the resolved limit
   - Records pass/fail

### Step 4: Execute and Analyze

```python
result = litmus_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project
)

# Results include measurements, outcome, and traceability
print(result["outcome"])  # "passed" / "failed" / "errored" / "skipped" / "done" / "terminated" / "aborted"
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

**`when` clause** — Test parameters that determine which SpecBand applies (NOT specification values)
```yaml
when:
  temperature: 25        # Temperature in °C
  load: 0.5              # Load in Amps
  frequency: 1000        # Frequency in Hz
```

**SpecBand** — One specification: `when` clause + nominal value + accuracy
```yaml
bands:
  - when: {temperature: 25, load: 0.5}
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

Sweeps define what to vary. Each list entry is one parametrize axis
(use multiple keys in one entry to zip; stack entries to nest):

```yaml
sweeps:
  - {temperature: [25, 85]}          # outer loop
  - {load: [0.1, 0.5, 3.0]}          # inner loop
characteristics: [output_voltage]     # pull limits from the product spec
```

This creates **2 × 3 = 6 test vectors** (nested combinations).

Each vector is passed to the test function via `context`:
```python
temperature = context.get_param("temperature", 25)  # From vector
load = context.get_param("load", 0.5)               # From vector
```

### Limit shapes

A sidecar `limits:` entry (or the kwargs to `@pytest.mark.litmus_limits`) is a `MeasurementLimitConfig` dict (`litmus.models.test_config.MeasurementLimitConfig`). All limits ultimately resolve to `low` / `high` / `nominal` / `comparator` at evaluation time.

| Shape | Example | When to use |
|------|---------|------------|
| **Direct** | `{low: 3.2, high: 3.4, units: V}` | Static numeric limits |
| **Nominal + tolerance** | `{nominal: 3.3, tolerance_pct: 5, units: V}` | Symmetric tolerance around a nominal value |
| **Characteristic delegation** | `{characteristic: "output_voltage", tolerance_pct: 10}` | Pull nominal + accuracy from the product spec (resolves per-vector via the matching SpecBand `when:` clause). `characteristic:` is the auto-derive trigger — `spec_ref:` is a free-form annotation only, it does NOT look anything up. |
| **Bands** | `{bands: [{when: {temperature: 25}, low: 3.2, high: 3.4}, ...]}` | Condition-dependent: inline list of bands evaluated against the active vector |
| **Comparator override** | `{nominal: 5.0, comparator: EQ}` | Pick the ATML comparator explicitly (`EQ`/`LE`/`GE`/`GELE`/...) |

Most common: **Characteristic delegation** (spec-derived) and **Direct** (static).

The plain `Limit` model (`litmus.models.test_config.Limit`) only carries the resolved `low / high / nominal / units / characteristic_id / spec_ref / comparator` shape — `tolerance_pct`, `bands:`, and `characteristic:` all live on `MeasurementLimitConfig`. You write `MeasurementLimitConfig` in sidecars and markers; `Limit` is what the resolver hands the runtime.

## Test Code Pattern

### ✅ Correct Pattern

```python
def test_output_voltage(context, psu, dmm, verify):
    """Measure output voltage at specified conditions."""
    # 1. Get test parameters from vector (context)
    temperature = context.get_param("temperature", 25)
    load = context.get_param("load", 0.5)
    vin = context.get_param("vin", 12.0)

    # 2. Set up stimulus (instrument methods don't return anything)
    psu.set_voltage(vin)
    psu.enable_output()

    # 3. Measure and CHECK via verify — it resolves the limit from
    # the product spec and raises AssertionError on FAIL
    verify("output_voltage", dmm.measure_dc_voltage())
```

### ❌ Wrong Patterns

```python
# WRONG: Hardcoded stimulus values
def test_output(psu, dmm, verify):
    psu.set_voltage(12.0)  # Where does 12.0 come from? Use context.get_param.
    verify("output_voltage", dmm.measure_voltage())

# WRONG: Hardcoded assertions instead of spec/limit resolution
def test_output(dmm):
    value = dmm.measure_voltage()
    assert value == 3.3  # No traceability, no limit model

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
result = litmus_project(action="init", path="~/tps54302-test")
project = result["project_root"]
```

**Step 1: Create spec**
```python
# (Use example from Step 1 above)
litmus_project(action="save", type="product", id="tps54302", content={...}, project=project)
```

**Step 2: Setup station**
```python
# (Use example from Step 2 above)
litmus_project(action="save", type="station", id="bench_1", content={...}, project=project)
```

**Step 3: Generate tests**
```python
# (Use examples from Step 3 above)
litmus_project(action="save", type="test", id="tests/test_tps54302.py", content={...}, project=project)
litmus_project(action="save", type="test", id="tests/test_<module>.yaml", content={...}, project=project)
```

**Step 4: Run tests**
```python
result = litmus_run(test="tests/test_tps54302.py", station="bench_1", serial="SN001", project=project)
print(result["outcome"])  # "passed" / "failed" / ...
```

## Checklist Before Generating Tests

- [ ] Product spec created with `bands` (not `test_requirements`)
- [ ] Characteristics have proper `bands` list with `when`, `value`, `accuracy`
- [ ] Station configured with real or mock instruments
- [ ] Called `litmus_project(action="read", path="template:test")` to see current pattern
- [ ] Test is a plain `def test_*` function or class method
- [ ] Test accepts `context`, `verify`, `logger`, and instrument fixtures (as needed)
- [ ] Test gets parameters via `context.get_param("key", default)`
- [ ] Test uses `verify(name, value)` or `logger.measure(name, value)` to record measurements
- [ ] Test config uses `characteristics:` + `spec_ref` limits
- [ ] `tolerance_pct` applied for manufacturing margin
- [ ] No hardcoded specification values in test code

## Next Steps

- [Writing Tests](writing-tests.md) — Detailed test patterns
- [Simulation Mode](mock-mode.md) — Testing without hardware
- [Architecture](../concepts/architecture.md) — System design
- [Specification Format](../how-to/spec-driven-testing.md) — SpecBand structure
