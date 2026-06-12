# Parquet Storage Schema

Each Litmus run produces **one Parquet file**. Every row carries an explicit `record_type` discriminator with one of three values:

- `record_type = 'run'` — exactly one row per file. Carries run-level identity, timing, outcome, plus UUT / station / project / git / environment context.
- `record_type = 'step'` — one row per `(step_path, vector_index)` execution. Step identity, timing, outcome, dynamic `in_*` / `out_*` columns. Measurement columns are NULL.
- `record_type = 'measurement'` — one row per recorded measurement. Carries the measurement payload plus the same denormalized step + run + UUT + station + fixture context as the corresponding step row.

Step and measurement rows share grain `(run_id, step_path, vector_index)`; measurement rows are further keyed by `measurement_name`. A step that records N measurements emits 1 step row + N measurement rows.

The canonical schema lives at `src/litmus/data/schemas.py` (`RUN_ROW_SCHEMA`); this page is a human-readable mirror of it.

## File layout

```
<data_dir>/runs/{date}/
├── {timestamp}_{serial}.parquet           # Run row + all step + measurement rows for one run
├── {timestamp}.parquet                    # Same shape, no UUT serial (dev runs)
└── {timestamp}_{serial}_ref/              # Reference data (waveforms, images, files)
    ├── {vector_id}_scope_waveform.npz
    ├── {vector_id}_camera_image.png
    └── ...
```

Timestamps are UTC and sort naturally. DuckDB / Spark / Polars / Pandas all read the file directly with `read_parquet`.

## Discriminator

| Column | Type | Description |
|--------|------|-------------|
| `record_type` | string | `'run'`, `'step'`, or `'measurement'` |

Every query starts here. Three values:
- `run` — one row per run carrying run-level metadata (start/end timestamps, UUT serial, station, outcome).
- `step` — one row per (step, vector) combination.
- `measurement` — one row per measurement name within a (step, vector).

To list steps: `WHERE record_type = 'step'`. To list measurements: `WHERE record_type = 'measurement'`. All kinds: omit the filter.

## Identity & timing

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | string | Session UUID — groups runs that ran together in one `litmus serve` / `pytest` invocation |
| `run_id` | string | Run UUID — primary key for the run |
| `slot_id` | string | Multi-UUT slot ID (NULL for single-UUT runs) |
| `run_started_at` | timestamp[us, UTC] | When the run started |
| `run_ended_at` | timestamp[us, UTC] | When the run ended |
| `step_name` | string | Test function or class name |
| `step_index` | int64 | 0-based step order within the run |
| `step_path` | string | Hierarchical path, e.g. `TestPower/test_efficiency` |
| `parent_path` | string | Container path; empty for root steps. Enables tree reconstruction without joins. |
| `step_started_at` | timestamp[us, UTC] | Step start (NULL for unrun planned steps) |
| `step_ended_at` | timestamp[us, UTC] | Step end |
| `step_node_id` | string | pytest node id (`tests/test_power.py::TestPower::test_efficiency`) |
| `step_module` | string | Module name |
| `step_file` | string | Source file path |
| `step_class` | string | Class name (NULL for module-level functions) |
| `step_function` | string | Function name |
| `step_markers` | string | Marker payload summary |
| `step_vector_count` | int32 | Total planned vectors for this step (1 for non-swept) |
| `vector_index` | int64 | 0-based index within the step's sweep matrix |
| `vector_retry` | int64 | 0-based retry counter (0 = first execution) |
| `vector_started_at` | timestamp[us, UTC] | Vector start |
| `vector_ended_at` | timestamp[us, UTC] | Vector end |

## Who — operator

| Column | Type | Description |
|--------|------|-------------|
| `operator_id` | string | From `--operator` or env var |
| `operator_name` | string | Human-readable name |

## What — UUT

| Column | Type | Description |
|--------|------|-------------|
| `uut_serial` | string | From `--uut-serial` |
| `uut_part_number` | string | Operator-facing part identifier (NOT `part_id`) |
| `uut_revision` | string | Hardware revision |
| `uut_lot_number` | string | Manufacturing lot |

