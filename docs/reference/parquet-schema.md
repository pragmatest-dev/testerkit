# Parquet Storage Schema

Litmus stores test results in Parquet files, organized into three tables that follow ATML (IEEE 1671) concepts.

## Directory Structure

```
results/
├── test_runs/
│   └── 2026-01-28/
│       └── <run_id>.parquet
├── vectors/
│   └── 2026-01-28/
│       └── <run_id>_vectors.parquet
└── measurements/
    └── 2026-01-28/
        └── <run_id>_measurements.parquet
```

## Table Schemas

### test_runs

One row per test execution. Corresponds to ATML `TestResults`.

| Column | Type | Description | ATML Equivalent |
|--------|------|-------------|-----------------|
| `test_run_id` | `string` | UUID of the test run | `TestResults.id` |
| `started_at` | `timestamp` | When run started | `TestResults.startDateTime` |
| `ended_at` | `timestamp` | When run ended | `TestResults.endDateTime` |
| `dut_serial` | `string` | Device serial number | `UUT.serialNumber` |
| `station_id` | `string` | Station that ran the test | `TestStation.id` |
| `test_sequence_id` | `string` | Test sequence executed | `TestDescription.name` |
| `test_phase` | `string` | Phase (production, validation, etc.) | - |
| `outcome` | `string` | Overall result (pass, fail, etc.) | `TestResults.outcome` |
| `total_steps` | `int` | Number of test steps | - |
| `failed_steps` | `int` | Number of failed steps | - |
| `total_vectors` | `int` | Number of test vectors | - |
| `failed_vectors` | `int` | Number of failed vectors | - |

### vectors

One row per parameter combination per step. Captures test conditions.

| Column | Type | Description | ATML Equivalent |
|--------|------|-------------|-----------------|
| `test_run_id` | `string` | FK to test_runs | - |
| `test_vector_id` | `string` | UUID of this vector | - |
| `test_step_id` | `string` | UUID of parent step | `TestGroup.id` |
| `step_name` | `string` | Test function name | `TestGroup.name` |
| `index` | `int` | 0-based vector index | - |
| `params` | `string` | JSON of input parameters | `Conditions` |
| `attempt` | `int` | Retry attempt number | - |
| `max_attempts` | `int` | Max retry attempts | - |
| `outcome` | `string` | Vector result | `TestGroup.outcome` |
| `started_at` | `timestamp` | When vector started | - |
| `ended_at` | `timestamp` | When vector ended | - |
| `error_message` | `string` | Error details if failed | - |
| `dut_serial` | `string` | Denormalized for queries | - |
| `station_id` | `string` | Denormalized for queries | - |

### measurements

One row per measurement. Full traceability per ATML signal routing.

| Column | Type | Description | ATML Equivalent |
|--------|------|-------------|-----------------|
| `test_run_id` | `string` | FK to test_runs | - |
| `test_vector_id` | `string` | FK to vectors | - |
| `step_name` | `string` | Test function name | `Test.name` |
| `vector_index` | `int` | Vector index for context | - |
| `measurement_name` | `string` | Measurement identifier | `Data.name` |
| `value` | `float` | Measured value | `Data.value` |
| `units` | `string` | Units (V, A, %, etc.) | `Data.units` |
| `low_limit` | `float` | Lower limit | `Limit.low` |
| `high_limit` | `float` | Upper limit | `Limit.high` |
| `nominal` | `float` | Expected value | `Limit.nominal` |
| `outcome` | `string` | pass/fail/error | `Data.outcome` |
| `spec_ref` | `string` | Spec traceability | `Data.specRef` |
| `timestamp` | `timestamp` | When measured | `Data.dateTime` |
| `dut_serial` | `string` | Denormalized | `UUT.serialNumber` |
| `station_id` | `string` | Denormalized | `TestStation.id` |
| `dut_pin` | `string` | DUT connection point | `SignalPath.uutPort` |
| `instrument_channel` | `string` | Instrument channel | `SignalPath.instrumentPort` |
| `fixture_point` | `string` | Fixture routing point | `SignalPath.fixturePort` |

## ATML/IEEE 1671 Alignment

Litmus adopts ATML terminology and concepts:

| Litmus Concept | ATML Equivalent | Description |
|----------------|-----------------|-------------|
| `TestRun` | `TestResults` | Complete test execution record |
| `TestStep` | `TestGroup` | Container for related tests |
| `TestVector` | (Conditions) | Parameter combination for test |
| `Measurement` | `Data` | Single measured value |
| `DUT` | `UUT` | Unit Under Test |
| `Outcome` | `OutcomeValue` | PASS, FAIL, SKIP, ERROR, ABORTED |
| `Comparator` | `Comparator` | EQ, NE, GELE, GT, LT, etc. |
| `spec_ref` | `specRef` | Specification traceability |
| `dut_pin` | `uutPort` | Signal routing - DUT side |
| `instrument_channel` | `instrumentPort` | Signal routing - instrument side |

## Querying Data

### Python with PyArrow

```python
import pyarrow.parquet as pq

# Read all measurements for a date
table = pq.read_table("results/measurements/2026-01-28/")
df = table.to_pandas()

# Filter failed measurements
failures = df[df["outcome"] == "fail"]

# Group by DUT
by_dut = df.groupby("dut_serial")["outcome"].value_counts()
```

### Join vectors with measurements

```python
import pandas as pd

vectors = pq.read_table("results/vectors/").to_pandas()
measurements = pq.read_table("results/measurements/").to_pandas()

# Join to get params with each measurement
full = measurements.merge(
    vectors[["test_vector_id", "params"]],
    on="test_vector_id",
    how="left"
)

# Parse params JSON
import json
full["params"] = full["params"].apply(json.loads)
```

### Query by traceability

```python
# Find all measurements on a specific DUT pin
pin_data = df[df["dut_pin"] == "J1.3"]

# Find all measurements from a specific instrument
inst_data = df[df["instrument_channel"].str.startswith("CH")]

# Find measurements linked to a spec
spec_data = df[df["spec_ref"].str.contains("output_voltage", na=False)]
```

## Why Three Tables?

1. **Normalization** — Vector params stored once, not per measurement
2. **Query efficiency** — Filter runs/vectors before loading measurements
3. **Flexibility** — Add measurements without duplicating context
4. **Traceability** — Clear FK relationships for audits

## Outcome Values

Per ATML/IEEE 1671:

| Value | Meaning |
|-------|---------|
| `pass` | All limits satisfied |
| `fail` | One or more limits exceeded |
| `skip` | Test was skipped |
| `error` | Test encountered an error |
| `aborted` | Test was aborted |
| `not_tested` | Test was not executed |

## Comparator Values

Per ATML/IEEE 1671:

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

## See Also

- [Data Models](models.md) — Pydantic model reference
- [Traceability](../guides/traceability.md) — ATML signal routing
- [Test Results](../guides/results.md) — Working with results
