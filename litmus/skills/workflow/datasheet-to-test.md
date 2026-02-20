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

## MCP Tools Available

| Tool | Purpose |
|------|---------|
| `litmus(action="init", path="...")` | Initialize project, returns `project_root` |
| `litmus(action="save", type="...", id="...", content={...}, project=...)` | Save product/station/test |
| `litmus(action="read", path="...", project=...)` | Read files or templates |
| `litmus_match(product_id, station_id, project)` | Check compatibility |
| `litmus_run(test="...", station="...", serial="...", project=...)` | Execute tests |
| `litmus_open(type="...", id="...")` | Get browser URL for viewing/editing |
| `litmus(action="lookup_enum", id="FRES")` | Resolve datasheet abbreviation to enum value |
| `litmus(action="enum_reference")` | Full enum abbreviation table (markdown) |
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

**Do NOT proceed without a response.** Always wait for the user to click a button, never assume approval.

---

## Decision Questions (Contextual, Not Boilerplate)

**DO NOT use generic boilerplate.** Ask contextual, knowledgeable questions specific
to what you found. You're a colleague with expertise in hardware testing.

**Format:** Always present numbered choices at the END of your message, after explanation.
Never inline `[A]`, `(A)`, or `→` styles.

---

## Step 1: Parse Datasheet

**Goal:** Extract electrical characteristics, pins, and test conditions from the datasheet.

**Your actions:**
1. **Ask the user where to create the project** — suggest `~/litmus-<part_number>` but let them choose
2. Initialize project with `litmus(action="init", path="<user's chosen path>")`
3. Read the datasheet file the user provides
4. Use `litmus(action="lookup_enum", id="...")` to resolve datasheet abbreviations
   (e.g. "FRES" → resistance_4w, "DCV" → dc_voltage) to the correct MeasurementFunction enum values
5. Extract key information:
   - Product ID, name, description
   - Pin definitions (name, type, net)
   - Electrical characteristics (voltage, current, power, timing)
   - Test conditions (temperature, load, input voltage)
   - Performance specs with limits (nominal, min, max, tolerance)

**Show the user:**
1. Product summary with part number, name, datasheet info
2. Pin table (name, role, net, purpose)
3. Characteristics table (name, function, direction, nominal value, test conditions)
4. Confidence assessment (0-100%, list any ambiguities or uncertain specs)
5. **Ask a contextual follow-up** — a knowledgeable colleague question specific to what you found

Pin roles: `power` (supply/output rails), `ground` (return/reference),
`signal` (measured/stimulated, default), `reference` (voltage ref, not driven).

---

## Step 2: Save Product Spec

**Goal:** Save the extracted spec and let user refine it.

**Your actions:**
1. Show the draft spec structure (YAML preview)
2. Highlight any uncertainties or missing fields
3. Save with `litmus(action="save", type="product", ...)` — schema is validated server-side

Refer to `refs/product-schema.md` for the full product spec structure.

End with specific observations about the spec — missing guardbands, additional
testable specs you noticed, anything that looks off.

**Then ask a contextual question**, e.g.:
- "I notice the efficiency spec varies with load. Should we test at all three load points, or focus on the worst-case?"
- "The thermal limits assume natural convection. Are you adding a heatsink in your design?"

---

## Step 2b: Recommend Instruments

**Goal:** Find catalog instruments that can measure/source the extracted characteristics.

**Your actions:**
1. **Consider passive components first:** Not every DUT pin needs a programmable instrument. A power resistor or voltage divider may suffice for fixed operating points. Only recommend programmable instruments (eload, SMU) when the test needs dynamic control.
2. Call `litmus_match(product_id="<product_id>", project=project_root)` — the platform derives requirements from the saved product characteristics automatically. Do NOT build requirements manually.
3. Present recommendations with coverage info
4. **Check for existing drivers:** PyMeasure, InstrumentKit, or vendor SDKs. Note availability.
5. Let the user pick instruments before generating station config

---

## Step 3: Create Station Config

**Goal:** Configure the test station with instruments and mock values.

**Your actions:**
1. Use the instruments selected in Step 2b, or use `litmus_discover()`
2. Build station config with realistic mock values — schema is validated server-side
3. Show config for approval

Refer to `refs/station-schema.md` for the full station config structure.

---

## Step 4: Generate Tests

**Goal:** Create pytest test code that exercises all characteristics.

**Your actions:**
1. Generate test code based on spec
2. Create config.yaml with limits and mock values
3. Show the code for review

**MUST create BOTH files** (test .py AND config.yaml).

Refer to `refs/test-writing.md` for test code patterns and `refs/limits.md` for config/limits structure.

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
3. **Station format:** Refer to `refs/station-schema.md` for field definitions
4. **mock_config keys** are method names (e.g., `measure_voltage`, `measure_current`)
5. **Create BOTH test files:** `.py` AND `config.yaml`
6. **`_mock` in config.yaml:** Per-test/per-vector mock values
7. **Standard Python math:** Instruments return `float`. Use standard Python arithmetic
8. **Pin roles:** `power` (supply rails), `ground` (return), `signal` (default), `reference`
9. **Characteristics:** Refer to `refs/enums.md` for valid MeasurementFunction values. Use `function:` + `direction:` (input/output)
10. **Per-step aliases:** When station has multiple instruments of same type, use `aliases:` in sequence steps to select which instrument each step uses
11. **conftest.py fixtures are auto-registered** — no boilerplate needed. Tests use instrument role names directly as fixture parameters.
