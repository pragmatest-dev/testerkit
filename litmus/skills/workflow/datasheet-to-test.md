---
name: datasheet-to-test
description: Create hardware tests from a product datasheet using Litmus MCP tools. Guides through spec extraction, station setup, and test generation with step-by-step approval.
---

# Datasheet to Test Workflow

You are helping the user create hardware tests from a product datasheet using Litmus. This is a **collaborative** workflow where you propose and the user approves at each step.

## Workflow Overview

```
Datasheet → Product Spec → Station → Tests → Results
   1           2            3         4        5
```

**Key principle:** Never proceed to the next step without user approval. At each step:
1. Show what you found/created
2. Ask if they want to approve, edit, or regenerate

## MCP Tools Available

| Tool | Purpose |
|------|---------|
| `litmus(action="init", path="...")` | Initialize project, returns `project_root` |
| `litmus(action="save", type="...", id="...", content={...}, project=...)` | Save product/station/test |
| `litmus(action="read", path="...", project=...)` | Read files or templates |
| `litmus_match(product_id, station_id, project)` | Check compatibility |
| `litmus_run(test="...", station="...", serial="...", project=...)` | Execute tests |
| `litmus_open(type="...", id="...")` | Get browser URL for viewing/editing |
| `litmus_discover()` | Scan for VISA instruments |

**IMPORTANT:** Pass `project=<project_root>` to ALL calls after init.

---

## Step 1: Parse Datasheet

**Goal:** Extract electrical characteristics, pins, and test conditions from the datasheet.

**Your actions:**
1. Read the datasheet file the user provides
2. Extract key information:
   - Product ID, name, description
   - Pin definitions (name, type, net)
   - Electrical characteristics (voltage, current, power, timing)
   - Test conditions (temperature, load, input voltage)
   - Performance specs with limits (nominal, min, max, tolerance)
3. Initialize project with `litmus(action="init", path="...")`

**Present to user:**
```
I've analyzed the datasheet and extracted:

**Product:** TPS54302 - 3A Buck Converter

**Pins (5):**
| Name | Role      | Net    | Description |
|------|-----------|--------|-------------|
| VIN  | power     | VIN    | Input voltage |
| SW   | signal    | SW     | Switch node |
| VOUT | power     | VOUT   | Output voltage |
| GND  | ground    | GND    | Ground |
| EN   | signal    | EN     | Enable |

Pin roles: `power` (supply/output rails), `ground` (return/reference),
`signal` (measured/stimulated, default), `reference` (voltage ref, not driven).
Ground pins wire to instrument LO terminals via bus routing in the designer.

**Characteristics (7):**
| Name           | Function   | Direction | Value      | Conditions        |
|----------------|------------|-----------|------------|-------------------|
| input_voltage  | dc_voltage | input     | 4.5-18V    | -                 |
| output_voltage | dc_voltage | output    | 3.3V ±1%   | Vin=5V, Iout=1A   |
| efficiency     | dc_power   | output    | ≥90%       | Vin=5V, Iout=1A   |
| ...            |            |           |            |                   |

**Confidence:** 94% (some thermal specs unclear)

Want me to:
- [A]pprove and continue
- [E]dit - I'll open the editor
- [R]egenerate with different focus
- [?] Ask me questions about specific values
```

---

## Step 2: Save Product Spec

**Goal:** Save the extracted spec and let user refine it.

**Your actions:**
1. Show the draft spec structure
2. Highlight any uncertainties or missing fields
3. Suggest improvements based on common patterns
4. Save with `litmus(action="save", type="product", ...)`

**Present to user:**
```
Here's the draft product specification:

**Validation Results:** ✓ Valid structure

**Suggestions:**
- Consider adding guardband to efficiency (currently at datasheet limit)
- Missing: thermal shutdown temperature (common for power ICs)
- Pin 'EN' could use threshold voltage spec

**Spec Preview:**
```yaml
product:
  id: tps54302
  name: "TPS54302 3A Buck Converter"
  manufacturer: "Texas Instruments"

pins:
  VIN:
    name: "VIN"
    net: "VIN"
    role: power          # Power input rail
  VOUT:
    name: "VOUT"
    net: "VOUT"
    role: power          # Power output rail
  GND:
    name: "GND"
    net: "GND"
    role: ground         # Current return / reference
  EN:
    name: "EN"
    net: "EN"
    # role: signal (default - measured/stimulated)

