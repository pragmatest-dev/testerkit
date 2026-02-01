# Parquet Storage Schema

Litmus stores test results in analysis-ready Parquet files with **one row per measurement** and all metadata denormalized for easy querying with DuckDB, Spark, Polars, Pandas, etc.

## Design Philosophy

**The framework automatically captures ALL metadata when a measurement is produced.**

When `measure()` is called, Litmus knows:
- Station, instruments, channels, VISA addresses
- Fixture routing, DUT pins
- Product spec, characteristics, limits
- Input conditions and which instruments provided them
- Operator, timestamps, sequence

This is captured automatically—no user effort required.

## File Structure

```
results/runs/{date}/
├── {timestamp}_{serial}.parquet     # With serial (production)
├── {timestamp}.parquet              # Without serial (dev/debug)
└── {timestamp}_{serial}_raw/        # Raw data (waveforms, images)
    ├── {acquisition_id}_waveform.npy
    └── ...
```

**Key principles:**
- **UTC timestamps** — Consistent cross-timezone analysis
- **Chronological sorting** — Files sort naturally by time
- **Self-describing** — Timestamp + serial tells you exactly what's in the file
- **Portable** — Copy the file anywhere and you know what it is

## Schema Overview

One row per measurement. Every column queryable. All metadata automatic.

### Identity & Timing

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | string | UUID of the test run |
| `run_started_at` | timestamp | When run started |
| `run_ended_at` | timestamp | When run ended |
| `step_name` | string | Test function name |
| `step_index` | int32 | 0-based step index |
| `vector_index` | int32 | Vector index within step |
| `attempt` | int32 | Retry attempt number |
| `vector_started_at` | timestamp | When vector execution started |
| `vector_ended_at` | timestamp | When vector execution ended |

### Who — Operator

| Column | Type | Description |
|--------|------|-------------|
| `operator_id` | string | Operator ID (from `--operator`) |
| `operator_name` | string | Human-readable name |

### What — DUT

| Column | Type | Description |
|--------|------|-------------|
| `dut_serial` | string | Device serial number (from `--dut-serial`) |
| `dut_part_number` | string | Part number |
| `dut_revision` | string | Hardware revision |
| `dut_lot_number` | string | Manufacturing lot |

### What — Product

| Column | Type | Description |
|--------|------|-------------|
| `product_id` | string | Product ID from spec |
| `product_name` | string | Human-readable name |
| `product_revision` | string | Spec revision |

### Where — Station

| Column | Type | Description |
|--------|------|-------------|
| `station_id` | string | Station ID (from `--station`) |
| `station_type` | string | Station type/template |
| `station_location` | string | Physical location |

### Where — Fixture

| Column | Type | Description |
|--------|------|-------------|
| `fixture_id` | string | Fixture identifier |

### What — Test Context

| Column | Type | Description |
|--------|------|-------------|
| `sequence_id` | string | Test sequence ID |
| `test_phase` | string | production/engineering/debug |
| `git_commit` | string | Code version at test time |

### Configuration — Input Conditions (Dynamic `in_*`)

For each input parameter, columns are created dynamically:

| Column Pattern | Type | Description |
|----------------|------|-------------|
| `in_{param}` | float64 | Value commanded |
| `in_{param}_instrument` | string | Instrument name ("psu_main") |
| `in_{param}_resource` | string | VISA address at test time |
| `in_{param}_channel` | string | Channel on instrument |
| `in_{param}_dut_pin` | string | DUT pin driven |
| `in_{param}_fixture_point` | string | Fixture routing point |

**Example:** For a test with `vin` and `load` inputs:
- `in_vin`, `in_vin_instrument`, `in_vin_resource`, `in_vin_channel`
- `in_load`, `in_load_instrument`, `in_load_resource`, `in_load_channel`

#### Naming Convention

Parameter names follow a convention that distinguishes spec-relevant conditions from implementation details:

