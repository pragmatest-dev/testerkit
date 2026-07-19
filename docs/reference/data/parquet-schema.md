# Parquet Storage Schema

Each TesterKit run produces **one Parquet file**. The file has two layers: the at-rest format and the query projection.

**At-rest — three row types.** Every row carries an explicit `record_type` discriminator with one of three values:

- `record_type = 'run'` — exactly one row per file. Carries run-level identity, timing, outcome, plus UUT / station / project / git / environment context.
- `record_type = 'step'` — one row per step execution (per `step_path`, per retry). Carries the step's identity, timing, and rolled-up outcome — and, when the step measures directly, its own `inputs` / `outputs` / `measurements`. `vector_index` is always NULL on a step row; if the step is nested inside a parent loop, the iteration it ran under is carried by `vector_outer_index`.
- `record_type = 'vector'` — a **condition point**: one row per iteration of a sweep or in-body loop (the `vectors` fixture / `run_vector`). Present **only** when a step loops — a step that measures directly emits no vector rows. Each vector row carries that point's own `inputs` / `outputs` / `measurements`, with `vector_index` its 0-based position in the loop.

**Measurements are nested, not rows.** Measurements live in a `measurements` column — a typed nested list (`LIST<STRUCT>`) — on whichever row owns them: the **step row** for a directly-measured step, or the **vector row** for a swept one. Each struct holds `name`, `value`, `unit`, `outcome`, `timestamp`, `limit_*`, `characteristic_id`, `spec_ref`, and signal-path fields (`uut_pin`, `fixture_connection`, `instrument_*`). There is no at-rest `record_type = 'measurement'` row.

**Query projection — four virtual types.** The query projection UNNESTs the nested measurements from **both step and vector rows** into a flat fact and presents a fourth virtual row type `record_type = 'measurement'` in query results. All `WHERE record_type = 'measurement'` queries target this projected view, not the at-rest file. The `inputs` / `outputs` lanes (from either row type) are also projected into two separate EAV tables, `inputs` and `outputs` (the table name IS the role — no `role` column), for query-time access. Query output shape is byte-stable regardless of at-rest format changes.

**Run identity lives once.** The query projection's derived tables each carry only their own grain's columns plus a `run_id` foreign key — run identity (UUT / station / part / project / git / environment context) lives once, in the `runs` table, and every other query view (`steps`, `measurements`, `instruments`) joins it back in. Reads always see the same full column set as before; only the storage underneath is normalized.

This page mirrors the canonical at-rest schema; the column names and types here match what `read_parquet` returns.

## File layout