characteristics:
  output_voltage:
    function: dc_voltage   # MeasurementFunction enum
    direction: output      # DUT provides this signal
    units: V
    pin: VOUT
    conditions:
      - nominal: 3.3
        tolerance_pct: 1
        vin: 5.0
        load: 1.0
```

Want me to:
- [A]pprove and save
- [E]dit in UI: http://localhost:8000/products/tps54302
- [S]uggest guardbands (I'll add 5-10% margins)
- [?] Explain any characteristic
```

**After approval:**
```python
litmus(action="save", type="product", id="tps54302", content={...}, project=project_root)
```

---

## Step 2b: Recommend Instruments

**Goal:** Find catalog instruments that can measure/source the extracted characteristics.

**Your actions:**
1. Build a requirements list from the product characteristics (function + direction + range)
2. Call `litmus_match(requirements=[...], project=project_root)` to search the catalog
3. Present recommendations with coverage info
4. **Check for existing drivers:** For each recommended instrument, check if PyMeasure, InstrumentKit, or vendor SDKs have a driver (use your knowledge of these libraries). If a driver exists, note it — e.g., "PyMeasure has `pymeasure.instruments.keysight.Keysight34461A`". If not, note that a custom SCPI wrapper or stub driver will be needed.
5. Let the user pick instruments before generating station config

**Example call:**
```python
litmus_match(requirements=[
    {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"},
    {"function": "dc_voltage", "direction": "output", "range_max": 12, "units": "V"},
    {"function": "dc_current", "direction": "input", "range_max": 3, "units": "A"},
], project=project_root)
```

**Present to user:**
```
Based on your product specs, here are recommended instruments from the catalog:

**Full coverage (all requirements):**
| Instrument | Class | Covers | Driver Available? |
|------------|-------|--------|-------------------|
| Keysight 34461A | DMM | voltage input, current input | ✓ PyMeasure |
| Keysight E36312A | PSU | voltage output | ✓ PyMeasure |

**Coverage summary:**
- dc_voltage input: 34461A, MSO44, ...
- dc_voltage output: E36312A, ...
- dc_current input: 34461A, ...

Want me to:
- [A]pprove these selections and create station config
- [C]hange instrument selection
- [?] See more options for a specific requirement
```

---

## Step 3: Create Station Config

**Goal:** Configure the test station with instruments and mock values.

**Your actions:**
1. Use the instruments selected in Step 2b, or ask about available instruments / use `litmus_discover()`
2. Create station config with realistic mock values
3. Show config for approval

**CRITICAL FORMAT - Use exactly:**
```yaml
station:
  id: test_bench
  name: "Test Bench"

instruments:
  psu:
    type: power_supply
    driver: myproject.instruments.PSU
    resource: "TCPIP::192.168.1.100::INSTR"
    catalog_ref: keysight_e36312a   # Resolves capabilities + channel topology from catalog/
    channels: ["1", "2"]
    simulate: true
    mock_config:
      measure_voltage: 12.0
      measure_current: 1.0
  dmm:
    type: dmm
    driver: myproject.instruments.DMM
    resource: "TCPIP::192.168.1.101::INSTR"
    catalog_ref: keysight_34461a
    simulate: true
    mock_config:
      measure_voltage: 3.3
      measure_dc_voltage: 3.3
  eload:
    type: electronic_load
    driver: myproject.instruments.ELoad
    resource: "TCPIP::192.168.1.102::INSTR"
    catalog_ref: siglent_sdl1020x
    simulate: true
    mock_config:
      measure_current: 1.0
```

**Station config fields:**
- `type`: Instrument type (power_supply, dmm, electronic_load, oscilloscope, smu)
- `driver`: Python import path to instrument class (required)
- `resource`: VISA address for real hardware
- `catalog_ref`: Reference to catalog entry for capability/topology resolution
- `channels`: Channel keys (from catalog or explicit list)
- `simulate`: If true, uses Mock with mock_config values
- `mock_config`: Return values for mocked methods (keys = method names)

