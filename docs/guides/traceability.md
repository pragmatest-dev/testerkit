# Measurement Traceability

Litmus provides ATML-style traceability for every measurement, enabling compliance reporting, root cause analysis, and calibration tracking.

## What is ATML?

**ATML (Automatic Test Markup Language)** is an IEEE standard (IEEE 1671) for exchanging test information. It defines:

- Standard vocabulary for test outcomes (PASS, FAIL, SKIP, ERROR, etc.)
- Standard comparator types (GELE, GTLT, EQ, NE, etc.)
- Signal routing concepts (how measurements trace back to DUT pins and instruments)

Litmus adopts ATML terminology and concepts to enable interoperability with other test systems and compliance with industry standards.

## Traceability Fields

Every Measurement in Litmus includes traceability fields:

| Field | Description | Example |
|-------|-------------|---------|
| `spec_ref` | Reference to specification | `"output_voltage @ tolerance_pct=5"` |
| `dut_pin` | Which DUT pin was measured | `"J1.3"`, `"TP_VOUT"` |
| `instrument_name` | Station config instrument name | `"dmm"`, `"dmm_main"` |
| `instrument_resource` | VISA address or connection | `"TCPIP::192.168.1.100::INSTR"` |
| `instrument_channel` | Channel on the instrument | `"CH1"`, `"ai0"`, `"1"` |
| `fixture_point` | Fixture point name | `"VOUT"`, `"VIN_SENSE"` |

## The Traceability Chain

Every measurement can be traced from result back to source:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRACEABILITY CHAIN                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Measurement                                                            │
│  └── spec_ref ──────────► Product Spec (spec.yaml)                     │
│      │                     └── Datasheet section reference              │
│      │                                                                  │
│      ├── dut_pin ───────► Product Pin Definition                       │
│      │                     └── Physical location: "J1.3", net: "VOUT"  │
│      │                                                                  │
│      ├── fixture_point ─► Fixture Config (fixture.yaml)                │
│      │                     └── Maps DUT pin to instrument               │
│      │                                                                  │
│      ├── instrument_name ► Station Config (station.yaml)               │
│      │                     └── Logical name: "dmm", "psu"               │
│      │                                                                  │
│      ├── instrument_resource ► Physical Connection                      │
│      │                     └── VISA: "TCPIP::192.168.1.100::INSTR"     │
│      │                                                                  │
│      └── instrument_channel ► Instrument Channel                        │
│                            └── Specific input: "CH1", "ai0"             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Setting Traceability in Tests

### Automatic (via Fixture)

When you use the `pins` fixture, traceability is automatic:

```python
@litmus_test
def test_output_voltage(pins):
    # pins["VOUT"] knows:
    # - dut_pin (from product spec)
    # - instrument_name (from fixture)
    # - instrument_resource (from station)
    # - instrument_channel (from fixture)
    return pins["VOUT"].measure_voltage()
```

### Manual (Direct Instruments)

When using instruments directly, set traceability manually:

```python
@litmus_test
def test_output_voltage(vector, dmm, harness):
    voltage = dmm.measure_dc_voltage()

    harness.measure(
        "output_voltage",
        voltage,
        dut_pin="J1.3",
        instrument_name="dmm",
        instrument_channel="CH1",
    )
```

### Using SpecContext

For spec-driven traceability:

```python
from litmus.products import SpecContext

spec = SpecContext.from_file("products/power_board/spec.yaml")

@litmus_test
def test_output_voltage(vector, dmm, harness):
    voltage = dmm.measure_dc_voltage()

    # spec_context provides spec_ref and dut_pin automatically
    harness.measure_spec(
        "output_voltage",  # Characteristic name in spec
        voltage,
        spec_context=spec,
        instrument_name="dmm",
    )
```

## Comparators (ATML/IEEE 1671)

The `comparator` field defines how values are compared against limits:

### Range Comparators