```
<data_dir>/runs/{date}/
├── {timestamp}_{run_id8}_{serial}.parquet  # Run + step + vector rows; measurements nested in the step or vector row that owns them
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
- `step` — one row per step execution, keyed `(step_path, step_retry, vector_outer_index)`; code identity, timing, rolled-up outcome, and the step's own `inputs` / `outputs` / `measurements` when it measures directly.
- `vector` — a condition point: one row per sweep / in-body-loop iteration, keyed `(step_path, vector_outer_index, vector_index, vector_retry)` (it also carries the enclosing `step_retry`). `vector_outer_index` is NULL for a top-level loop and set only when the loop is nested inside a parent loop. Present only when a step loops. Carries that point's `inputs` / `outputs` lane columns and a nested `measurements` list.

**Query projection (virtual fourth type):**
- `measurement` — the query projection UNNESTs the nested `measurements` list from **both step and vector rows** into flat fact rows stamped `record_type = 'measurement'`. These rows exist in query results but not in the at-rest file.

To list steps: `WHERE record_type = 'step'`. To list vectors: `WHERE record_type = 'vector'`. To list measurements: `WHERE record_type = 'measurement'`. All kinds: omit the filter.

## Identity & timing

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | string | Session UUID — groups runs that ran together in one `testerkit serve` / `pytest` invocation |
| `run_id` | string | Run UUID — primary key for the run |
| `site_index` | int64 | Multi-UUT site index, 0-based; always present, default `0` (a single-UUT run stores `0`, never NULL) |
| `site_name` | string | Optional human label for the site (NULL when unnamed or single-UUT) |
| `run_started_at` | timestamp[us, UTC] | When the run started |
| `run_ended_at` | timestamp[us, UTC] | When the run ended |
| `step_name` | string | Test function or class name |
| `step_index` | int64 | 0-based step order within the run |
| `step_path` | string | Hierarchical path, e.g. `TestPower/test_efficiency` |
| `step_started_at` | timestamp[us, UTC] | Step start (NULL for unrun planned steps) |
| `step_ended_at` | timestamp[us, UTC] | Step end |
| `step_node_id` | string | pytest node id (`tests/test_power.py::TestPower::test_efficiency`) |
| `step_module` | string | Module name |
| `step_file` | string | Source file path |
| `step_class` | string | Class name (NULL for module-level functions) |
| `step_function` | string | Function name |
| `step_markers` | string | Marker payload summary |
| `step_retry` | int64 | 0-based step retry counter (0 = first execution); a step retry makes a new step row |
| `vector_index` | int64 | Position in the loop; **NULL on run and step rows**, 0..N on vector rows |
| `vector_outer_index` | int64 | The enclosing parent-loop iteration a nested step/vector ran under; NULL when not nested |
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
| `uut_serial_number` | string | From `--uut-serial` |
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

## Where — instruments (`instruments` lane — at-rest format)

Instrument identity and calibration traceability for the run, captured from the pytest fixtures the test used. Stored at rest in the `instruments` column as a typed nested list — `LIST<STRUCT>` — one struct per instrument. Every row carries the column; the run row holds the full inventory.

**Entry structure** (one item in the `instruments` list):

| Field | Type | Description |
|---|---|---|
| `name` | string | Role name (e.g. `dmm`, `psu`) |
| `id` | string | Instrument file ID |
| `driver` | string | Driver class path |
| `resource` | string | VISA address |
| `protocol` | string | Protocol (`"visa"`, `"daqmx"`, …) |
| `manufacturer` | string | From `*IDN?` or YAML config |
| `model` | string | Model number |
| `serial_number` | string | Serial number |
| `firmware` | string | Firmware version |
| `cal_due` | string | Calibration due date (ISO 8601) |
| `cal_last` | string | Last cal date (ISO 8601) |
| `cal_certificate` | string | Cal certificate number |
| `cal_lab` | string | Cal lab name |
| `mocked` | bool | True if the instrument ran in mock mode |

For real hardware, identity comes from `*IDN?` at session start. For mock instruments, identity comes from the instrument YAML configs.

The Query API materializes these structs into the `instruments` table (one row per instrument per run) at ingest, so per-instrument queries select named columns instead of unnesting the nested list:

```sql
-- DuckDB: one row per instrument per run, via the materialized table
SELECT name, serial_number, cal_due, count(DISTINCT run_id) AS runs
FROM instruments
GROUP BY name, serial_number, cal_due;
```

To read the nested list directly off the parquet (e.g. an external lakehouse ingest):

```sql
-- DuckDB: unnest the instruments struct off the run row
SELECT
    i.name AS instrument,
    i.serial_number AS serial,
    i.cal_due AS cal_due
