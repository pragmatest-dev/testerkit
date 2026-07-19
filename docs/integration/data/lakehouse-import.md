# Importing TesterKit run parquets into a lakehouse

Each TesterKit run produces **one sealed parquet** at
`<data_dir>/runs/{date}/{timestamp}_{run_id8}_{serial}.parquet`. The
8-char `run_id8` sits in a fixed position right after the timestamp; the
serial trails and is omitted for dev/debug runs
(`{timestamp}_{run_id8}.parquet`). The parquet's unified `RUN_ROW_SCHEMA`
carries three row kinds, distinguished by an explicit `record_type` column:

Full column list and types: [Reference → Parquet schema](../../reference/data/parquet-schema.md) (`RUN_ROW_SCHEMA`).

| `record_type` | Cardinality | Carries |
|---|---|---|
| `'run'` | Exactly one per file | Run-level identity, timing, outcome — start/end timestamps, UUT, station, project, git, environment |
| `'step'` | One per `(step_path, vector_index)` | Step identity + outcome + timing + denormalized run / UUT / station context |
| `'vector'` | One per execution | The `inputs` / `outputs` lanes and the nested `measurements` list (`LIST<STRUCT>`) for that execution |

Measurements are **nested** inside each vector row's `measurements` list —
there is no `record_type='measurement'` row on disk; `UNNEST` them to build
a flat measurement table. Run-level identity is denormalized onto step and
vector rows, so you can reconstruct a runs-table either by filtering
`record_type = 'run'` or by taking `DISTINCT` run-level columns from any
row kind.

This file is everything you need for a single run — sealed, atomic,
write-once, portable. Drop the directory into S3, GCS, or your local lake;
ingest however your warehouse/lakehouse prefers.

This page shows the canonical transform for splitting a TesterKit parquet
into the logical tables your warehouse expects.

## DuckDB (local, native)

```sql
-- One file → three tables. Run identity is denormalized; derive runs from DISTINCT.
INSERT INTO runs
SELECT DISTINCT run_id, session_id, run_started_at, run_ended_at,
       uut_serial_number, uut_part_number, station_id, station_hostname,
       run_outcome, project_name, git_commit
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_a1b2c3d4_SN001.parquet');

INSERT INTO steps
SELECT * EXCLUDE (record_type, measurements, inputs, outputs)
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_a1b2c3d4_SN001.parquet')
WHERE record_type = 'step';

-- Measurements are nested under each vector row (a LIST<STRUCT>).
-- UNNEST flattens one row per measurement; m.* expands the struct fields.
INSERT INTO measurements
SELECT run_id, session_id, step_path, vector_index, vector_retry, m.*
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_a1b2c3d4_SN001.parquet'),
     UNNEST(measurements) AS t(m)
WHERE record_type = 'vector';
```

`EXCLUDE` lists the columns each target table doesn't need — for steps,
the nested `measurements` / `inputs` / `outputs` lists. DuckDB's
`SELECT * EXCLUDE` is the cleanest way to do this; other engines have
equivalents (`SELECT col1, col2, …` or column lists at COPY time).

## Snowflake

```sql
-- Stage the run directory (or a glob over many days)
CREATE OR REPLACE STAGE testerkit_runs
  URL='s3://my-bucket/data/runs/'
  FILE_FORMAT = (TYPE = PARQUET);

-- runs is derived via DISTINCT — run identity is denormalized onto every row
COPY INTO runs FROM (
  SELECT DISTINCT $1:run_id::STRING, $1:uut_serial_number::STRING, /* ... */
  FROM @testerkit_runs/2026-05-08/20260508T120000Z_a1b2c3d4_SN001.parquet
  (FILE_FORMAT => 'PARQUET')
);

COPY INTO steps        FROM (… WHERE $1:record_type = 'step');

-- Measurements are nested under each vector row — FLATTEN the array
COPY INTO measurements FROM (
  SELECT $1:run_id::STRING, $1:step_path::STRING,
         m.value:name::STRING, m.value:value::FLOAT, m.value:outcome::STRING, /* … */
  FROM @testerkit_runs/2026-05-08/20260508T120000Z_a1b2c3d4_SN001.parquet (FILE_FORMAT => 'PARQUET'),
       LATERAL FLATTEN(input => $1:measurements) m
  WHERE $1:record_type = 'vector'
);
```

For batch ingest of many runs, wrap this in a Snowflake task or external
orchestrator (Airflow, Dagster, dbt) that iterates over new parquet files.

## BigQuery

```sql
-- Create external table over the parquet glob
CREATE OR REPLACE EXTERNAL TABLE testerkit.run_rows
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://my-bucket/data/runs/*.parquet']
);

-- Materialize three logical tables: runs via DISTINCT, others via record_type filter
INSERT INTO testerkit.runs
SELECT DISTINCT run_id, uut_serial_number, station_hostname, run_started_at, run_ended_at,
       run_outcome, /* ... */
FROM testerkit.run_rows;

INSERT INTO testerkit.steps        SELECT … WHERE record_type = 'step';

-- Measurements are nested — UNNEST the repeated measurements field
INSERT INTO testerkit.measurements
SELECT run_id, step_path, m.name, m.value, m.outcome, /* … */
FROM testerkit.run_rows, UNNEST(measurements) AS m
WHERE record_type = 'vector';
```

## Databricks / Delta Lake