## What — part spec

| Column | Type | Description |
|--------|------|-------------|
| `part_id` | string | Internal part identifier from the part YAML |
| `part_name` | string | Human-readable part name |
| `part_revision` | string | Spec revision |

## Where — station

| Column | Type | Description |
|--------|------|-------------|
| `station_id` | string | Station config id |
| `station_name` | string | Human-readable station name |
| `station_type` | string | Station type (template) |
| `station_location` | string | Physical location |
| `station_hostname` | string | Operator-facing identifier for the physical bench |

## Where — fixture

| Column | Type | Description |
|--------|------|-------------|
| `fixture_id` | string | Fixture YAML id |

## Where — instruments (dynamic `step_instruments_*`)

Per-step instrument identity, captured from the pytest fixtures the test actually used. All columns are `list[string]` (one entry per instrument) and arrays stay in parallel order.

| Column | Type | Description |
|--------|------|-------------|
| `step_instruments_name` | list[string] | Role names (e.g. `["dmm", "psu"]`) |
| `step_instruments_id` | list[string] | Instrument file IDs |
| `step_instruments_driver` | list[string] | Driver class paths |
| `step_instruments_resource` | list[string] | VISA addresses |
| `step_instruments_protocol` | list[string] | Protocols (`"visa"`, `"daqmx"`, …) |
| `step_instruments_manufacturer` | list[string] | From `*IDN?` or YAML config |
| `step_instruments_model` | list[string] | Model number |
| `step_instruments_serial` | list[string] | Serial number |
| `step_instruments_firmware` | list[string] | Firmware version |
| `step_instruments_cal_due` | list[string] | Calibration due date (ISO 8601) |
| `step_instruments_cal_last` | list[string] | Last cal date (ISO 8601) |
| `step_instruments_cal_certificate` | list[string] | Cal certificate number |
| `step_instruments_cal_lab` | list[string] | Cal lab name |
| `step_instruments_mocked` | list[bool] | True if the instrument ran in mock mode |

For real hardware, identity comes from `*IDN?` at session start. For mock instruments, identity comes from the instrument YAML configs.

```sql
-- DuckDB: unnest parallel arrays for per-instrument queries
SELECT
    step_name,
    unnest(step_instruments_name) AS instrument,
    unnest(step_instruments_serial) AS serial,
    unnest(step_instruments_cal_due) AS cal_due
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'step';
```

## Test context

| Column | Type | Description |
|--------|------|-------------|
| `test_phase` | string | `production` / `characterization` / `development` |
| `project_name` | string | Project name from `litmus.yaml` |
| `git_commit` | string | Code version at test time |
| `git_branch` | string | Branch at test time |
| `git_remote` | string | Remote URL at test time |

## Input conditions (dynamic `in_*`)

For each parametrize axis or sidecar sweep parameter, the writer emits a column. Types are inferred from values.

| Column pattern | Type | Description |
|---|---|---|
| `in_{param}` | float64 / int64 / string | Value commanded for that axis |
| `in_{param}_instrument` | string | Instrument name |
| `in_{param}_resource` | string | VISA address at test time |
| `in_{param}_channel` | string | Channel on instrument |
| `in_{param}_uut_pin` | string | UUT pin driven |
| `in_{param}_fixture_connection` | string | Fixture routing connection |

**Naming convention:**

| Type | Pattern | Examples |
|------|---------|----------|
| Spec conditions | bare name | `in_temperature`, `in_load`, `in_vin` |
| Implementation details | fixture-prefixed | `in_psu.voltage`, `in_dmm.sample_count` |

Bare names are spec-relevant for condition matching; prefixed names are stimulus/settings. Convention is enforced by docs, not by the writer.

## Observations (dynamic `out_*`)

Observations are *measured* context — readings captured during the test, not commanded values.

| Column pattern | Type | Description |
|----------------|------|-------------|
| `out_{key}` | varies | Observed value (scalar, array, or file reference) |

Examples: `out_temp_probe.temperature`, `out_temp_probe.humidity`, `out_scope.waveform`.

