# Importing Litmus run parquets into a lakehouse

Each Litmus run produces **one sealed parquet** at
`<data_dir>/runs/{date}/{timestamp}_{serial}.parquet`. The parquet's unified
`RUN_ROW_SCHEMA` carries three row kinds, distinguished by an explicit
`record_type` column:

| `record_type` | Cardinality | Carries |
|---|---|---|
| `'run'` | Exactly one per file | Run-level identity, timing, outcome — start/end timestamps, UUT, station, project, git, environment |
| `'step'` | One per `(step_path, vector_index)` | Step identity + outcome + timing + denormalized run / UUT / station context |
| `'vector'` | One per execution | The `inputs` / `outputs` lanes and the nested `measurements` list (`LIST<STRUCT>`) for that execution |

Measurements are **nested** inside each vector row's `measurements` list, not
their own row kind — `UNNEST` them to build a flat measurement table (the
Litmus daemon does the same projection to surface a virtual
`record_type = 'measurement'` at query time). Run-level identity is
denormalized onto step and vector rows, so you can reconstruct a runs-table
either by filtering `record_type = 'run'` or by taking `DISTINCT` run-level
columns from any row kind.

This file is everything you need for a single run — sealed, atomic,
write-once, portable. Drop the directory into S3, GCS, or your local lake;
ingest however your warehouse/lakehouse prefers.

This page shows the canonical transform for splitting a Litmus parquet
into the logical tables your warehouse expects.

## DuckDB (local, native)

```sql
-- One file → three tables. Run identity is denormalized; derive runs from DISTINCT.
INSERT INTO runs
SELECT DISTINCT run_id, session_id, run_started_at, run_ended_at,
       uut_serial, uut_part_number, station_id, station_hostname,
       run_outcome, project_name, git_commit
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_SN001.parquet');

INSERT INTO steps
SELECT * EXCLUDE (record_type, measurements, inputs, outputs)
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_SN001.parquet')
WHERE record_type = 'step';

-- Measurements are nested under each vector row (a LIST<STRUCT>).
-- UNNEST flattens one row per measurement; m.* expands the struct fields.
INSERT INTO measurements
SELECT run_id, session_id, step_path, vector_index, vector_retry, m.*
FROM read_parquet('data/runs/2026-05-08/20260508T120000Z_SN001.parquet'),
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
CREATE OR REPLACE STAGE litmus_runs
  URL='s3://my-bucket/data/runs/'
  FILE_FORMAT = (TYPE = PARQUET);

-- runs is derived via DISTINCT — run identity is denormalized onto every row
COPY INTO runs FROM (
  SELECT DISTINCT $1:run_id::STRING, $1:uut_serial::STRING, /* ... */
  FROM @litmus_runs/2026-05-08/20260508T120000Z_SN001.parquet
  (FILE_FORMAT => 'PARQUET')
);

COPY INTO steps        FROM (… WHERE $1:record_type = 'step');

-- Measurements are nested under each vector row — FLATTEN the array
COPY INTO measurements FROM (
  SELECT $1:run_id::STRING, $1:step_path::STRING,
         m.value:name::STRING, m.value:value::FLOAT, m.value:outcome::STRING, /* … */
  FROM @litmus_runs/2026-05-08/20260508T120000Z_SN001.parquet (FILE_FORMAT => 'PARQUET'),
       LATERAL FLATTEN(input => $1:measurements) m
  WHERE $1:record_type = 'vector'
);
```

For batch ingest of many runs, wrap this in a Snowflake task or external
orchestrator (Airflow, Dagster, dbt) that iterates over new parquet files.

## BigQuery

```sql
-- Create external table over the parquet glob
CREATE OR REPLACE EXTERNAL TABLE litmus.run_rows
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://my-bucket/data/runs/*.parquet']
);

-- Materialize three logical tables: runs via DISTINCT, others via record_type filter
INSERT INTO litmus.runs
SELECT DISTINCT run_id, uut_serial, station_hostname, run_started_at, run_ended_at,
       run_outcome, /* ... */
FROM litmus.run_rows;

INSERT INTO litmus.steps        SELECT … WHERE record_type = 'step';

-- Measurements are nested — UNNEST the repeated measurements field
INSERT INTO litmus.measurements
SELECT run_id, step_path, m.name, m.value, m.outcome, /* … */
FROM litmus.run_rows, UNNEST(measurements) AS m
WHERE record_type = 'vector';
```

## Databricks / Delta Lake