| Type | Pattern | Examples | Notes |
|------|---------|----------|-------|
| **Spec conditions** | Bare name | `in_temperature`, `in_load` | Match spec condition keys |
| **Implementation details** | Fixture-prefixed | `in_psu.voltage`, `in_dmm.sample_count` | Stimulus/settings |

This convention is enforced by documentation, not code. When analyzing data:
- Bare names (`in_temperature`, `in_load`) are spec-relevant for condition matching
- Prefixed names (`in_psu.voltage`) are implementation details

### Observation — Context Data (Dynamic `out_*`)

Observations are measured context captured during test execution—not the commanded values (in_*), but actual readings that provide context for the measurement.

| Column Pattern | Type | Description |
|----------------|------|-------------|
| `out_{key}` | varies | Observed value (scalar, array, or path reference) |

**Examples:**
- `out_temp_probe.temperature` — Actual temperature reading (24.8°C)
- `out_temp_probe.humidity` — Humidity at time of test (45.2%)
- `out_scope.waveform` — Raw waveform data or path reference

**Usage in test code:**
```python
def test_output_voltage(psu, dmm, temp_probe, vector_context):
    # Log environmental observations
    vector_context.observe("temp_probe.temperature", temp_probe.read())
    vector_context.observe("temp_probe.humidity", temp_probe.read_humidity())

    # Configure stimulus (if tracking actual applied values)
    vector_context.configure("psu.actual_voltage", psu.read_voltage())

    # THE measurement
    return dmm.measure_dc_voltage()
```

**Large arrays** (waveforms, images) can be stored in the `_raw` directory:
- Small arrays: Stored inline as JSON in the column
- Large arrays: Saved to `_raw/{vector_id}_{key}.npy`, column contains path reference

### Measurement — Core

| Column | Type | Description |
|--------|------|-------------|
| `measurement_name` | string | "vout", "iout", "efficiency" |
| `measurement_timestamp` | timestamp | When measured |
| `value` | float64 | Measured value (always scalar) |
| `units` | string | Units (V, A, %, etc.) |
| `outcome` | string | pass/fail/error |
| `low_limit` | float64 | Lower limit |
| `high_limit` | float64 | Upper limit |
| `nominal` | float64 | Expected value |
| `comparator` | string | GELE, EQ, GT, etc. |

### Spec Traceability

| Column | Type | Description |
|--------|------|-------------|
| `spec_id` | string | Characteristic ID for structured queries (e.g., "output_voltage") |
| `spec_ref` | string | Human-readable reference with conditions (e.g., "Table 4.2 @ temp=25") |

**`spec_id`** enables structured queries:
```sql
-- Find all measurements for a specific characteristic
SELECT * FROM results WHERE spec_id = 'output_voltage';

-- Yield by characteristic across all products
SELECT spec_id, product_id, AVG(CASE WHEN outcome='pass' THEN 1.0 ELSE 0.0 END) as yield
FROM results
GROUP BY spec_id, product_id;
```

**`spec_ref`** provides human-readable traceability for reports and documentation.

### Measurement Signal Path

| Column | Type | Description |
|--------|------|-------------|
| `meas_dut_pin` | string | DUT pin measured |
| `meas_fixture_point` | string | Fixture routing point |
| `meas_instrument` | string | Instrument name ("dmm_main") |
| `meas_instrument_resource` | string | VISA address |
| `meas_instrument_channel` | string | Channel ("CH1") |

### Rollup Outcomes

| Column | Type | Description |
|--------|------|-------------|
| `vector_outcome` | string | Did this vector pass/fail |
| `run_outcome` | string | Did the entire run pass/fail |

### Custom Metadata

Test architects can add custom columns via `run_context`:

```python
def test_example(run_context, psu, dmm):
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("fixture_serial", "FIX-001")
    run_context.set("ambient_temp", 23.5)
    # ...
```

These become columns in the Parquet file:
- `operator_badge`
- `fixture_serial`
- `ambient_temp`

