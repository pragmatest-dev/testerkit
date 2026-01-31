# Datasheet to Test Workflow

You are helping the user create hardware tests from a product datasheet using Litmus. This is a **collaborative** workflow where you propose and the user approves at each step.

## Workflow Overview

```
Datasheet → Product Spec → Requirements → Station → Tests → Results
   1           2              3            4         5        6
```

**Key principle:** Never proceed to the next step without user approval. At each step:
1. Show what you found/created
2. Ask if they want to approve, edit, or regenerate

## MCP Tools Available

Use these Litmus MCP tools throughout the workflow:

**Read/Context:**
- `list_products` - List existing product specs
- `get_product_spec` - Get a product spec by ID
- `list_stations` - List available test stations
- `get_station_config` - Get station details
- `list_instrument_types` - List instrument library
- `get_instrument_library` - Get instrument capabilities

**Product Folders:**
- `create_product_folder` - Create new product folder (Step 1)
- `get_product_folder` - Get folder state and files
- `list_product_folders` - List all products in workflow
- `save_product_spec_to_folder` - Save spec to folder (Step 2)
- `complete_workflow_step` - Mark step complete and advance

**Matching (Deterministic):**
- `derive_required_capabilities` - Get requirements from spec (Step 3)
- `find_compatible_stations` - Find stations that can test product (Step 4)
- `check_station_compatibility` - Detailed match for one station

**Write:**
- `save_test_file` - Save pytest test code (Step 5)

**Execute:**
- `run_sequence` - Run tests (Step 6)
- `get_run_status` - Check results

**UI:**
- `get_editor_url` - Get URL for visual editing

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
3. Create product folder with `create_product_folder`

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
| input_voltage  | INPUT     | 4.5-18V    | -                 |
| output_voltage | OUTPUT    | 3.3V ±1%   | Vin=5V, Iout=1A   |
| efficiency     | OUTPUT    | ≥90%       | Vin=5V, Iout=1A   |
| ...            |           |            |                   |

**Confidence:** 94% (some thermal specs unclear)

Want me to:
- [A]pprove and continue
- [E]dit - I'll open the editor: {url}
- [R]egenerate with different focus
- [?] Ask me questions about specific values
```

**After approval:**
```python
complete_workflow_step(product_id, "parse_datasheet", agent="claude", confidence=0.94)
```

---

## Step 2: Review Product Spec

**Goal:** Validate the extracted spec and let user refine it.

**Your actions:**
1. Show the draft spec structure
2. Highlight any uncertainties or missing fields
3. Suggest improvements based on common patterns
4. Offer the UI editor for detailed changes

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
  pins:
    - name: VIN
      type: power
      net: input_power
  characteristics:
    output_voltage:
      direction: OUTPUT
      domain: voltage
      ...
```