```python
import pyspark.sql.functions as F

df = spark.read.parquet("s3://my-bucket/data/runs/")

# runs is the DISTINCT projection of run-level columns from any row
(df.select("run_id", "uut_serial_number", "station_hostname",
           "run_started_at", "run_ended_at", "run_outcome").distinct()
   .write.mode("append").format("delta").saveAsTable("testerkit.runs"))

(df.where(F.col("record_type") == "step")
   .drop("measurements", "inputs", "outputs")
   .write.mode("append").format("delta").saveAsTable("testerkit.steps"))

# Measurements are nested — explode the array, then expand the struct
(df.where(F.col("record_type") == "vector")
   .select("run_id", "step_path", F.explode("measurements").alias("m"))
   .select("run_id", "step_path", "m.*")
   .write.mode("append").format("delta").saveAsTable("testerkit.measurements"))
```

## Trino / Athena (Iceberg)

```sql
-- Register the parquet directory as an external Iceberg table
CREATE TABLE testerkit.run_rows (
  record_type VARCHAR, run_id VARCHAR, uut_serial_number VARCHAR, /* full schema … */
)
WITH (
  external_location = 's3://my-bucket/data/runs/',
  format = 'PARQUET'
);

-- runs is the DISTINCT projection; steps / measurements filter by record_type
INSERT INTO testerkit.runs         SELECT DISTINCT run_id, uut_serial_number, … FROM testerkit.run_rows;
INSERT INTO testerkit.steps        SELECT … FROM testerkit.run_rows WHERE record_type = 'step';

-- measurements is an ARRAY(ROW(...)) column on the vector rows — UNNEST it
INSERT INTO testerkit.measurements
SELECT run_id, step_path, m.name, m.value, m.outcome  -- …
FROM testerkit.run_rows, UNNEST(measurements) AS t(m)
WHERE record_type = 'vector';
```

## Pandas / Polars (one-off analysis)

```python
import duckdb

# Three logical views over the parquet glob. runs is DISTINCT over the
# denormalized run-identity columns; steps filters by record_type;
# measurements UNNESTs the nested list off the vector rows.
runs   = duckdb.sql("SELECT DISTINCT run_id, uut_serial_number, station_hostname, run_started_at, run_ended_at, run_outcome FROM read_parquet('data/runs/*/*.parquet')").df()
steps  = duckdb.sql("SELECT * EXCLUDE (measurements, inputs, outputs) FROM read_parquet('data/runs/*/*.parquet') WHERE record_type = 'step'").df()
meas   = duckdb.sql("SELECT run_id, step_path, vector_index, vector_retry, m.* FROM read_parquet('data/runs/*/*.parquet'), UNNEST(measurements) AS t(m) WHERE record_type = 'vector'").df()
```

## Why a single parquet (not three)

TesterKit stores one parquet per run for several reasons:

1. **One sealed atomic artifact per run** — write-once, portable, easy to
   archive / sync / inspect. Single file → single `mv` for atomic publish.
2. **Run-level identity is denormalized onto every row** — cross-run
   measurement queries don't need joins.
3. **Lakehouse imports are an explicit, auditable transform** — you see
   exactly what's loaded into each target table; no magic file-layout
   convention to learn.

If you find yourself running the transform repeatedly, write it once as
a dbt model, an Airflow DAG, or a `testerkit export` recipe — the SQL is
short enough to live in any of them.

## Operational notes

- **One-time vs incremental**: the queries above are idempotent if you
  use `MERGE` / `ON CONFLICT` on `(run_id, …)` keys. TesterKit parquets are
  write-once per run_id; a re-run produces a new run_id, so deduplication
  by run_id is sufficient.
- **Schema evolution**: TesterKit's `RUN_ROW_SCHEMA` evolves additively
  via column adds. Older parquets read forward-compatibly via
  `union_by_name=true` (DuckDB) or `mergeSchema=true` (Spark/Delta) /
  `name mapping` (Iceberg). The `schema_version` is stamped in parquet
  file-level KV metadata if you need to gate behavior. The directory
  layout (`runs/{date}/…`) and `RUN_ROW_SCHEMA` column names are the
  stable import surface — glob `runs/**/*.parquet` rather than
  hard-coding the `{timestamp}_{run_id8}_{serial}` filename shape, which
  can change across major versions.
- **Array/blob outputs**: there are no `out_*` wide columns. Inputs and
  outputs are nested lane lists on the vector row — `inputs` and `outputs`
  are each `LIST<STRUCT<name, value_type, value_*, unit, uut_pin>>`. A
  blob output's URI lives inside a lane struct's `value_text` field. New
  parquets route all blobs through the FileStore; the URI form is
  `file://{date}/{session_id}/{filename}` where filename is
  `{vector_id_short}_{name}.{ext}`. Pre-2.0 parquets carried
  `file://_ref/…` URIs pointing to a sibling `{stem}_ref/` directory —
  that layout is legacy; treat those URIs as opaque and use `load_ref`
  from `testerkit.data.backends.parquet` to dereference either form. For the
  full lane struct schema, see [Reference → Parquet schema](../../reference/data/parquet-schema.md).


## See also

**Related quadrants:**

- [Concepts → Data](../../concepts/data/index.md) — concepts entry point for this category
- [How-to → Data](../../how-to/data/index.md) — how-to entry point for this category
- [Reference → Data](../../reference/data/index.md) — reference entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