## File-Level Metadata

Config snapshots are stored in Parquet file metadata (not columns) for reconstruction:

| Key | Description |
|-----|-------------|
| `station_config_yaml` | Full station YAML snapshot |
| `product_spec_yaml` | Full spec YAML snapshot |
| `fixture_config_yaml` | Full fixture YAML snapshot |
| `test_config_yaml` | Full test config YAML snapshot |
| `litmus_version` | Litmus version |
| `schema_version` | Schema version (2.0) |

Access with PyArrow:
```python
import pyarrow.parquet as pq
import yaml

pf = pq.ParquetFile("results/runs/2026-01-15/20260115T143025Z_SN001/measurements.parquet")
metadata = pf.schema_arrow.metadata

station_config = yaml.safe_load(metadata[b"station_config_yaml"])
product_spec = yaml.safe_load(metadata[b"product_spec_yaml"])
```

## Querying Examples

### Load and Analyze a Run

```python
import pandas as pd

df = pd.read_parquet("results/runs/2026-01-15/20260115T143025Z_SN001/measurements.parquet")

# Filter to specific test step
vout_tests = df[df["step_name"] == "test_output_voltage"]

# Analyze by input condition
print(vout_tests.groupby("in_vin")["value"].mean())

# Find failures with full context
failures = df[df["outcome"] == "fail"]
print(failures[["step_name", "measurement_name", "value", "in_vin", "meas_instrument"]])
```

### Big Data Queries (DuckDB)

```sql
-- Find yield by product and station across ALL runs
SELECT
    product_id,
    station_id,
    measurement_name,
    COUNT(*) as total,
    SUM(CASE WHEN outcome = 'pass' THEN 1 ELSE 0 END) as passed,
    ROUND(100.0 * passed / total, 2) as yield_pct
FROM read_parquet('results/runs/**/*.parquet')
GROUP BY 1, 2, 3
ORDER BY yield_pct ASC;

-- Which instrument had the most failures?
SELECT meas_instrument, meas_instrument_resource, COUNT(*) as failures
FROM read_parquet('results/runs/**/*.parquet')
WHERE outcome = 'fail'
GROUP BY 1, 2
ORDER BY failures DESC;

-- Correlation: does input voltage affect output?
SELECT
    in_vin,
    AVG(value) as avg_vout,
    STDDEV(value) as std_vout
FROM read_parquet('results/runs/**/*.parquet')
WHERE measurement_name = 'vout'
GROUP BY in_vin
ORDER BY in_vin;
```

### Schema Auto-Discovery

Parquet is self-describing—schema is embedded in each file:

| Platform | Command |
|----------|---------|
| DuckDB | `DESCRIBE SELECT * FROM read_parquet('file.parquet')` |
| Spark | `spark.read.parquet("path/").printSchema()` |
| Polars | `pl.read_parquet("file.parquet").schema` |
| Pandas | `pd.read_parquet("file.parquet").columns` |

## Dynamic Schema

**Schema varies per test—this is correct and unavoidable.**

Different tests have different:
- Configuration parameters (`in_*`): vin, load, temp, duty_cycle, ...
- Observations (`out_*`): temp_probe.temperature, scope.waveform, ...
- Measurements: vout, iout, efficiency, ...
- Custom metadata

When querying across runs, big data platforms handle this automatically:

```sql
-- DuckDB/Spark union schemas; missing columns become NULL
SELECT station_id, measurement_name, outcome, in_vin, in_temp, out_temp_probe.temperature
FROM read_parquet('results/runs/**/*.parquet')
WHERE in_vin IS NOT NULL  -- filter to tests that used vin
```

## Retry Handling

All retry attempts are stored. Each retry is a separate row with same `vector_index`, different `attempt`:

```
vector_index | attempt | measurement_name | value | outcome
0            | 1       | vout             | 3.50  | fail
0            | 2       | vout             | 3.48  | fail
0            | 3       | vout             | 3.30  | pass
```