FROM read_parquet('data/runs/**/*.parquet'), UNNEST(instruments) AS t(i)
WHERE record_type = 'run';
```

## Test context

| Column | Type | Description |
|--------|------|-------------|
| `test_phase` | string | `production` / `characterization` / `development` |
| `project_name` | string | Project name from `testerkit.yaml` |
| `git_commit` | string | Code version at test time |
| `git_branch` | string | Branch at test time |
| `git_remote` | string | Remote URL at test time |

## Input conditions (`inputs` lane — at-rest format)

At rest, a step's commanded conditions — or, for a swept iteration, a vector's — are stored in that row's `inputs` column as a typed nested list: `LIST<STRUCT<name, value_type, value_int, value_double, value_bool, value_text, value_timestamp, value_json, unit, uut_pin>>`. One struct per parameter; `value_type` selects which `value_*` field holds the actual value.

The Query API projects these lane structs into the `inputs` EAV table (keyed by `name` — the table IS the role) for query-time access. See [Query API](query-api.md) for how to select input fields in analysis.

**Entry structure** (one item in the `inputs` list):

| Field | Type | Description |
|---|---|---|
| `name` | string | Parameter name (e.g. `vin`, `temperature`) |
| `value_type` | string | Value type tag: `scalar:int`, `scalar:float`, `scalar:bool`, `scalar:str`, `scalar:datetime`, `uri`, `list`, `dict`, or `other:<type>` for any other Python type (e.g. `other:Waveform`) |
| `value_int` | int64 | Set when `value_type = 'scalar:int'`; NULL otherwise |
| `value_double` | float64 | Set when `value_type = 'scalar:float'`; NULL otherwise |
| `value_bool` | bool | Set when `value_type = 'scalar:bool'`; NULL otherwise |
| `value_text` | string | Set when `value_type` is `scalar:str`, `uri`, or `other:<type>` (the value's `repr`); NULL otherwise |
| `value_timestamp` | timestamp[us, UTC] | Set when `value_type = 'scalar:datetime'`; NULL otherwise |
| `value_json` | string | Set when `value_type` is `list` or `dict`; NULL otherwise |
| `unit` | string | Engineering unit set via `context.configure(key, value, unit="V")` |
| `uut_pin` | string | UUT pin driven by this input (NULL if not applicable) |

**Naming convention** (applies to `name` inside each entry):

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

Observations are measured context — readings captured during the test, not commanded values. Stored at rest in the `outputs` column with the same `LIST<STRUCT>` shape as `inputs`. The Query API projects these into the `outputs` EAV table.

Each struct entry encodes one observation under `name`. Non-scalar payloads route to the `_ref/` sibling directory and are stored as `file://` URIs with `value_type = 'uri'` in the `value_text` field:

| Data Type | Storage format | `value_text` example |
|-----------|----------------|----------------------|
| Scalar (float / int / str / bool) | inline in appropriate `value_*` field | n/a — uses typed field |
| `Waveform` | `.npz` with t0, dt, Y, attributes | `file://_ref/{id}_scope_waveform.npz` |
| `XYData` | `.npz` with x, y, x_unit, y_unit, x_name, y_name | `file://_ref/{id}_iv_curve.npz` |
| `numpy.ndarray` | `.npy` compressed | `file://_ref/{id}_raw_samples.npy` |
| `Path` | copied, extension preserved | `file://_ref/{id}_debug_log.txt` |
| Pydantic model | `.json` | `file://_ref/{id}_protocol_trace.json` |
| `bytes` | `.bin` | `file://_ref/{id}_raw_data.bin` |

```python
from testerkit.data.backends.parquet import load_file, is_file_reference

if is_file_reference(column_value):
    data = load_file(parquet_path, column_value)
```

## Measurement fields (projected from nested struct)

At rest, measurements live in the owning row's `measurements` column — the step row for a direct measurement, the vector row for a swept one — as a `LIST<STRUCT>`. The fields below are exposed as flat columns on the projected `record_type = 'measurement'` rows the Query API surfaces.

| Column | Type | Description |
|--------|------|-------------|
| `measurement_name` | string | `"output_voltage"`, `"efficiency"`, ... |
| `measurement_timestamp` | timestamp[us, UTC] | When the measurement was recorded |
| `measurement_value` | float64 | Measured value (scalar; non-scalar payloads go to `_ref/` via the `outputs` lane) |
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
| `testerkit_version` | string | Installed TesterKit version |
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