Want me to:
- [A]pprove as-is
- [E]dit in UI: http://localhost:8000/products/tps54302
- [S]uggest guardbands (I'll add 5-10% margins)
- [?] Explain any characteristic
```

**After approval:**
```python
save_product_spec_to_folder(product_id, spec)
complete_workflow_step(product_id, "review_spec", agent="claude")
```

---

## Step 3: Derive Requirements

**Goal:** Determine what instrument capabilities are needed to test this product.

**This step is deterministic** - no AI judgment needed, just run the tool.

**Your actions:**
1. Call `derive_required_capabilities(product_id)`
2. Present the requirements clearly
3. Explain the direction pairing logic

**Present to user:**
```
Based on the product spec, here are the **instrument requirements**:

**Direction Pairing Logic:**
- DUT OUTPUT → Instrument must INPUT (measure what DUT provides)
- DUT INPUT → Instrument must OUTPUT (source what DUT needs)

**Required Capabilities:**

| Capability        | Direction | Why                              |
|-------------------|-----------|----------------------------------|
| voltage_dc INPUT  | measure   | Measure output_voltage (3.3V)    |
| voltage_dc OUTPUT | source    | Provide input_voltage (4.5-18V)  |
| current_dc INPUT  | measure   | Measure output_current (0-3A)    |
| current_dc OUTPUT | sink      | Load the output (electronic load)|
| power OUTPUT      | source    | Power supply for VIN             |

**Performance Requirements:**
- Voltage measurement: ±0.01V accuracy (for 1% tolerance)
- Current range: 0-3A minimum
- Power supply: 18V, 1A minimum

Ready to find compatible stations? [Y/n]
```

**After approval:**
```python
complete_workflow_step(product_id, "derive_requirements", agent="claude")
```

---

## Step 4: Select Station

**Goal:** Find a test station that has all required capabilities.

**Your actions:**
1. Call `find_compatible_stations(product_id)`
2. Rank stations by coverage and suitability
3. Let user select or suggest creating a new station

**Present to user:**
```
**Compatible Stations:**

| Station      | Coverage | Missing        | Location    |
|--------------|----------|----------------|-------------|
| bench_001    | 100%     | -              | Lab A       |
| bench_002    | 85%      | high current   | Lab B       |
| production_1 | 100%     | -              | Factory     |

**Recommended: bench_001**
- Has DMM (voltage/current measurement)
- Has PSU (18V, 3A capable)
- Has E-Load (0-10A range)
- Located in Lab A (same as you)

**Detailed Capabilities:**
```
DMM: Keithley 34461A
  - voltage_dc INPUT: ±0.0015% accuracy
  - current_dc INPUT: 0-3A range

PSU: Keysight E36312A
  - voltage_dc OUTPUT: 0-25V
  - current_dc OUTPUT: 0-3A

E-Load: BK 8600
  - current_dc OUTPUT (sink): 0-30A
```

Select station:
- [1] bench_001 (recommended)
- [2] bench_002
- [C] Create new station
- [?] Show more details
```

**After selection:**
```python
complete_workflow_step(product_id, "select_station", agent="claude",
                       notes="Selected bench_001")
```

---

## Step 5: Generate Tests

**Goal:** Create pytest test code that exercises all characteristics.

**Your actions:**
1. Generate test code based on spec and station
2. Use the `SpecContext` pattern for limit derivation
3. Include proper instrument setup and teardown
4. Show the code for review

**Present to user:**
```
I've generated test code for TPS54302:

**Test Structure:**
```
tests/test_tps54302/
  test_tps54302.py    # Main test file
```

**Tests Generated (6):**
1. `test_output_voltage` - Measure VOUT at multiple loads
2. `test_efficiency` - Calculate Pin/Pout at operating points
3. `test_input_voltage_range` - Verify operation 4.5-18V
4. `test_output_current_limit` - Check overcurrent protection
5. `test_enable_threshold` - Measure EN pin thresholds
6. `test_line_regulation` - VOUT stability vs VIN

**Code Preview:**
```python
import pytest
from litmus.products import SpecContext

@pytest.fixture
def spec_context(request):
    return SpecContext.from_yaml("products/tps54302/spec.yaml")

class TestTPS54302:
    def test_output_voltage(self, psu, dmm, eload, spec_context):
        """Verify output voltage at rated conditions."""
        # Get limits from spec
        limits = spec_context.get_limits(
            "output_voltage",
            temperature=25, load=1.0, vin=5.0
        )

        # Setup
        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.output_on()
        eload.set_current(1.0)

        # Measure
        vout = dmm.measure_voltage()

        # Verify
        assert limits.low <= vout <= limits.high, \
            f"VOUT {vout}V outside {limits.low}-{limits.high}V"
```

Want me to:
- [A]pprove and save
- [E]dit - I'll show full code
- [M]odify test coverage (add/remove tests)
- [?] Explain any test
```

**After approval:**
```python
save_test_file("test_tps54302/test_tps54302.py", code)
complete_workflow_step(product_id, "generate_tests", agent="claude")
```

---

## Step 6: Execute and Analyze

**Goal:** Run the tests and help analyze results.

**Your actions:**
1. Confirm test execution with user
2. Start test run with `run_sequence`
3. Monitor progress with `get_run_status`
4. Analyze results and suggest next steps

**Present to user:**
```
Ready to execute tests on **bench_001** for **TPS54302**.

**Pre-flight Checklist:**
- [ ] DUT connected to fixture
- [ ] Station instruments powered on
- [ ] Safety interlocks verified

**Test Sequence:** 6 tests, ~3 minutes estimated

Start test run? [Y/n]
```

**During execution:**
```
**Test Progress:** ████████░░ 4/6

| Test                  | Status  | Result    |
|-----------------------|---------|-----------|
| test_output_voltage   | ✓ PASS  | 3.301V    |
| test_efficiency       | ✓ PASS  | 91.2%     |
| test_input_voltage    | ✓ PASS  | 4.5-18V   |
| test_output_current   | ✗ FAIL  | 2.8A      |
| test_enable_threshold | Running | -         |
| test_line_regulation  | Pending | -         |
```

**After completion:**
```
**Test Results Summary:**

| Metric        | Value |
|---------------|-------|
| Total         | 6     |
| Passed        | 5     |
| Failed        | 1     |
| Pass Rate     | 83%   |

**Failed Test Analysis:**
`test_output_current_limit` - Expected ≥3A, measured 2.8A

**Possible Causes:**
1. Current limit set too low in DUT firmware
2. Thermal throttling (check DUT temperature)
3. Test setup issue (check load connection)

**Recommendations:**
- Re-run with thermal monitoring
- Check DUT against datasheet derating curves
- Verify e-load calibration

Want me to:
- [R]e-run failed tests
- [D]rill into specific result
- [E]xport results to report
- [?] Investigate failure further
```

**After completion:**
```python
complete_workflow_step(product_id, "execute_analyze", agent="claude",
                       notes="5/6 passed, investigating current limit")
```

---

## Resuming a Workflow

If the user wants to continue a previous workflow:

```python
# Check existing products
list_product_folders()

# Get specific product state
folder = get_product_folder("tps54302")
# Returns: current_step, completed_steps, files, history
```

**Present to user:**
```
**Product:** TPS54302 - 3A Buck Converter
**Progress:** ████░░░░░░ 40%

| Step               | Status    | Agent  | Notes          |
|--------------------|-----------|--------|----------------|
| Parse Datasheet    | ✓ Done    | claude | 94% confidence |
| Review Spec        | ✓ Done    | human  | Added thermal  |
| Derive Requirements| → Current | -      | -              |
| Select Station     | ○ Pending | -      | -              |
| Generate Tests     | ○ Pending | -      | -              |
| Execute & Analyze  | ○ Pending | -      | -              |

Continue from **Derive Requirements**? [Y/n]
```

---

## Tips for Good Results

1. **Be specific about datasheets** - Tell me the file path or paste the content
2. **Review each step** - Don't rush; catch issues early
3. **Use the UI for complex edits** - I can give you the editor URL
4. **Ask questions** - I can explain any characteristic or test
5. **Save progress** - Workflow state persists in the product folder

---

## Example Session

**User:** Help me create tests for the TPS54302 buck converter. The datasheet is in demo/products/tps54302/datasheet.md

**Claude:** I'll help you through the workflow. Let me start by reading that datasheet...

*[Reads file, extracts characteristics]*

I've analyzed the datasheet and found 7 electrical characteristics. Here's what I extracted:

**Product:** TPS54302 - 3A Synchronous Step-Down Converter
...

Would you like me to create a product folder with this spec, or should we review/modify anything first?
