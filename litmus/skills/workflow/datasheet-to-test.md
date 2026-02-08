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

**Key principles:**

1. Never proceed to the next step without user approval.
2. **Be a guide, not a form.** At each step, use what you know about the
   specific product, datasheet, and context to offer *relevant* choices —
   not generic "approve/modify" boilerplate. The options should feel like a
   knowledgeable colleague walking them through setup.
3. Present choices as a **numbered list** at the end of your message.
   Never use inline `[A] [B] [C]` letter codes.

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

**Show the user:** Product summary, pin table, characteristics table, confidence level.
End with a contextual question based on what you found — ambiguities, design choices,
or which variant/reference design to target.

Pin roles: `power` (supply/output rails), `ground` (return/reference),
`signal` (measured/stimulated, default), `reference` (voltage ref, not driven).

---

## Step 2: Save Product Spec

**Goal:** Save the extracted spec and let user refine it.

**Your actions:**
1. Show the draft spec structure (YAML preview)
2. Highlight any uncertainties or missing fields
3. Save with `litmus(action="save", type="product", ...)` — schema is validated server-side

**Spec structure:**
```yaml
product:
  id: part_number
  name: "Full Name"
  manufacturer: "Vendor"

pins:
  PIN_NAME:
    name: "Pin label"
    net: "Net name"
    role: power          # power/ground/signal/reference

characteristics:
  char_name:
    function: dc_voltage   # MeasurementFunction enum
    direction: output      # input/output/bidir
    units: V
    pin: PIN_NAME
    conditions:
      - nominal: 3.3
        tolerance_pct: 1
```

End with specific observations about the spec — missing guardbands, additional
testable specs you noticed, anything that looks off.

---

## Step 2b: Recommend Instruments

**Goal:** Find catalog instruments that can measure/source the extracted characteristics.

**Your actions:**
1. **Consider passive components first:** Not every DUT pin needs a programmable instrument. A power resistor or voltage divider may suffice for fixed operating points. Only recommend programmable instruments (eload, SMU) when the test needs dynamic control.
2. Build a requirements list from the product characteristics (function + direction + range)
3. Call `litmus_match(requirements=[...], project=project_root)` to search the catalog
4. Present recommendations with coverage info
5. **Check for existing drivers:** PyMeasure, InstrumentKit, or vendor SDKs. Note availability.
6. Let the user pick instruments before generating station config

---

## Step 3: Create Station Config

**Goal:** Configure the test station with instruments and mock values.

**Your actions:**
1. Use the instruments selected in Step 2b, or use `litmus_discover()`
2. Build station config with realistic mock values — schema is validated server-side
3. Show config for approval

**Station config structure:**
```yaml
station:
  id: test_bench
  name: "Test Bench"

instruments:
  role_name:
    type: psu              # Short name (e.g. psu, dmm, scope, eload, fgen, smu)
    driver: pkg.module.Class
    resource: ""           # Use litmus_discover() for real addresses
    catalog_ref: catalog_id
    channels: ["1", "2"]   # Optional
    mock: true             # Start mocked, switch to real hardware later
    mock_config:
      method_name: return_value
```

**Station config fields:**
- `type`: Instrument type — freeform short name
- `driver`: Python import path to instrument class (required)
- `resource`: VISA address for real hardware
- `catalog_ref`: Reference to catalog entry for capability/topology resolution
- `channels`: Channel keys (from catalog or explicit list)
- `mock`: If true, uses Mock with mock_config values
- `mock_config`: Return values for mocked methods (keys = method names)

---

## Step 4: Generate Tests

**Goal:** Create pytest test code that exercises all characteristics.

**Your actions:**
1. Generate test code based on spec
2. Create config.yaml with limits and mock values
3. Show the code for review

**MUST create BOTH files:**

**Test code pattern:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_characteristic(context, psu, dmm):
    """What this test measures."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

**Config pattern:**
```yaml
test_characteristic:
  vectors:
    - vin: 12.0
  _mock:
    dmm.measure_voltage: 3.3
  limits:
    test_characteristic:
      low: 3.267
      high: 3.333
      nominal: 3.3
      units: V
```

---

## Step 5: Execute and Analyze

**Goal:** Run the tests and help analyze results.

**Your actions:**
1. Confirm test execution with user
2. Run with `litmus_run`
3. Show results table, analyze, suggest next steps

```python
litmus_run(test="tests/test_x.py", station="station_id",
           serial="SERIAL", project=project_root)
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
11. **conftest.py fixtures are auto-registered** — no boilerplate needed. Tests use instrument role names directly as fixture parameters.