| Comparator | Meaning | Pass Condition |
|------------|---------|----------------|
| `GELE` | Greater-equal, less-equal (default) | `low <= value <= high` |
| `GELT` | Greater-equal, less-than | `low <= value < high` |
| `GTLE` | Greater-than, less-equal | `low < value <= high` |
| `GTLT` | Greater-than, less-than | `low < value < high` |

### Single-Bound Comparators

| Comparator | Meaning | Pass Condition |
|------------|---------|----------------|
| `GE` | Greater-equal | `value >= low` |
| `GT` | Greater-than | `value > low` |
| `LE` | Less-equal | `value <= high` |
| `LT` | Less-than | `value < high` |

### Equality Comparators

| Comparator | Meaning | Pass Condition |
|------------|---------|----------------|
| `EQ` | Equal | `value == nominal` |
| `NE` | Not equal | `value != nominal` |

### Setting Comparators in config.yaml

```yaml
test_output_voltage:
  limits:
    output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      comparator: GELE  # Default: inclusive range
      units: V
      spec_ref: "output_voltage @ tolerance_pct=5"

test_minimum_current:
  limits:
    load_current:
      low: 0.1
      comparator: GE  # Only lower bound: must be >= 0.1A
      units: A

test_exact_value:
  limits:
    calibration_ref:
      nominal: 1.000
      comparator: EQ  # Exact match required
      units: V
```

## Querying Traceable Results

### By DUT Pin

```python
import pyarrow.parquet as pq

df = pq.read_table("results/measurements").to_pandas()

# Find all measurements on pin J1.3
j1_3_measurements = df[df["dut_pin"] == "J1.3"]

# Find failures on specific pin
failures = df[(df["dut_pin"] == "J1.3") & (df["outcome"] == "fail")]
```

### By Instrument

```python
# Find all measurements from the main DMM
dmm_measurements = df[df["instrument_name"] == "dmm"]

# Find measurements from specific VISA address
visa_measurements = df[df["instrument_resource"] == "TCPIP::192.168.1.100::INSTR"]
```

### By Spec Reference

```python
# Find all measurements for output_voltage spec
output_v = df[df["spec_ref"].str.contains("output_voltage", na=False)]
```

## Compliance Reporting

Traceability enables compliance reports that link:

1. **Measurement** → **Spec Requirement** (via `spec_ref`)
2. **Measurement** → **Test Equipment** (via `instrument_*` fields)
3. **Measurement** → **DUT** (via `dut_pin` and parent TestRun.dut)

Example compliance report structure:

```
Test Report: SN12345
──────────────────────────────────────────────────────
Requirement: output_voltage @ tolerance_pct=5
  Source: products/power_board/spec.yaml, Section 7.2
  DUT Pin: J1.3 (VOUT_3V3)
  Instrument: dmm (Keithley 2000)
  Resource: TCPIP::192.168.1.100::INSTR
  Channel: CH1

  Measured: 3.31 V
  Limits: 3.135 V to 3.465 V
  Result: PASS
──────────────────────────────────────────────────────
```

## Benefits of Traceability

1. **Root Cause Analysis** — When a test fails, identify exactly which instrument and channel were involved

2. **Calibration Tracking** — Link measurements to instrument calibration records via `instrument_resource`

3. **Fixture Debugging** — Verify signal routing through the fixture via `fixture_point`

4. **Specification Compliance** — Prove that measurements satisfy specific spec requirements via `spec_ref`

5. **Audit Trail** — Complete chain from measurement to DUT pin to datasheet reference

## Best Practices

1. **Always set `spec_ref`** — Link measurements to specifications for traceability

2. **Use fixtures for complex routing** — Let the framework handle traceability automatically

3. **Include `instrument_channel`** — Especially for multi-channel instruments

4. **Reference spec conditions** — Include temperature, load, etc. in `spec_ref`:
   ```yaml
   spec_ref: "output_voltage @ load=0.5A, temp=25°C"
   ```

5. **Use meaningful DUT pin names** — Match your schematic/PCB designators