At rest, custom metadata is stored as a JSON blob in the Parquet **file-level metadata** under the key `custom_metadata` — not as a column in the row data. It is run-scoped (one blob per file, not one entry per measurement).

## `inputs` / `outputs` EAV tables (query projection)

The Query API projects all `inputs` and `outputs` lane entries into two long EAV tables, named `inputs` and `outputs` — the table name IS the role, so there is no `role` column. This is what the [Query API](query-api.md) reads when you select inputs or outputs by name.

| Column | Type | Description |
|--------|------|-------------|
| `name` | string | Entry name as passed to `configure()` or `observe()` |
| `value_type` | string | Value type tag (e.g. `scalar:float`, `scalar:int`, `scalar:bool`, `scalar:str`, `scalar:datetime`, `uri`, `list`, `dict`) |
| `value_int` | int64 | Populated when `value_type = 'scalar:int'` |
| `value_double` | float64 | Populated when `value_type = 'scalar:float'` |
| `value_bool` | bool | Populated when `value_type = 'scalar:bool'` |
| `value_text` | string | Populated when `value_type` is `scalar:str`, `uri`, or `other:<type>` |
| `value_timestamp` | timestamp[us, UTC] | Populated when `value_type = 'scalar:datetime'` |
| `value_json` | string | Populated when `value_type` is `list` or `dict` |
| `unit` | string | Engineering unit |
| `uut_pin` | string | UUT pin (for input-side stimulus entries) |
| `run_id` | string | Links back to the run |
| `step_index` | int32 | Step position within the run |
| `step_path` | string | Hierarchical step id (part of the join key) |
| `step_retry` | int64 | Enclosing-step retry (part of the join key) |
| `vector_index` | int64 | 0-based index within the step's sweep (NULL for a step-scope entry) |
| `vector_retry` | int64 | Vector retry (NULL for a step-scope entry) |
| `ordinal` | int64 | 0-based position within the carrier's entry list — discriminates a name that repeats on one carrier |
| `index` | int64 | Run-wide, per-name, retry-stable occurrence ordinal (the `/explore` X axis) |

Querying these tables directly is rarely needed — use the [Query API](query-api.md) (`FieldRef.input("vin")`, `FieldRef.output("v_rail")`) which joins the right one for you and handles type coherence (fails loud if a name carries mixed `value_type`s in scope; auto-resolves when unambiguous).

### Projection tables are a normalized snowflake

The query projection is a **snowflake**: each derived table (`runs`, `steps`, `vectors`, `measurements`, `inputs`, `outputs`, `instruments`) stores only its own grain's columns plus the foreign key to its parent. Run identity lives once in `runs`; step code/timing lives once in `steps`; the swept condition points live in `vectors`; the flat measurement fact and the `inputs`/`outputs` lanes carry only their payload plus the coordinate key that reaches their step/vector. The `measurements` / `steps` / `step_vectors` / `instruments` **views** JOIN back up the hierarchy to reconstruct the wide, denormalized row shape — so every query and its results are unchanged; only the storage underneath is normalized (no denormalized copy that can drift). This is a derived cache: it rebuilds from the parquet on boot and carries no `schema_version` of its own.

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

All retries are stored. Each retry produces a new row at that execution's grain — a fresh `step` row for a directly-measured step, or a fresh `vector` row for a swept iteration — so measurement-less retries are captured too. Measurements stay nested on the retried row.

**Mode-1 (parametrize / single / unswept):** each attempt is a separate `step` row carrying its own nested measurements; `step_retry` increments per attempt. There are no vector rows. In the query projection, the UNNESTed measurement rows carry the same `step_retry` as their enclosing step.

```
record_type | step_retry | step_outcome | measurement_name | measurement_value
step        | 0          | failed       | —                | —                  ← first attempt (measurements nested on the step row)
measurement | 0          | —            | output_voltage   | 3.50               ← projected (query-time UNNEST)
step        | 1          | failed       | —                | —                  ← first retry
measurement | 1          | —            | output_voltage   | 3.48               ← projected
step        | 2          | passed       | —                | —                  ← second retry
measurement | 2          | —            | output_voltage   | 3.30               ← projected
```

