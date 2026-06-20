# Parquet Storage Schema

Each Litmus run produces **one Parquet file**. The file has two layers: the at-rest format and the query projection.

**At-rest — three row types.** Every row carries an explicit `record_type` discriminator with one of three values:

- `record_type = 'run'` — exactly one row per file. Carries run-level identity, timing, outcome, plus UUT / station / project / git / environment context.
- `record_type = 'step'` — one row per `(step_path, vector_index)` execution. Step identity, timing, and rolled-up outcome. Conditions and observations are on the paired vector row, not here.
- `record_type = 'vector'` — one row per execution. Every step execution has at least one vector row: a synthesized scope vector for the step itself. Mode-2 (`vectors`-fixture / `run_vector`) loops add one vector row per iteration. The vector row carries the `inputs` / `outputs` / `custom` lane columns and a nested `measurements` list (`LIST<STRUCT>`).

**Measurements are nested, not rows.** Measurements live inside the vector row's `measurements` column as a typed nested list (`LIST<STRUCT>`). Each struct holds `name`, `value`, `unit`, `outcome`, `timestamp`, `limit_*`, `characteristic_id`, `spec_ref`, and signal-path fields (`uut_pin`, `fixture_connection`, `instrument_*`). There is no at-rest `record_type = 'measurement'` row.

**Query projection — four virtual types.** The DuckDB daemon UNNESTs the nested measurements from each vector row into a flat fact and presents a fourth virtual row type `record_type = 'measurement'` in query results. All `WHERE record_type = 'measurement'` queries target this projected view, not the at-rest file. The `in_*` / `out_*` / `custom_*` columns are also projections from the nested lane structs. Query output shape is byte-stable regardless of at-rest format changes.

The canonical schema lives in `src/litmus/data/schemas.py` (`RUN_ROW_SCHEMA`); this page is a human-readable mirror of it.

## File layout

```
<data_dir>/runs/{date}/
├── {timestamp}_{run_id8}_{serial}.parquet  # Run + step + vector rows; measurements nested in vector rows
├── {timestamp}_{run_id8}.parquet           # Same shape, no UUT serial (dev runs)
└── {timestamp}_{run_id8}_{serial}_ref/     # Reference data (waveforms, images, files)
    ├── {vector_id}_scope_waveform.npz
    ├── {vector_id}_camera_image.png
    └── ...
```

Timestamps are UTC and sort naturally. The 8-char `run_id` sits right after the timestamp (the trailing serial is optional, so its absence never shifts the leading parts) and disambiguates two runs of the same serial that start in the same second. DuckDB / Spark / Polars / Pandas all read the file directly with `read_parquet`.

## Discriminator

| Column | Type | Description |
|--------|------|-------------|
| `record_type` | string | At-rest: `'run'`, `'step'`, `'vector'`. Query projection also surfaces `'measurement'`. |

**At-rest row types (three):**
- `run` — one row per run; run-level metadata (start/end timestamps, UUT serial, station, outcome).
- `step` — one row per `(step_path, vector_index)` execution; code identity, timing, rolled-up outcome.
- `vector` — one row per execution, keyed `(step_path, vector_index, retry)`. Every step has at least one vector row: its scope vector. Mode-2 (`vectors`-fixture) loops add one iteration vector per pass. The vector row carries `inputs` / `outputs` / `custom` lane columns and a nested `measurements` list.

**Query projection (virtual fourth type):**
- `measurement` — the daemon UNNESTs each vector's nested `measurements` list into a flat fact row stamped `record_type = 'measurement'`. These rows exist in query results but not in the at-rest file.

To list steps: `WHERE record_type = 'step'`. To list vectors: `WHERE record_type = 'vector'`. To list measurements: `WHERE record_type = 'measurement'`. All kinds: omit the filter.

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

## Input conditions (`inputs` lane — at-rest format)

At rest, each vector's commanded conditions are stored in the `inputs` column as a typed nested list: `LIST<STRUCT<name, kind, value_int, value_double, value_bool, value_text, value_timestamp, value_json, unit>>`. One struct per parameter; `kind` selects which `value_*` lane holds the actual value (`scalar:int`, `scalar:float`, `scalar:bool`, `scalar:str`, `scalar:datetime`, `uri`, `list`, `dict`).

The DuckDB daemon projects these lane structs into flat `in_{param}` columns when populating the query views. The `in_*` wide columns you see in query results are projections, not the at-rest representation.

**Entry structure** (one item in the `inputs` list):

