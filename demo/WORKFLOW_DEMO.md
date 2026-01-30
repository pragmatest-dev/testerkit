# Litmus Workflow Demo: Datasheet to Tests

This demo shows the complete AI-assisted workflow from a component datasheet to running tests. Everything is created through conversation with Claude Desktop.

## Setup

1. Configure Claude Desktop (see CLAUDE_DESKTOP_SETUP.md)
2. Start the UI server: `uv run litmus serve`
3. Restart Claude Desktop

## The Demo Conversation

### Step 1: Start the Workflow

**You:**
```
I want to create tests for a TPS54302 DC-DC converter.
Here's the datasheet: [attach TPS54302 datasheet PDF or paste relevant sections]
```

**Claude:**
```
I'll help you set up testing for the TPS54302. Let me first check what
instruments you have available.

[Calls discover_visa_resources]

Found:
- TCPIP::192.168.1.100::INSTR - Keysight 34461A DMM
- TCPIP::192.168.1.101::INSTR - Keysight E36312A PSU

Let me create a station configuration for your test bench.

[Calls create_station]

Created station "bench_main" with:
- dmm_main: Keysight 34461A (voltage/current measurement)
- psu_input: Keysight E36312A (power supply)

Now let me analyze the datasheet...
```

### Step 2: Datasheet Analysis

Claude reads the datasheet and extracts the key electrical specifications:

**Claude:**
```
From the TPS54302 datasheet, I've extracted these key characteristics:

Output Voltage:
- Nominal: 3.3V (adjustable)
- Tolerance: ±2% over load and temperature

Input Voltage Range:
- Min: 4.5V
- Max: 28V

Efficiency:
- >90% at 1A load, VIN=12V
- >85% at 2A load, VIN=12V

Output Current:
- Max: 3A continuous

Let me create the product specification...

[Calls create_product_folder]
[Calls save_product_spec_to_folder]

Product folder created at products/tps54302/
Spec saved with 4 characteristics and 4 test requirements.

You can review and edit the spec here:
http://localhost:8000/products/tps54302
```

### Step 3: Capability Matching

**Claude:**
```
Let me check what instruments are needed to test this product...

[Calls derive_required_capabilities]

Required capabilities:
- voltage OUTPUT (to power the DUT): 4.5-28V range
- voltage INPUT (to measure output): 0-5V range
- current INPUT (to measure load current): 0-3A range

[Calls find_compatible_stations]

Your station "bench_main" is compatible!
- psu_input provides voltage OUTPUT up to 30V
- dmm_main provides voltage INPUT up to 1000V
- dmm_main provides current INPUT up to 10A

Ready to generate tests.
```

### Step 4: Test Generation

**Claude:**
```
I'll generate the test code now...

[Calls save_test_file with generated pytest code]

Created tests/test_tps54302/test_electrical.py with:
- test_output_voltage: Verify 3.3V ±2%
- test_input_voltage_range: Test at VIN=4.5V, 12V, 28V
- test_efficiency_1a: Measure efficiency at 1A load
- test_output_current_limit: Verify 3A max

[Calls save_test_sequence]

Created sequence tps54302_validation with 4 test steps.

You can preview the sequence:
http://localhost:8000/sequences/tps54302_validation
```

### Step 5: Execution

**You:**
```
Run the tests on DUT serial TPS54302-PROTO-001
```

**Claude:**
```
[Calls run_sequence]

Starting test run...
Run ID: abc123-def456

Watch live progress at:
http://localhost:8000/live/abc123-def456

[After tests complete]
[Calls get_run_status]

Test Results:
- test_output_voltage: PASS (3.301V, limit 3.234-3.366V)
- test_input_voltage_range: PASS (all 3 conditions)
- test_efficiency_1a: PASS (91.2%, limit >90%)
- test_output_current_limit: PASS (3.05A measured)

Overall: PASS

Full results at:
http://localhost:8000/results/abc123-def456
```

## Alternate Flow: Unknown Instrument

What if you have an instrument that's not in the built-in library?