**Catalog entries** (in `catalog/`) define structured channel topology:
- Terminals: `[hi, lo]`, `[hi, lo, sense_hi, sense_lo]`, `[signal]`
- Ground topology: `floating` (PSU), `shared` (DMM, scope), `earth`
- Connector type: `binding_post`, `bnc`, `banana`, `triax`
- Readback: `readback: true` on PSU/eload input caps (excluded from auto-matching)

**Present to user:**
```
I'll create a station config for testing:

**Station:** test_bench

**Instruments:**
| Name  | Driver                        | Resource                      | Mock Config               |
|-------|-------------------------------|-------------------------------|---------------------------|
| psu   | myproject.instruments.PSU     | TCPIP::192.168.1.100::INSTR   | measure_voltage: 12.0     |
| dmm   | myproject.instruments.DMM     | TCPIP::192.168.1.101::INSTR   | measure_voltage: 3.3      |
| eload | myproject.instruments.ELoad   | TCPIP::192.168.1.102::INSTR   | measure_current: 1.0      |

**Mock config** values are returned by methods when `simulate: true` or `--mock-instruments`.

Want me to:
- [A]pprove and save
- [M]odify instruments
- [D]iscover connected instruments
- [?] Explain mock configuration
```

**After approval:**
```python
litmus(action="save", type="station", id="test_bench", content={...}, project=project_root)
```

**Also create instrument classes** (if not using PyMeasure):

```python
# myproject/instruments/psu.py
class PSU:
    def __init__(self, resource: str = ""):
        self.resource = resource

    def connect(self): pass
    def disconnect(self): pass
    def set_voltage(self, voltage: float): pass
    def set_current(self, current: float): pass
    def enable_output(self): pass
    def disable_output(self): pass
    def measure_voltage(self) -> float: pass
    def measure_current(self) -> float: pass
```

**conftest.py fixtures are auto-registered** — the Litmus pytest plugin automatically
creates session-scoped fixtures for each instrument role in the station config. No
conftest.py boilerplate needed. Tests can directly use `psu`, `dmm`, `eload` as fixture
parameters.

---

## Step 4: Generate Tests

**Goal:** Create pytest test code that exercises all characteristics.

**Your actions:**
1. Generate test code based on spec
2. Create config.yaml with limits and mock values
3. Show the code for review

**MUST create BOTH files:**

**File 1: Test Code (tests/test_tps54302.py)**

```python
from litmus.execution import litmus_test


@litmus_test
def test_output_voltage(context, psu, dmm):
    """Measure output voltage at specified input."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()


@litmus_test
def test_quiescent_current(context, psu):
    """Measure quiescent current in uA."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    current_a = psu.measure_current()
    return current_a * 1e6  # Convert to uA


@litmus_test
def test_load_regulation(context, psu, dmm, eload):
    """Output voltage under load."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    eload.set_current(context.inputs["load"])
    eload.enable()
    vout = dmm.measure_dc_voltage()
    eload.disable()
    return vout
```

**File 2: Config with Limits and Mocks (tests/config.yaml)**

```yaml
test_output_voltage:
  vectors:
    - vin: 12.0
  _mock:
    dmm.measure_voltage: 3.3
    psu.measure_current: 0.001
  limits:
    test_output_voltage:
      low: 3.267
      high: 3.333
      nominal: 3.3
      units: V

test_load_regulation:
  vectors:
    - vin: 12.0
      load: 0.5
      _mock:
        dmm.measure_voltage: 3.31
    - vin: 12.0
      load: 1.0
      _mock:
        dmm.measure_voltage: 3.30
    - vin: 12.0
      load: 2.0
      _mock:
        dmm.measure_voltage: 3.28
  limits:
    test_load_regulation:
      low: 3.2
      high: 3.4
      nominal: 3.3
      units: V
```

**Present to user:**
```
I've generated test code for TPS54302:

**Tests Generated (3):**
1. `test_output_voltage` - Measure VOUT at specified VIN
2. `test_quiescent_current` - Measure IQ in microamps
3. `test_load_regulation` - VOUT stability vs load current

**Mock Configuration:**
- Per-vector `_mock` values simulate realistic DUT behavior
- Load regulation shows voltage dropping slightly under load

**Files to Create:**
- tests/test_tps54302.py (test code)
- tests/config.yaml (limits + mocks)

Want me to:
- [A]pprove and save both files
- [E]dit - I'll show full code
- [M]odify test coverage (add/remove tests)
- [?] Explain any test
```

