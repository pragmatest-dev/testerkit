# Writing Test Sequences

Sequences define the order and conditions for running tests. They're the source of truth for what an operator runs in production.

## What is a Sequence?

A **sequence** is a named, ordered collection of test steps:

```yaml
# sequences/power_board_smoke.yaml
id: power_board_smoke
name: "Power Board - Smoke Test"
description: "Quick power-up verification"
test_phase: development

steps:
    - id: measure_5v_rail
      test: tests/test_power_board.py::test_measure_5v_rail
      description: "Verify 5V rail present"

    - id: measure_3v3_rail
      test: tests/test_power_board.py::test_measure_3v3_rail
      description: "Verify 3.3V rail present"
```

## Where Sequences Live

Sequences are stored in the `sequences/` folder:

```
my_project/
├── sequences/
│   ├── power_board_smoke.yaml
│   ├── power_board_full.yaml
│   └── characterization.yaml
├── tests/
│   └── test_power_board.py
└── ...
```

## Sequence Structure

### Required Fields

```yaml
id: unique_sequence_id        # Unique identifier
description: "What this tests" # For operators and reports
test_phase: development       # Required: development, validation, characterization, production
steps: []                      # List of test steps
```

### Optional Fields

```yaml
id: power_board_full
name: "Power Board - Full Test"           # Display name (defaults to id)
description: "Complete functional test"
test_phase: production                    # Required: dev, validation, characterization, production

# Scoping
product_family: power_board               # Which products this applies to

# Requirements
required_fixture: power_board_fixture     # Fixture ID required
required_station_type: bench_with_eload   # Station type required

# Execution
pytest_args: ["-v", "--tb=short"]         # Extra pytest arguments
timeout_seconds: 1800                     # Overall sequence timeout (30 min)

steps: []

# Inline dialog definitions
dialogs:
  confirm_load:
    id: confirm_load
    message: "Connect electronic load"
    dialog_type: confirm
```

## Test Steps

Each step references a pytest test by its node ID.

### Basic Step

```yaml
steps:
  - id: measure_5v_rail
    test: tests/test_power_board.py::test_measure_5v_rail
    description: "Verify 5V rail present"
```

### Step with Vectors, Limits, and Mocks

Steps carry their own test configuration — vectors, limits, mocks, and retry:

```yaml
steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    vectors:
      expand: product
      vin: [4.5, 5.0, 5.5]
      load: [0.1, 0.5, 1.0]
    limits:
      output_voltage:
        low: 3.135
        high: 3.465
        nominal: 3.3
        units: V
    mocks:
      dmm.measure_dc_voltage: 3.31
    retry:
      max_attempts: 2
      delay_seconds: 0.5
```

Per-vector mocks use `_mocks` (underscore prefix inside vector dicts):

```yaml
steps:
  - id: load_regulation
    test: tests/test_power.py::test_load_regulation
    vectors:
      - load: 0.1
        _mocks:
          dmm.measure_dc_voltage: 3.32
      - load: 0.5
        _mocks:
          dmm.measure_dc_voltage: 3.30
    limits:
      output_voltage:
        low: 3.2
        high: 3.4
```

### Step with Limit Reference

```yaml
steps:
  - id: measure_5v_rail
    test: tests/test_power_board.py::test_measure_5v_rail
    limit_ref: specs.power_board.rail_5v    # Derive from spec
```

### Step with Dialogs

```yaml
steps:
  - id: load_test
    test: tests/test_power_board.py::test_load_5v
    pre_dialog: confirm_load_connected      # Show before test
    post_dialog: inspect_thermal            # Show after test

dialogs:
  confirm_load_connected:
    id: confirm_load_connected
    message: "Connect electronic load to 5V output"
    dialog_type: confirm

  inspect_thermal:
    id: inspect_thermal
    message: "Check that regulator is not overheating"
    dialog_type: confirm
```

### Step with Retry

```yaml
steps:
  - id: flaky_measurement
    test: tests/test_power.py::test_noise_floor
    retry:
      max_attempts: 3
      delay_seconds: 0.5
      strategy: on_fail    # always | on_fail | dialog | custom
```

### Step with Skip Condition

```yaml
steps:
  - id: measure_5v_rail
    test: tests/test_power_board.py::test_measure_5v_rail

  - id: measure_3v3_rail
    test: tests/test_power_board.py::test_measure_3v3_rail
    skip_on: [measure_5v_rail]    # Skip if 5V failed
```

### Step with Instrument Aliases

```yaml
steps:
  - id: precision_cal
    test: tests/test_cal.py::test_voltage
    aliases:
      dmm: precision_dmm       # This step uses the 6.5-digit DMM

  - id: quick_screen
    test: tests/test_screen.py::test_voltage
    aliases:
      dmm: fast_dmm            # This step uses the 4.5-digit DMM
```