**Claude:**
```
[Calls discover_visa_resources]

Found:
- TCPIP::192.168.1.100::INSTR - Unknown (IDN: "ACME,Model9000,...")
- TCPIP::192.168.1.101::INSTR - Keysight E36312A PSU

I don't recognize "ACME Model9000". Do you have the SCPI programming
manual or a datasheet I can reference?
```

**You:**
```
Here's the SCPI reference: [attach PDF or paste relevant sections]
```

**Claude:**
```
I've analyzed the SCPI reference. The ACME Model9000 supports:

Measurement Commands:
- MEASure:VOLTage:DC? - DC voltage measurement
- MEASure:CURRent:DC? - DC current measurement
- MEASure:RESistance? - 2-wire resistance

Configuration:
- CONFigure:VOLTage:DC <range> - Set voltage range (100mV to 1000V)
- CONFigure:CURRent:DC <range> - Set current range (10uA to 10A)

I'll create an instrument definition with the actual SCPI commands.

[Calls save_instrument_library]

Created instruments/acme_model9000.yaml
```

The new instrument definition includes real SCPI commands:
```yaml
# instruments/acme_model9000.yaml
instrument:
  type: acme_model9000
  name: "ACME Model 9000 DMM"
  description: "6.5 digit precision DMM"
  driver_class: litmus.instruments.scpi.ScpiDmm  # Generic SCPI driver

capabilities:
  - name: voltage_dc
    direction: input
    domain: voltage
    range_max: 1000
  - name: current_dc
    direction: input
    domain: current
    range_max: 10
  - name: resistance
    direction: input
    domain: resistance
    range_max: 100e6

scpi_commands:
  measure_voltage_dc: "MEAS:VOLT:DC?"
  measure_current_dc: "MEAS:CURR:DC?"
  measure_resistance: "MEAS:RES?"
  configure_voltage_dc: "CONF:VOLT:DC {range}"
  configure_current_dc: "CONF:CURR:DC {range}"
```

Now Claude can generate tests that use the correct SCPI commands for your instrument.

---

## What Claude Creates

After this workflow, you'll have:

```
instruments/
  acme_model9000.yaml    # YOUR custom instrument definition

products/
  tps54302/
    manifest.yaml      # Workflow state tracking
    spec.yaml          # Product specification

stations/
  bench_main.yaml      # Your station configuration

sequences/
  tps54302_validation.yaml  # Test sequence definition

tests/
  test_tps54302/
    test_electrical.py     # Generated pytest code

results/
  abc123-def456/          # Test results (Parquet files)
```

## Traceability

Every measurement traces back to the source:

```
Result: output_voltage = 3.301V PASS
  └─ Test: test_output_voltage in test_electrical.py
       └─ Limit: 3.234V - 3.366V (derived from spec with 5% guardband)
            └─ Characteristic: output_voltage in tps54302 spec
                 └─ Datasheet: Section 7.4, Table 1
```

## You're Always in Control

Throughout the entire workflow, you have full access to:

**Files** - Everything Claude creates is a plain file you can edit:
```
products/tps54302/spec.yaml      # Edit specs directly
stations/bench_main.yaml         # Adjust instrument config
tests/test_tps54302/*.py         # Modify test code
sequences/tps54302_validation.yaml  # Change test order
```

**UI** - Take over at any point:
- http://localhost:8000/products/tps54302 - Edit product spec visually
- http://localhost:8000/stations/bench_main - Adjust station config
- http://localhost:8000/sequences/tps54302_validation - Modify sequence
- http://localhost:8000/launch - Run tests manually

**Workflow** - Claude's progress is just a manifest file:
```yaml
# products/tps54302/manifest.yaml
workflow:
  current_step: generate_tests
  completed_steps: [parse_datasheet, review_spec, derive_requirements]
```

You can edit any file, use the UI to make changes, then return to Claude and say "I updated the spec, continue from here." Claude will pick up where you left off.

---

## Key Points

1. **Datasheet First**: Claude reads the actual datasheet (PDF or text), not manual entry
2. **Deterministic Matching**: Capability matching is algorithmic, not AI guesswork
3. **Human in the Loop**: Files and UI accessible at every step - take control anytime
4. **Incremental**: Workflow state is tracked; you can resume where you left off
5. **Portable**: All artifacts are plain files that can be version controlled
