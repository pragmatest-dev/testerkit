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
3. **PRESENT CHOICES AS A NUMBERED LIST** at the end of your message.
   - ✅ CORRECT: "What would you like to do?\n\n1. Approve and save\n2. Edit..."
   - ❌ NEVER: Inline `[A] [B] [C]` or `(A) (B) (C)` letter codes
   - ❌ NEVER: Embed choices mid-paragraph or in narrative text

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

## CRITICAL: User Prompting — ALWAYS Use `ask_user_input_v0`

**At EVERY approval gate, you MUST use the `ask_user_input_v0` tool** to present choices as interactive widgets.
NEVER present options as text like `[A]pprove [E]dit [R]egenerate` — always use the tool.

### When to Use `ask_user_input_v0`

**Required approval gates:**
1. After Step 1 (datasheet parsing) — approve extracted characteristics
2. After Step 2 (product spec) — approve before saving
3. After Step 2b (instrument recommendations) — choose which instruments to use
4. After Step 3 (station config) — approve instruments and mock values
5. After Step 4 (test generation) — approve test code and config
6. Before Step 5 (execution) — confirm test run parameters

**Additional prompts that REQUIRE this tool:**
- Any time you need the user's target voltage, input range, design parameters
- When multiple valid approaches exist
- Before any destructive or irreversible action

### Example Usage

After presenting extracted specs:

```json
{
  "questions": [
    {
      "question": "Does the extracted spec look correct?",
      "type": "single_select",
      "options": [
        "Approve and continue to station setup",
        "Edit — I need to adjust the characteristics",
        "Regenerate — focus on different specs",
        "Ask — I have questions about the extraction"
      ]
    }
  ]
}
```

**Do NOT proceed without a response.** Always wait for the user to click a button, never assume approval.

---

## Decision Questions (Contextual, Not Boilerplate)

**DO NOT use generic boilerplate.** Ask contextual, knowledgeable questions specific
to what you found. You're a colleague with expertise in hardware testing.

✅ **GOOD:** "I see three output voltage specs at different loads. Should we test all three conditions, or focus on the worst-case scenario at full load?"

✅ **GOOD:** "The datasheet doesn't specify accuracy requirements for the quiescent current. Do you have an internal requirement, or should we measure it and use your production baseline as limits?"

❌ **BAD:** "Would you like to: (1) Approve (2) Edit (3) Continue?"

❌ **BAD:** "Should we test all characteristics or a subset? [A] All [B] Subset [C] Ask"

**Format:** Always present numbered choices at the END of your message, after explanation.
Never inline `[A]`, `(A)`, or `→` styles.

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

**Show the user:**
1. Product summary with part number, name, datasheet info
2. Pin table (name, role, net, purpose)
3. Characteristics table (name, function, direction, nominal value, test conditions)
4. Confidence assessment (0-100%, list any ambiguities or uncertain specs)
5. **Ask a contextual follow-up** — a knowledgeable colleague question specific to what you found

**Example (good):** If datasheet shows multiple output voltages, ask: "I see three output options (3.3V, 5V, 12V). Are you designing for a specific configuration, or should we test all three?"

**Example (good):** If specs are incomplete: "The protection threshold specs are vague. Do you have internal thresholds, or should we use the typical values from Figure 7 with reasonable margin?"

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

**Then ask a contextual question**, e.g.:
- "I notice the efficiency spec varies with load. Should we test at all three load points, or focus on the worst-case?"
- "The thermal limits assume natural convection. Are you adding a heatsink in your design?"
- "Some parametric specs have 'typical' but no min/max. Should I use these as nominal with a reasonable tolerance, or get those limits from you?"

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

**Then ask about instrument selection**, e.g.:
- "I found [X] and [Y] can measure the output voltage. [X] is more accurate but slower. Which fits your test tempo better?"
- "For the load testing, an electronic load (Keysight [model]) can sweep 0-3A. Does your bench have one, or should I mock it?"
- "The enable threshold test needs a precision voltage source. Do you have access to the [model] in your lab?"

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