```python
import pyspark.sql.functions as F

df = spark.read.parquet("s3://my-bucket/data/runs/")

# runs is the DISTINCT projection of run-level columns from any row
(df.select("run_id", "uut_serial", "station_hostname",
           "run_started_at", "run_ended_at", "run_outcome").distinct()
   .write.mode("append").format("delta").saveAsTable("litmus.runs"))

(df.where(F.col("record_type") == "step")
   .drop("measurements", "inputs", "outputs")
   .write.mode("append").format("delta").saveAsTable("litmus.steps"))

# Measurements are nested — explode the array, then expand the struct
(df.where(F.col("record_type") == "vector")
   .select("run_id", "step_path", F.explode("measurements").alias("m"))
   .select("run_id", "step_path", "m.*")
   .write.mode("append").format("delta").saveAsTable("litmus.measurements"))
```

## Trino / Athena (Iceberg)

```sql
-- Register the parquet directory as an external Iceberg table
CREATE TABLE litmus.run_rows (
  record_type VARCHAR, run_id VARCHAR, uut_serial VARCHAR, /* full schema … */
)
WITH (
  external_location = 's3://my-bucket/data/runs/',
  format = 'PARQUET'
);

-- runs is the DISTINCT projection; steps / measurements filter by record_type
INSERT INTO litmus.runs         SELECT DISTINCT run_id, uut_serial, … FROM litmus.run_rows;
INSERT INTO litmus.steps        SELECT … FROM litmus.run_rows WHERE record_type = 'step';

-- measurements is an ARRAY(ROW(...)) column on the vector rows — UNNEST it
INSERT INTO litmus.measurements
SELECT run_id, step_path, m.name, m.value, m.outcome  -- …
FROM litmus.run_rows, UNNEST(measurements) AS t(m)
WHERE record_type = 'vector';
```

## Pandas / Polars (one-off analysis)

```python
import duckdb

# Three logical views over the parquet glob. runs is DISTINCT over the
# denormalized run-identity columns; steps filters by record_type;
# measurements UNNESTs the nested list off the vector rows.
runs   = duckdb.sql("SELECT DISTINCT run_id, uut_serial, station_hostname, run_started_at, run_ended_at, run_outcome FROM read_parquet('data/runs/*/*.parquet')").df()
steps  = duckdb.sql("SELECT * EXCLUDE (measurements, inputs, outputs) FROM read_parquet('data/runs/*/*.parquet') WHERE record_type = 'step'").df()
meas   = duckdb.sql("SELECT run_id, step_path, vector_index, vector_retry, m.* FROM read_parquet('data/runs/*/*.parquet'), UNNEST(measurements) AS t(m) WHERE record_type = 'vector'").df()
```

## Why a single parquet (not three)

Litmus stores one parquet per run for several reasons:

1. **One sealed atomic artifact per run** — write-once, portable, easy to
   archive / sync / inspect. Single file → single `mv` for atomic publish.
2. **Run-level identity is denormalized onto every row** — cross-run
   measurement queries don't need joins. This works for our DuckDB-internal
   query path (the hot path).
3. **Lakehouse imports are an explicit, auditable transform** — you see
   exactly what's loaded into each target table; no magic file-layout
   convention to learn.

If you find yourself running the transform repeatedly, write it once as
a dbt model, an Airflow DAG, or a `litmus export` recipe — the SQL is
short enough to live in any of them.

## Operational notes

- **One-time vs incremental**: the queries above are idempotent if you
  use `MERGE` / `ON CONFLICT` on `(run_id, …)` keys. Litmus parquets are
  write-once per run_id; a re-run produces a new run_id, so deduplication
  by run_id is sufficient.
- **Schema evolution**: Litmus's `RUN_ROW_SCHEMA` evolves additively
  via column adds. Older parquets read forward-compatibly via
  `union_by_name=true` (DuckDB) or `mergeSchema=true` (Spark/Delta) /
  `name mapping` (Iceberg). The `schema_version` is stamped in parquet
  file-level KV metadata if you need to gate behavior.
- **Reference data**: large outputs (waveforms, images) live in
  `_ref/` directories alongside each parquet. The parquet's `out_*`
  columns carry URI strings (`file://_ref/{vector_id}_{key}.npy`) referencing
  these files. Consumers either dereference at query time or copy the
  `_ref/` directory alongside.


## See also

**Related quadrants:**

- [Concepts → Data](../../concepts/data/index.md) — concepts entry point for this category
- [How-to → Data](../../how-to/data/index.md) — how-to entry point for this category
- [Reference → Data](../../reference/data/index.md) — reference entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
