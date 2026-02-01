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
| Name | Type   | Description |
|------|--------|-------------|
| VIN  | power  | Input voltage |
| SW   | power  | Switch node |
| VOUT | power  | Output voltage |
| GND  | power  | Ground |
| EN   | signal | Enable |

**Characteristics (7):**
| Name           | Direction | Value      | Conditions        |
|----------------|-----------|------------|-------------------|
| input_voltage  | input     | 4.5-18V    | -                 |
| output_voltage | output    | 3.3V ±1%   | Vin=5V, Iout=1A   |
| efficiency     | output    | ≥90%       | Vin=5V, Iout=1A   |
| ...            |           |            |                   |

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
    type: power
  VOUT:
    name: "VOUT"
    type: power

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
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

## Step 3: Create Station Config

**Goal:** Configure the test station with instruments and mock values.

**Your actions:**
1. Ask about available instruments or use `litmus_discover()`
2. Create station config with realistic mock values
3. Show config for approval

**CRITICAL FORMAT - Use exactly:**
```yaml
station:
  id: test_bench
  name: "Test Bench"

instruments:
  psu:
    type: psu
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 12.0
      current: 1.0
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.101::INSTR"
    mock_config:
      voltage: 3.3
  eload:
    type: eload
    resource: "TCPIP::192.168.1.102::INSTR"
    mock_config:
      current: 1.0
```

**Valid instrument types:** `psu`, `dmm`, `eload`, `scope` (use exactly these)

**Present to user:**
```
I'll create a station config for testing:

**Station:** test_bench

**Instruments:**
| Name  | Type  | Resource                      | Mock Values      |
|-------|-------|-------------------------------|------------------|
| psu   | psu   | TCPIP::192.168.1.100::INSTR   | 12V, 1A          |
| dmm   | dmm   | TCPIP::192.168.1.101::INSTR   | 3.3V             |
| eload | eload | TCPIP::192.168.1.102::INSTR   | 1A               |

**Mock values** are used when running with `--mock-instruments` (no hardware needed).

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
def test_output_voltage(vector, psu, dmm):
    """Measure output voltage at specified input."""
    psu.set_voltage(vector.get("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()


@litmus_test
def test_quiescent_current(vector, psu):
    """Measure quiescent current in uA."""
    psu.set_voltage(vector.get("vin", 12.0))
    psu.enable_output()
    current_a = psu.measure_current()
    return current_a * 1e6  # Convert to uA


@litmus_test
def test_load_regulation(vector, psu, dmm, eload):
    """Output voltage under load."""
    psu.set_voltage(vector.get("vin", 12.0))
    psu.enable_output()
    eload.set_current(vector["load"])
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
3. **Station instrument types:** `psu`, `dmm`, `eload`, `scope` (exactly)
4. **Station format:** `type` + `resource` + `mock_config`
5. **Create BOTH test files:** `.py` AND `config.yaml`
6. **`_mock` in config.yaml:** Per-test/per-vector mock values
7. **Standard Python math:** Instruments return `float`. Use standard Python arithmetic

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