| Field | Type | Description |
|---|---|---|
| `name` | string | Parameter name (e.g. `vin`, `temperature`) |
| `kind` | string | Value type discriminator (`scalar:int`, `scalar:float`, `scalar:bool`, `scalar:str`, `scalar:datetime`, `uri`, `list`, `dict`) |
| `value_int` | int64 | Set when `kind = 'scalar:int'`; NULL otherwise |
| `value_double` | float64 | Set when `kind = 'scalar:float'`; NULL otherwise |
| `value_bool` | bool | Set when `kind = 'scalar:bool'`; NULL otherwise |
| `value_text` | string | Set when `kind` is `scalar:str` or `uri`; NULL otherwise |
| `value_timestamp` | timestamp[us, UTC] | Set when `kind = 'scalar:datetime'`; NULL otherwise |
| `value_json` | string | Set when `kind` is `list` or `dict`; NULL otherwise |
| `unit` | string | Engineering unit set via `context.configure(key, value, unit="V")` or `context.observe(key, value, unit="°C")` |

**Naming convention** (applies to `name` inside each entry and to the projected `in_*` column names):

| Type | Pattern | Examples |
|------|---------|----------|
| Spec conditions | bare name | `temperature`, `load`, `vin` |
| Implementation details | fixture-prefixed | `psu.voltage`, `dmm.sample_count` |

Bare names are spec-relevant for condition matching; prefixed names are stimulus/settings. Convention is enforced by docs, not by the writer.

Stimulus signal-path sub-fields for each param (also stored in the `inputs` lane as separate entries with compound names):

| Entry name pattern | Description |
|---|---|
| `{param}_instrument` | Instrument name |
| `{param}_resource` | VISA address at test time |
| `{param}_channel` | Channel on instrument |
| `{param}_uut_pin` | UUT pin driven |
| `{param}_fixture_connection` | Fixture routing connection |

## Observations (`outputs` lane — at-rest format)

Observations are *measured* context — readings captured during the test, not commanded values. Stored at rest in the `outputs` column with the same `LIST<STRUCT>` shape as `inputs`. The DuckDB daemon projects these into flat `out_{key}` query columns.

Each struct entry encodes one observation under `name`. Non-scalar payloads route to the `_ref/` sibling directory and are stored as `file://` URIs with `kind = 'uri'` in the `value_text` lane:

| Data Type | Storage format | `value_text` example |
|-----------|----------------|----------------------|
| Scalar (float / int / str / bool) | inline in appropriate `value_*` lane | n/a — uses typed lane |
| `Waveform` | `.npz` with t0, dt, Y, attributes | `file://_ref/{id}_scope_waveform.npz` |
| `XYData` | `.npz` with x, y, x_unit, y_unit, x_name, y_name | `file://_ref/{id}_iv_curve.npz` |
| `numpy.ndarray` | `.npy` compressed | `file://_ref/{id}_raw_samples.npy` |
| `Path` | copied, extension preserved | `file://_ref/{id}_debug_log.txt` |
| Pydantic model | `.json` | `file://_ref/{id}_protocol_trace.json` |
| `bytes` | `.bin` | `file://_ref/{id}_raw_data.bin` |

```python
from litmus.data.backends.parquet import load_file, is_file_reference

if is_file_reference(column_value):
    data = load_file(parquet_path, column_value)
```

## Measurement fields (projected from nested struct)

At rest, measurements live in the vector row's `measurements` column as a `LIST<STRUCT>`. The fields below are exposed as flat columns on the projected `record_type = 'measurement'` rows the daemon surfaces.

| Column | Type | Description |
|--------|------|-------------|
| `measurement_name` | string | `"output_voltage"`, `"efficiency"`, ... |
| `measurement_timestamp` | timestamp[us, UTC] | When the measurement was recorded |
| `measurement_value` | float64 | Measured value (scalar; non-scalar payloads go to `_ref/` via `out_*`) |
| `measurement_unit` | string | Units (`V`, `A`, `%`, ...) |
| `measurement_outcome` | string | `passed` / `failed` / `skipped` / `errored` / `aborted` / `terminated` / `done` |

## Limits (on projected `record_type='measurement'` rows)

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

Test code can add arbitrary run-level metadata via `run_context.set()`:

```python
def test_example(run_context, psu, dmm, verify):
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("fixture_serial", "FIX-001")
    run_context.set("ambient_temp", 23.5)
    ...
```

At rest, custom metadata is stored in the `custom` column using the same `LIST<STRUCT>` lane format as `inputs` and `outputs`. The DuckDB daemon projects these into flat `custom_*` query columns with inferred types.

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

All retries are stored. Each retry produces a new step + vector pair at that execution's grain, so measurement-less retries are captured. Measurements stay nested in the paired vector row.