**After approval:**
```python
litmus(action="save", type="test", id="tests/test_tps54302.py", content={"code": "..."}, project=project_root)
litmus(action="save", type="test", id="tests/config.yaml", content={"code": "..."}, project=project_root)
```

---

## Step 5: Execute and Analyze

**Goal:** Run the tests and help analyze results.

**Your actions:**
1. Confirm test execution with user
2. Run with `litmus_run`
3. Analyze results and suggest next steps

**Present to user:**
```
Ready to execute tests on **test_bench** for **TPS54302**.

**Test Run:**
- Station: test_bench
- Tests: 3 tests in test_tps54302.py
- Mode: --mock-instruments (no hardware needed)
- Serial: TEST001

Start test run? [Y/n]
```

**Execute:**
```python
litmus_run(
    test="tests/test_tps54302.py",
    station="test_bench",
    serial="TEST001",
    project=project_root
)
```

**After completion:**
```
**Test Results Summary:**

| Test                | Status | Value  | Limit         |
|---------------------|--------|--------|---------------|
| test_output_voltage | ✓ PASS | 3.30V  | 3.267-3.333V  |
| test_quiescent_current | ✓ PASS | 45µA | -           |
| test_load_regulation[0.5A] | ✓ PASS | 3.31V | 3.2-3.4V |
| test_load_regulation[1.0A] | ✓ PASS | 3.30V | 3.2-3.4V |
| test_load_regulation[2.0A] | ✓ PASS | 3.28V | 3.2-3.4V |

**Summary:** 5/5 passed (100%)

Want me to:
- [V]iew detailed results in browser
- [R]e-run with different parameters
- [E]xport results to report
- [?] Investigate specific measurement
```

---

## Key Rules

1. **STOP and ASK** before each step - never proceed without approval
2. **Pass `project=`** to ALL calls after init
3. **Station format:** `type` + `driver` + `resource` + `catalog_ref` + `mock_config`
4. **mock_config keys** are method names (e.g., `measure_voltage`, `measure_current`)
5. **Create BOTH test files:** `.py` AND `config.yaml`
6. **`_mock` in config.yaml:** Per-test/per-vector mock values
7. **Standard Python math:** Instruments return `float`. Use standard Python arithmetic
8. **Pin roles:** `power` (supply rails), `ground` (return), `signal` (default), `reference`
9. **Characteristics:** Use `function:` (dc_voltage, dc_current, etc.) + `direction:` (input/output)
10. **Per-step aliases:** When station has multiple instruments of same type, use `aliases:` in sequence steps to select which instrument each step uses

---

## Tips for Good Results

1. **Be specific about datasheets** - Tell me the file path or paste the content
2. **Review each step** - Don't rush; catch issues early
3. **Use the UI for complex edits** - I can give you the editor URL with `litmus_open`
4. **Ask questions** - I can explain any characteristic or test
5. **Mock values matter** - They simulate realistic DUT behavior for development

---

## Example Session

**User:** Help me create tests for the TPS54302 buck converter. The datasheet is in ~/datasheets/tps54302.pdf

**Claude:** I'll help you through the workflow. Let me start by reading that datasheet...

*[Reads file, extracts characteristics]*

I've analyzed the datasheet and found 7 electrical characteristics. Here's what I extracted:

**Product:** TPS54302 - 3A Synchronous Step-Down Converter

**Key Specs:**
| Characteristic | Value | Conditions |
|----------------|-------|------------|
| Input Voltage | 4.5-28V | - |
| Output Voltage | Adjustable | 0.6V ref |
| Output Current | 3A max | - |
| Efficiency | 95% typ | 12V→3.3V, 1A |

Would you like me to initialize a project and save this spec, or should we review/modify anything first?

**User:** Looks good, let's continue

**Claude:** I'll initialize the project at `~/litmus-tps54302`:

```python
litmus(action="init", path="~/litmus-tps54302")
```

*[Continues through each step, always asking before proceeding...]*
