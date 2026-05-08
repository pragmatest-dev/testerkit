# Importing Litmus run parquets into a lakehouse

Each Litmus run produces **one sealed parquet** at
`results/runs/{date}/{timestamp}_{serial}.parquet`. The parquet's unified
`RUN_ROW_SCHEMA` carries three row kinds, distinguished by an explicit
`record_type` column:

| `record_type` | Cardinality | Carries |
|---|---|---|
| `'run'` | 1 row per parquet | Run-level identity (run_id, dut_serial, station, fixture, environment); measurement & step columns NULL |
| `'step'` | One per `(step_path, vector_index)` | Step identity + outcome + timing; measurement columns NULL |
| `'measurement'` | One per recorded measurement | Full measurement payload + denormalized step + run context |

This file is everything you need for a single run — sealed, atomic,
write-once, portable. Drop the directory into S3, GCS, or your local lake;
ingest however your warehouse/lakehouse prefers.

This page shows the canonical 3-line transform for splitting a Litmus
parquet into the three logical tables your warehouse expects.

## DuckDB (local, native)

```sql
-- One file → three tables
INSERT INTO runs
SELECT * EXCLUDE (record_type, measurement_name, measurement_value, /* … */)
FROM read_parquet('results/runs/2026-05-08/12-00_SN001.parquet')
WHERE record_type = 'run';

INSERT INTO steps
SELECT * EXCLUDE (record_type, measurement_name, measurement_value, /* … */)
FROM read_parquet('results/runs/2026-05-08/12-00_SN001.parquet')
WHERE record_type = 'step';

INSERT INTO measurements
SELECT * EXCLUDE (record_type)
FROM read_parquet('results/runs/2026-05-08/12-00_SN001.parquet')
WHERE record_type = 'measurement';
```

`EXCLUDE` lists the columns each target table doesn't need. DuckDB's
`SELECT * EXCLUDE` is the cleanest way to do this; other engines have
equivalents (`SELECT col1, col2, …` or column lists at COPY time).

## Snowflake

```sql
-- Stage the run directory (or a glob over many days)
CREATE OR REPLACE STAGE litmus_runs
  URL='s3://my-bucket/results/runs/'
  FILE_FORMAT = (TYPE = PARQUET);

-- Three filtered COPY INTOs per file (or use Snowflake tasks for batch)
COPY INTO runs FROM (
  SELECT $1:run_id::STRING, $1:dut_serial::STRING, /* ... */
  FROM @litmus_runs/2026-05-08/12-00_SN001.parquet
  (FILE_FORMAT => 'PARQUET')
  WHERE $1:record_type = 'run'
);

COPY INTO steps        FROM (… WHERE $1:record_type = 'step');
COPY INTO measurements FROM (… WHERE $1:record_type = 'measurement');
```

For batch ingest of many runs, wrap this in a Snowflake task or external
orchestrator (Airflow, Dagster, dbt) that iterates over new parquet files.

## BigQuery

```sql
-- Create external table over the parquet glob
CREATE OR REPLACE EXTERNAL TABLE litmus.run_rows
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://my-bucket/results/runs/*.parquet']
);

-- Materialize three logical tables via filtered INSERTs
INSERT INTO litmus.runs
SELECT run_id, dut_serial, station_hostname, run_started_at, run_ended_at,
       run_outcome, /* ... */
FROM litmus.run_rows
WHERE record_type = 'run';

INSERT INTO litmus.steps        SELECT … WHERE record_type = 'step';
INSERT INTO litmus.measurements SELECT … WHERE record_type = 'measurement';
```

## Databricks / Delta Lake

```python
import pyspark.sql.functions as F

df = spark.read.parquet("s3://my-bucket/results/runs/")

(df.where(F.col("record_type") == "run")
   .drop("measurement_name", "measurement_value", /* ... */)
   .write.mode("append").format("delta").saveAsTable("litmus.runs"))

(df.where(F.col("record_type") == "step")
   .drop("measurement_name", "measurement_value", /* ... */)
   .write.mode("append").format("delta").saveAsTable("litmus.steps"))

(df.where(F.col("record_type") == "measurement")
   .drop("record_type")
   .write.mode("append").format("delta").saveAsTable("litmus.measurements"))
```

## Trino / Athena (Iceberg)

```sql
-- Register the parquet directory as an external Iceberg table
CREATE TABLE litmus.run_rows (
  record_type VARCHAR, run_id VARCHAR, dut_serial VARCHAR, /* full schema … */
)
WITH (
  external_location = 's3://my-bucket/results/runs/',
  format = 'PARQUET'
);

-- Three filtered INSERTs into the target Iceberg tables
INSERT INTO litmus.runs         SELECT … FROM litmus.run_rows WHERE record_type = 'run';
INSERT INTO litmus.steps        SELECT … FROM litmus.run_rows WHERE record_type = 'step';
INSERT INTO litmus.measurements SELECT … FROM litmus.run_rows WHERE record_type = 'measurement';
```

## Pandas / Polars (one-off analysis)

```python
import duckdb

# Three logical views over the parquet glob
runs   = duckdb.sql("SELECT * FROM read_parquet('results/runs/*/*.parquet') WHERE record_type = 'run'").df()
steps  = duckdb.sql("SELECT * FROM read_parquet('results/runs/*/*.parquet') WHERE record_type = 'step'").df()
meas   = duckdb.sql("SELECT * FROM read_parquet('results/runs/*/*.parquet') WHERE record_type = 'measurement'").df()
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
  columns carry URI strings (`_ref/{vector_id}/{key}.npy`) referencing
  these files. Consumers either dereference at query time or copy the
  `_ref/` directory alongside.