**Mode-1 (parametrize / single / unswept):** each attempt is a separate `step` row + scope `vector` row. `vector_retry` increments per attempt. In the query projection, the UNNESTed measurement rows carry the same `vector_retry` as their enclosing vector.

```
record_type | vector_index | vector_retry | step_outcome   | measurement_name | measurement_value
step        | 0            | 0            | failed         | —                | —                  ← first attempt (at-rest)
vector      | 0            | 0            | failed         | —                | —                  ← scope vector with nested measurements
measurement | 0            | 0            | —              | output_voltage   | 3.50               ← projected (daemon UNNEST)
step        | 0            | 1            | failed         | —                | —                  ← first retry
vector      | 0            | 1            | failed         | —                | —
measurement | 0            | 1            | —              | output_voltage   | 3.48               ← projected
step        | 0            | 2            | passed         | —                | —                  ← second retry
vector      | 0            | 2            | passed         | —                | —
measurement | 0            | 2            | —              | output_voltage   | 3.30               ← projected
```

**Mode-2 (`vectors` fixture):** each attempt at one in-body iteration is a separate `vector` row with the same `vector_index` and an incremented `vector_retry`.

Filter to the final attempt with `WHERE vector_retry = (SELECT MAX(vector_retry) FROM … WHERE record_type IN ('step','vector') …)`, scoping by `(run_id, step_path, vector_index)`.

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

Measurements are nested in vector rows at rest. Use DuckDB to UNNEST them first, then load into pandas:

```python
import duckdb
import pandas as pd

# UNNEST the nested measurements from vector rows and join with run context
con = duckdb.connect()
df = con.execute("""
    SELECT
        v.run_id, v.uut_serial, v.station_hostname,
        v.step_name, v.step_path, v.vector_index, v.vector_retry,
        v.step_outcome, v.vector_outcome, v.run_outcome,
        m.name  AS measurement_name,
        m.value AS measurement_value,
        m.unit  AS measurement_unit,
        m.outcome AS measurement_outcome,
        m.limit_low, m.limit_high, m.limit_nominal,
        m.uut_pin, m.instrument_name
    FROM read_parquet('data/runs/2026-05-16/20260516T143025Z_SN001.parquet') AS v,
         UNNEST(v.measurements) AS t(m)
    WHERE v.record_type = 'vector'
""").df()

# Step rows (direct — no UNNEST needed)
steps = pd.read_parquet(
    "data/runs/2026-05-16/20260516T143025Z_SN001.parquet"
)
steps = steps[steps["record_type"] == "step"]

# Failures
failures = df[df["measurement_outcome"] == "failed"]
print(failures[["step_name", "measurement_name", "measurement_value",
                "limit_low", "limit_high", "uut_pin", "instrument_name"]])
```

When using Litmus's [Query API](query-api.md), the daemon handles the UNNEST automatically — `WHERE record_type = 'measurement'` works as expected in daemon-mediated queries.

### Yield by station with DuckDB (direct file — UNNEST required)

Measurements are nested in vector rows. UNNEST them to get the flat measurement fact:

```sql
SELECT
    v.part_id,
    v.station_hostname,
    m.name AS measurement_name,
    COUNT(*) AS total,
    SUM(CASE WHEN m.outcome = 'passed' THEN 1 ELSE 0 END) AS passed,
    ROUND(100.0 * SUM(CASE WHEN m.outcome = 'passed' THEN 1 ELSE 0 END) / COUNT(*), 2) AS yield_pct
FROM read_parquet('data/runs/**/*.parquet') AS v,
     UNNEST(v.measurements) AS t(m)
WHERE v.record_type = 'vector'
GROUP BY 1, 2, 3
ORDER BY yield_pct ASC;
```

When querying via the Litmus Query API or daemon, the UNNEST runs automatically and `WHERE record_type = 'measurement'` works as-is.

### Cross-run instrument-failure correlation (direct file)

```sql
SELECT
    m.instrument_name,
    m.instrument_resource,
    COUNT(*) AS failures
FROM read_parquet('data/runs/**/*.parquet') AS v,
     UNNEST(v.measurements) AS t(m)
WHERE v.record_type = 'vector'
  AND m.outcome = 'failed'
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
| projected measurement fact (`record_type='measurement'`) | `Data` |
| `UUT` (`uut_*`) | `UUT` |
| `measurement_outcome` | `OutcomeValue` |
| `limit_comparator` | `Comparator` |
| `uut_pin` | `uutPort` |
| `instrument_channel` | `instrumentPort` |

## See also

- [Models](models.md) — Pydantic model index + ERD
- [Event types](event-types.md) — the event-log payloads that source these rows
- [Measurement traceability](../../how-to/execution/traceability.md) — how the signal-path columns get populated