**Mode-2 (`vectors` fixture):** each attempt at one in-body iteration is a separate `vector` row with the same `vector_index` and an incremented `vector_retry`.

Filter to the final attempt with a window over the retry axis — for step-grained rows, the max `step_retry` per `(run_id, step_path)`; for swept rows, the max `vector_retry` per `(run_id, step_path, vector_index)`.

## File-level metadata

Beyond columns, each Parquet file carries metadata:

| Key | Description |
|-----|-------------|
| `environment_json` | Full environment snapshot (Python version, OS, TesterKit version, top-level deps, lockfile hash) |
| `custom_metadata` | Run-level custom metadata set via `run_context.set()`, serialized as a JSON object |
| `schema_version` | At-rest schema version (`"0.1"`) |

```python
import pyarrow.parquet as pq
from testerkit.environment import EnvironmentSnapshot

pf = pq.ParquetFile("data/runs/2026-05-16/20260516T143025Z_SN001.parquet")
metadata = pf.schema_arrow.metadata
env = EnvironmentSnapshot.model_validate_json(metadata[b"environment_json"])
print(f"Python {env.python_version}, TesterKit {env.testerkit_version}")
```

## Export column naming

When exporting runs to CSV or HDF5, input and output lane entries use `input_` / `output_` prefixes on the field name:

| At-rest lane | Export column / attribute |
|---|---|
| `inputs` entry named `vin` | `input_vin` |
| `outputs` entry named `v_rail` | `output_v_rail` |

Run-level `custom_metadata` keys are also exported, each as a `custom_<key>` column (CSV).

## Querying examples

### Load a run with pandas

Measurements are nested in step (and vector) rows at rest. Use DuckDB to UNNEST them first, then load into pandas:

```python
import duckdb
import pandas as pd

# Measurements are nested on step rows (direct) and vector rows (swept).
# UNNEST from both, joined with each row's run/step context.
con = duckdb.connect()
df = con.execute("""
    SELECT
        v.run_id, v.uut_serial_number, v.station_hostname,
        v.step_name, v.step_path, v.vector_index,
        v.step_retry, v.vector_retry,
        v.step_outcome, v.vector_outcome, v.run_outcome,
        m.name  AS measurement_name,
        m.value AS measurement_value,
        m.unit  AS measurement_unit,
        m.outcome AS measurement_outcome,
        m.limit_low, m.limit_high, m.limit_nominal,
        m.uut_pin, m.instrument_name
    FROM read_parquet('data/runs/2026-05-16/20260516T143025Z_SN001.parquet') AS v,
         UNNEST(v.measurements) AS t(m)
    WHERE v.record_type IN ('step', 'vector')
""").df()

# Failures
failures = df[df["measurement_outcome"] == "failed"]
print(failures[["step_name", "measurement_name", "measurement_value",
                "limit_low", "limit_high", "uut_pin", "instrument_name"]])
```

When using TesterKit's [Query API](query-api.md), the UNNEST is handled automatically — `WHERE record_type = 'measurement'` works as expected.

### Yield by station with DuckDB (direct file — UNNEST required)

Measurements are nested in step (and vector) rows. UNNEST them to get the flat measurement fact:

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
WHERE v.record_type IN ('step', 'vector')
GROUP BY 1, 2, 3
ORDER BY yield_pct ASC;
```

When querying via the TesterKit Query API, the UNNEST runs automatically and `WHERE record_type = 'measurement'` works as-is.

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

| TesterKit column | ATML equivalent |
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
- [Query API](query-api.md) — how to select inputs and outputs by role and name in analysis
- [Measurement traceability](../../how-to/execution/traceability.md) — how the signal-path columns get populated