Aliases remap fixture names to station instrument roles per step. Without `aliases`, fixture names map directly to station role names. Alias targets must exist in the station's `instruments:` section.

## Sequence Composition

Sequences can include other sequences as steps.

### Composing Sequences

```yaml
# sequences/power_board_full.yaml
id: power_board_full
name: "Power Board - Full Test"
description: "Complete functional test"
test_phase: production

steps:
    # Run the smoke test sequence first
    - id: smoke_tests
      sequence: power_board_smoke    # References another sequence
      description: "Run smoke tests first"

    # Then continue with full tests
    - id: load_test_5v
      test: tests/test_power_board.py::test_load_5v

    - id: load_test_3v3
      test: tests/test_power_board.py::test_load_3v3
```

When the sequence runs, `smoke_tests` expands to all steps in `power_board_smoke`.

### Why Compose?

1. **Reuse** — Common tests (smoke, calibration) defined once
2. **Layering** — Build comprehensive sequences from simple ones
3. **Maintenance** — Fix a bug in one place, all compositions updated

## Test Phases

The `test_phase` field indicates when this sequence runs:

| Phase | Purpose | Typical Duration |
|-------|---------|------------------|
| `validation` | Engineering validation, characterization | Hours to days |
| `characterization` | Data collection across conditions | Hours |
| `production` | Manufacturing test, pass/fail | Minutes |

```yaml
id: power_board_char
test_phase: characterization
description: "Characterize output across temperature"
# ...
```

## Dialog Types

Dialogs pause execution for operator interaction.

### Confirm Dialog

```yaml
dialogs:
  confirm_load:
    id: confirm_load
    message: "Connect electronic load to J1"
    dialog_type: confirm
    timeout_seconds: 60    # Optional: auto-fail after 60s
```

### Choice Dialog

```yaml
dialogs:
  select_fixture:
    id: select_fixture
    message: "Which fixture revision?"
    dialog_type: choice
    choices:
      - "Rev A (old)"
      - "Rev B (current)"
      - "Rev C (new)"
```

### Input Dialog

```yaml
dialogs:
  enter_serial:
    id: enter_serial
    message: "Enter DUT serial number"
    dialog_type: input
```

## Complete Example

```yaml
# sequences/power_board_production.yaml
id: power_board_production
name: "Power Board Production Test"
description: "Manufacturing test for power boards"
product_family: power_board
test_phase: production
required_fixture: power_board_v2
required_station_type: bench_with_eload
timeout_seconds: 600    # 10 minute limit

steps:
    # Quick checks first
    - id: check_continuity
      test: tests/test_power_board.py::test_continuity
      description: "Verify fixture contact"

    # Power-up sequence
    - id: power_up
      test: tests/test_power_board.py::test_power_up
      pre_dialog: confirm_no_shorts
      retry:
        max_attempts: 2
        strategy: on_fail

    # Voltage rails
    - id: measure_5v
      test: tests/test_power_board.py::test_5v_rail
      skip_on: [power_up]

    - id: measure_3v3
      test: tests/test_power_board.py::test_3v3_rail
      skip_on: [power_up]

    # Load tests
    - id: load_test
      test: tests/test_power_board.py::test_load_regulation
      pre_dialog: confirm_load_connected

    # Efficiency
    - id: efficiency
      test: tests/test_power_board.py::test_efficiency
      limit_ref: specs.power_board.efficiency

  dialogs:
    confirm_no_shorts:
      id: confirm_no_shorts
      message: "Confirm no shorts on board before power-up"
      dialog_type: confirm

    confirm_load_connected:
      id: confirm_load_connected
      message: "Connect electronic load to 5V output"
      dialog_type: confirm
```

## Running Sequences

### From Operator UI

1. Start the UI: `litmus serve`
2. Navigate to `/sequences`
3. Select a sequence
4. Click "Run"

### From CLI (via pytest)

```bash
pytest tests/ --sequence=power_board_production --station=bench_1 --dut-serial=SN001 -v
```

When `--sequence` is active, step config (vectors, limits, mocks, retry) overrides any inline decorator config.

### From MCP/AI

The MCP server exposes `litmus_run` for AI agents to execute sequences.

## Best Practices

1. **Use descriptive IDs** — `power_board_smoke` not `seq1`

2. **Add descriptions** — Help operators understand what each step does

3. **Use skip_on wisely** — Skip dependent tests when prerequisites fail

4. **Compose for reuse** — Build production tests from validated smoke tests

5. **Scope to products** — Use `product_family` to filter sequences in UI

6. **Set timeouts** — Prevent runaway tests from blocking production

7. **Include dialogs** — Guide operators through setup and inspection

8. **Version with code** — Sequences are YAML, commit them to git

## See Also

- [Test Limits](./limits.md) — How limits work with sequences
- [Vector Expansion](./vector-expansion.md) — Test parameterization
- [Operator UI](./operator-ui.md) — Running sequences from UI