For non-scalar payloads, the value is a `file://_ref/...` URI:

| Data Type | Storage format | Example column value |
|-----------|----------------|----------------------|
| Scalar (float / int / str / bool) | inline | `3.31` |
| `Waveform` | `.npz` with t0, dt, Y, attrs | `file://_ref/{id}_scope_waveform.npz` |
| `numpy.ndarray` | `.npy` compressed | `file://_ref/{id}_raw_samples.npy` |
| `Path` | copied, extension preserved | `file://_ref/{id}_debug_log.txt` |
| Pydantic model | `.json` | `file://_ref/{id}_protocol_trace.json` |
| `bytes` | `.bin` | `file://_ref/{id}_raw_data.bin` |

```python
from litmus.data.backends.parquet import load_file, is_file_reference

if is_file_reference(column_value):
    data = load_file(parquet_path, column_value)
```

## Measurement core (on `record_type='measurement'` rows)

| Column | Type | Description |
|--------|------|-------------|
| `measurement_name` | string | `"output_voltage"`, `"efficiency"`, ... |
| `measurement_timestamp` | timestamp[us, UTC] | When the measurement was recorded |
| `measurement_value` | float64 | Measured value (scalar; non-scalar payloads go to `_ref/` via `out_*`) |
| `measurement_units` | string | Units (`V`, `A`, `%`, ...) |
| `measurement_outcome` | string | `passed` / `failed` / `skipped` / `errored` / `aborted` / `terminated` / `done` |

## Limits (on `record_type='measurement'` rows)

| Column | Type | Description |
|--------|------|-------------|
| `limit_low` | float64 | Lower bound (NULL if no lower limit) |
| `limit_high` | float64 | Upper bound (NULL if no upper limit) |
| `limit_nominal` | float64 | Expected / target value |
| `limit_comparator` | string | `GELE`, `EQ`, `GE`, `LE`, `GELT`, `GTLE`, `GTLT`, `GT`, `LT`, `NE` |

## Spec traceability

| Column | Type | Description |
|--------|------|-------------|
| `characteristic_id` | string | Characteristic ID from the part YAML (e.g. `"output_voltage"`) |
| `spec_ref` | string | Human-readable reference with conditions (e.g. `"Table 4.2 @ temp=25"`) |

```sql
-- Yield by characteristic across all parts
SELECT characteristic_id, part_id,
       AVG(CASE WHEN measurement_outcome='passed' THEN 1.0 ELSE 0.0 END) AS yield
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'measurement'
GROUP BY characteristic_id, part_id;
```

## Measurement signal path

| Column | Type | Description |
|--------|------|-------------|
| `uut_pin` | string | UUT pin that was measured |
| `fixture_connection` | string | Fixture routing connection name |
| `instrument_name` | string | Role name of the instrument that took the measurement |
| `instrument_resource` | string | VISA address |
| `instrument_channel` | string | Channel on the instrument |

## Rollup outcomes

| Column | Type | Description |
|--------|------|-------------|
| `step_outcome` | string | Did this step pass overall |
| `vector_outcome` | string | Did this vector pass |
| `run_outcome` | string | Did the entire run pass |

## Environment traceability

| Column | Type | Description |
|--------|------|-------------|
| `python_version` | string | e.g. `"3.13.1"` |
| `litmus_version` | string | Installed Litmus version |
| `env_fingerprint` | string | Hash of the lockfile + top-level deps |

## Custom metadata

Test code can add arbitrary columns via `run_context.set()`:

```python
def test_example(run_context, psu, dmm, verify):
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("fixture_serial", "FIX-001")
    run_context.set("ambient_temp", 23.5)
    ...
```

Those become Parquet columns prefixed `custom_*` with inferred types.

## Outcome values

| Value | Meaning |
|-------|---------|
| `passed` | All limits satisfied |
| `failed` | One or more limits exceeded |
| `skipped` | Test was skipped (`pytest.skip`, marker, or session-level skip) |
| `errored` | Test errored before pass/fail could be decided |
| `terminated` | Run was terminated (keyboard interrupt, signal) |
| `aborted` | Run was aborted by operator |
| `done` | Container outcome — work finished, no measurements |

