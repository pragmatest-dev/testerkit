---
name: datasheet-to-test
description: Create hardware tests from a product datasheet using Litmus MCP tools. Guides through spec extraction, station setup, and test generation.
---

# Datasheet to Test Workflow

You are helping the user create hardware tests from a product datasheet using Litmus. This is a **collaborative** workflow where you propose and the user approves at each step.

## CRITICAL: Stop and Ask at Every Step

**Never proceed without user approval.** At each step:
1. Show what you plan to do
2. Ask for approval before executing
3. Wait for response

## MCP Tools Available

Litmus provides 5 tools:

| Tool | Purpose |
|------|---------|
| `litmus(action="init", path="...")` | Initialize project, returns `project_root` |
| `litmus(action="save", type="...", id="...", content={...}, project=...)` | Save product/station/test |
| `litmus(action="read", path="...", project=...)` | Read files or templates |
| `litmus_run(test="...", station="...", serial="...", project=...)` | Execute tests |
| `litmus_open(type="...", id="...")` | Get browser URL |

**IMPORTANT:** Pass `project=<project_root>` to ALL calls after init.

---

## Workflow Steps

### Step 1: Initialize Project

```python
result = litmus(action="init", path="~/my-project")
project_root = result["project_root"]  # USE THIS IN ALL SUBSEQUENT CALLS
```

### Step 2: Extract and Save Product Spec

1. Read the datasheet
2. Extract specs (voltage, current, limits, etc.)
3. **Show extracted specs to user and ask approval**
4. Save:

```python
litmus(action="save", type="product", id="tps54302", content={
    "product": {
        "id": "tps54302",
        "name": "TPS54302 DC-DC Converter",
        "manufacturer": "Texas Instruments"
    },
    "specs": {
        "input_voltage": {"min": 4.5, "max": 28, "unit": "V"},
        "output_current": {"max": 3, "unit": "A"},
        "feedback_voltage": {"min": 0.581, "typ": 0.596, "max": 0.611, "unit": "V"}
    }
}, project=project_root)
```

### Step 3: Create Station Config

**Show config to user and ask approval before saving.**

**USE THIS EXACT FORMAT:**

```python
litmus(action="save", type="station", id="sim_bench", content={
    "station": {
        "id": "sim_bench",
        "name": "Simulated Test Bench"
    },
    "instruments": {
        "psu": {
            "type": "psu",           # MUST be: psu, dmm, eload, or scope
            "resource": "MOCK::PSU",
            "simulate": True,
            "sim_config": {
                "voltage": 12.0,
                "current": 1.0
            }
        },
        "dmm": {
            "type": "dmm",
            "resource": "MOCK::DMM",
            "simulate": True,
            "sim_config": {
                "voltage": 5.0
            }
        },
        "eload": {
            "type": "eload",
            "resource": "MOCK::ELOAD",
            "simulate": True,
            "sim_config": {
                "current": 1.0
            }
        }
    }
}, project=project_root)
```

**Valid instrument types:** `psu`, `dmm`, `eload`, `scope` (use exactly these)

### Step 4: Create Test Files

**MUST create BOTH files. Show to user and ask approval.**

**File 1: Test Code (tests/test_tps54302.py)**

**IMPORTANT:** Instruments return `Decimal`, not `float`. For unit conversions, use `Decimal("1e6")` not `1e6`.

```python
litmus(action="save", type="test", id="tests/test_tps54302.py", content={
    "code": '''from decimal import Decimal
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
    current_a = psu.measure_current()  # Returns Decimal
    return current_a * Decimal("1e6")  # Convert to uA


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
'''
}, project=project_root)
```

**File 2: Config with Limits and Mocks (tests/config.yaml)**

```python
litmus(action="save", type="test", id="tests/config.yaml", content={
    "code": '''test_output_voltage:
  vectors:
    - vin: 12.0
  _mock:
    dmm.measure_voltage: 5.0
    psu.measure_current: 0.1
  limits:
    test_output_voltage:
      low: 4.75
      high: 5.25
      nominal: 5.0
      units: V

test_load_regulation:
  vectors:
    - vin: 12.0
      load: 0.5
      _mock:
        dmm.measure_voltage: 5.02
    - vin: 12.0
      load: 1.0
      _mock:
        dmm.measure_voltage: 5.00
    - vin: 12.0
      load: 2.0
      _mock:
        dmm.measure_voltage: 4.95
  limits:
    test_load_regulation:
      low: 4.7
      high: 5.3
      nominal: 5.0
      units: V
'''
}, project=project_root)
```

### Step 5: Run Tests

**Confirm with user before running.**

```python
litmus_run(
    test="tests/test_tps54302.py",
    station="sim_bench",
    serial="TEST001",
    project=project_root
)
```

---

## Key Rules

1. **STOP and ASK** before each step - never proceed without approval
2. **Pass `project=`** to ALL calls after init
3. **Station instrument types:** `psu`, `dmm`, `eload`, `scope` (exactly)
4. **Station format:** `type` + `simulate: true` + `sim_config` (NOT `driver:`)
5. **Create BOTH test files:** `.py` AND `config.yaml`
6. **`_mock` in config.yaml:** Values mocks return during simulation
7. **Use Decimal for math:** Instruments return `Decimal`. Use `Decimal("1e6")` not `1e6`

---

## Example Interaction

**User:** Create tests for the TPS54302 from the datasheet at ~/datasheets/tps54302.pdf

**Assistant:** I'll help you create tests for the TPS54302. Let me start by initializing a project.

**Step 1: Initialize Project**

I'll create a project at `~/litmus-tps54302`:

```
litmus(action="init", path="~/litmus-tps54302")
```

Should I proceed? [Y/n]

**User:** Y

**Assistant:** *[Executes init, gets project_root]*

Project initialized at `/home/user/litmus-tps54302`.

**Step 2: Extract Product Spec**

I've read the datasheet and extracted these specifications:

| Spec | Min | Typ | Max | Unit |
|------|-----|-----|-----|------|
| Input Voltage | 4.5 | - | 28 | V |
| Output Current | - | - | 3 | A |
| Feedback Voltage | 0.581 | 0.596 | 0.611 | V |
| Switching Freq | 290 | 400 | 510 | kHz |

Should I save this product spec? [Y/n]

*[Continues step by step, always asking before each action...]*