Filter to final attempt with window functions or `WHERE attempt = (SELECT MAX(attempt) ...)`.

## ATML/IEEE 1671 Alignment

| Litmus Concept | ATML Equivalent |
|----------------|-----------------|
| `TestRun` | `TestResults` |
| `TestStep` | `TestGroup` |
| `TestVector` | (Conditions) |
| `Measurement` | `Data` |
| `DUT` | `UUT` |
| `Outcome` | `OutcomeValue` |
| `Comparator` | `Comparator` |
| `meas_dut_pin` | `uutPort` |
| `meas_instrument_channel` | `instrumentPort` |

## Outcome Values

| Value | Meaning |
|-------|---------|
| `pass` | All limits satisfied |
| `fail` | One or more limits exceeded |
| `skip` | Test was skipped |
| `error` | Test encountered an error |
| `aborted` | Test was aborted |

## Comparator Values

| Comparator | Pass Condition |
|------------|----------------|
| `GELE` | `low <= value <= high` (default) |
| `GELT` | `low <= value < high` |
| `GTLE` | `low < value <= high` |
| `GTLT` | `low < value < high` |
| `EQ` | `value == nominal` |
| `NE` | `value != nominal` |
| `GE` | `value >= low` |
| `GT` | `value > low` |
| `LE` | `value <= high` |
| `LT` | `value < high` |

## Context API

The `Context` provides methods for recording `in_*` and `out_*` data during test execution. Context is hierarchical with scoped inheritance:

- **Run level**: Data visible to all steps and vectors
- **Step level**: Data visible to all vectors in that step
- **Vector level**: Data visible only to that vector

Data set at parent level is inherited by children. Children can override parent values locally.

```python
def test_output_voltage(psu, dmm, temp_probe, harness):
    ctx = harness.context  # Current active context (vector > step > run)

    # Semantic methods (preferred)
    ctx.configure("psu.voltage", 5.0)              # → in_psu.voltage
    ctx.observe("temp_probe.temperature", 24.8)    # → out_temp_probe.temperature

    # Explicit aliases
    ctx.set_in("psu.voltage", 5.0)
    ctx.set_out("temp_probe.temperature", 24.8)

    # Bulk operations
    ctx.configure_all({"psu.voltage": 5.0, "eload.current": 0.8})
    ctx.observe_all({"temp_probe.temperature": 24.8, "temp_probe.humidity": 45})

    # Direct set (aliases)
    ctx.set_inputs({"psu.voltage": 5.0})
    ctx.set_outputs({"temp_probe.temperature": 24.8})

    # Read back (includes inherited values from parent contexts)
    voltage = ctx.get_in("psu.voltage")
    all_inputs = ctx.inputs     # Dict of all in_* values (merged with parents)
    all_outputs = ctx.outputs   # Dict of all out_* values (merged with parents)

    return dmm.measure_dc_voltage()
```

### Context Inheritance Example

```python
# Run-level context (persists across all tests)
harness.run_context.configure("operator", "jane")

with harness.step():
    # Step-level context (inherits from run)
    harness.context.configure("fixture.id", "FIX-01")

    with harness.run_vector(Vector(temp=25)):
        # Vector context inherits from step and run
        harness.context.inputs
        # → {"operator": "jane", "fixture.id": "FIX-01", "temp": 25}

    with harness.run_vector(Vector(temp=85)):
        # Fresh vector context, still inherits step and run
        harness.context.inputs
        # → {"operator": "jane", "fixture.id": "FIX-01", "temp": 85}
```

**Note:** Vector params from config are automatically populated as `in_*` columns. Use `configure()` to add implementation details (fixture-prefixed names) or readback values.

## See Also

- [Data Models](models.md) — Pydantic model reference
- [Traceability](../guides/traceability.md) — Signal path traceability
- [Test Harness](../integration/harness.md) — Recording measurements