Source of truth: `src/litmus/data/models.py` (`Outcome`).

## Comparator values

| Comparator | Pass condition |
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

## Retries

All retries are stored. Each retry produces measurement rows with the same `vector_index` and an incremented `vector_retry`:

```
vector_index | vector_retry | measurement_name | measurement_value | measurement_outcome
0            | 0            | output_voltage   | 3.50              | failed   ← first execution
0            | 1            | output_voltage   | 3.48              | failed   ← first retry
0            | 2            | output_voltage   | 3.30              | passed   ← second retry
```

Filter to the final execution with `WHERE vector_retry = (SELECT MAX(vector_retry) ...)` or use the daemon's `runs` view, which already rolls `retry_count` per `(run_id, step_path, vector_index)`.

## File-level metadata

Beyond columns, each Parquet file carries metadata:

| Key | Description |
|-----|-------------|
| `environment_json` | Full environment snapshot (Python version, OS, Litmus version, top-level deps, lockfile hash) |
| `litmus_version` | Litmus version that produced this file |
| `schema_version` | Schema version (`"1.0"` at time of writing — see `SCHEMA_VERSION` in `src/litmus/data/schemas.py`) |

```python
import pyarrow.parquet as pq
from litmus.environment import EnvironmentSnapshot

pf = pq.ParquetFile("data/runs/2026-05-16/20260516T143025Z_SN001.parquet")
metadata = pf.schema_arrow.metadata
env = EnvironmentSnapshot.model_validate_json(metadata[b"environment_json"])
print(f"Python {env.python_version}, Litmus {env.litmus_version}")
```

## Querying examples

### Load a run with pandas

```python
import pandas as pd

df = pd.read_parquet("data/runs/2026-05-16/20260516T143025Z_SN001.parquet")

# Step rows
steps = df[df["record_type"] == "step"]
# Measurement rows with full context
measurements = df[df["record_type"] == "measurement"]

# Failures with full context
failures = measurements[measurements["measurement_outcome"] == "failed"]
print(failures[["step_name", "measurement_name", "measurement_value",
                "limit_low", "limit_high", "uut_pin", "instrument_name"]])
```

### Yield by station with DuckDB

```sql
SELECT
    part_id,
    station_id,
    measurement_name,
    COUNT(*) AS total,
    SUM(CASE WHEN measurement_outcome = 'passed' THEN 1 ELSE 0 END) AS passed,
    ROUND(100.0 * SUM(CASE WHEN measurement_outcome = 'passed' THEN 1 ELSE 0 END) / COUNT(*), 2) AS yield_pct
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'measurement'
GROUP BY 1, 2, 3
ORDER BY yield_pct ASC;
```

### Cross-run instrument-failure correlation

```sql
SELECT
    instrument_name,
    instrument_resource,
    COUNT(*) AS failures
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'measurement'
  AND measurement_outcome = 'failed'
GROUP BY 1, 2
ORDER BY failures DESC;
```

### Slowest steps across runs

```sql
SELECT
    step_name,
    AVG(EPOCH(step_ended_at) - EPOCH(step_started_at)) AS avg_seconds,
    COUNT(*) AS runs
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'step'
  AND step_started_at IS NOT NULL
GROUP BY step_name
ORDER BY avg_seconds DESC;
```

## ATML / IEEE 1671 alignment

| Litmus column | ATML equivalent |
|---|---|
| `TestRun` (`run_id`) | `TestResults` |
| `record_type='step'` | `TestGroup` |
| `vector_index` | (Conditions) |
| `record_type='measurement'` | `Data` |
| `UUT` (`uut_*`) | `UUT` |
| `measurement_outcome` | `OutcomeValue` |
| `limit_comparator` | `Comparator` |
| `uut_pin` | `uutPort` |
| `instrument_channel` | `instrumentPort` |

## See also

- [Models](models.md) — Pydantic model index + ERD
- [Event types](event-types.md) — the event-log payloads that source these rows
- [Measurement traceability](../../how-to/execution/traceability.md) — how the signal-path columns get populated
