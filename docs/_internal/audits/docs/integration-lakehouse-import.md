# Page audit: docs/integration/lakehouse-import.md

**Quadrant:** Integration (importing Litmus parquet into a data lakehouse — Spark, Snowflake, BigQuery, etc.)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 1 | 2 |
| Accuracy | 2 | 2 | 1 |
| Gaps | 2 | 4 | 3 |
| Cross-links | 1 | 4 | 5 |
| **Total** | **5** | **13** | **16** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L23-24 | The sentence "This page shows the canonical transform for splitting a Litmus parquet into the logical tables your warehouse expects" arrives AFTER the practical statement at L19-21 ("This file is everything you need…"). The reader hits the engine sections without a clear "here is the pattern we will repeat across engines" framing. Move the canonical-transform sentence up to be the page's lead so the reader knows the shape of what's coming before the first SQL block at L28. |
| SUGGESTION | L26-45 (DuckDB section) | The DuckDB block teaches the three-table pattern (`runs` via DISTINCT, `steps`/`measurements` via `record_type` filter) but the page never names this pattern before showing it. Add a one-line pattern statement immediately before the first code block: "Each recipe does the same three things: derive `runs` via DISTINCT, filter `steps` by `record_type = 'step'`, filter `measurements` by `record_type = 'measurement'`." Then the reader skim-reads each engine block as a variation. |
| SUGGESTION | "Why a single parquet" (L146) | This section is the explanatory "why" — in an integration page it belongs above the recipes (so the reader understands the shape before transforming), OR after the operational notes as a closing rationale. Currently it sits awkwardly between recipes and operational notes. Consider moving it directly under the intro table (after L17) so the design rationale lands before the reader writes their first transform. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L162-167 | Hedging / passive | "the queries above are idempotent if you use `MERGE` / `ON CONFLICT` on `(run_id, …)` keys" — the actor that makes this idempotent is the user's `MERGE` clause; phrasing is fine, but the dependent "Litmus parquets are write-once per run_id; a re-run produces a new run_id, so deduplication by run_id is sufficient" buries the actionable rule. Lead with the rule: "Dedupe on `run_id` — Litmus never reuses a `run_id`, so `MERGE` on `run_id` is sufficient." |
| SUGGESTION | L20 | Marketing-adjacent | "sealed, atomic, write-once, portable" — four virtue-words in a row reads as a sales pitch. Cut to the two that matter for the integration audience: "sealed and atomic — one `mv` to publish." The reader is here to import, not to be sold on the format. |
| SUGGESTION | L47-49 | Throat-clearing | "`EXCLUDE` lists the columns each target table doesn't need. DuckDB's `SELECT * EXCLUDE` is the cleanest way to do this; other engines have equivalents (`SELECT col1, col2, …` or column lists at COPY time)." — "the cleanest way to do this" is a value judgement. Replace with the mechanical fact: "DuckDB supports `SELECT * EXCLUDE`; for engines that don't (Snowflake, BigQuery), list columns explicitly." |
| SUGGESTION | L158-160 | Hedging | "the SQL is short enough to live in any of them" — soft closer that doesn't tell the reader what to do. Either commit ("See [Results storage](../concepts/results-storage.md) for the schema contract these queries depend on") or cut. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L37, L62, L67, L89-90, L106, L111, L127-129 | Hand-waving placeholders in SQL | Repeated use of `/* … */`, `/* ... */`, `/* full schema … */`, and `SELECT …` in code blocks. A test engineer copying these recipes into Snowflake/BigQuery cannot execute them — they have to invent the missing column list. At minimum, point to `reference/parquet-schema.md` as the column source, or expand ONE recipe to be runnable end-to-end as the reference. |
| SUGGESTION | L61-62 | Operator-facing identifier | The Snowflake snippet `SELECT DISTINCT $1:run_id::STRING, $1:dut_serial::STRING, /* ... */` is consistent with the rest of the page — but compare to the DuckDB block at L31-32 which uses `station_id, station_hostname` together. Per project terminology rules, operator-facing/lakehouse-bound columns should prefer `station_hostname` over `station_id`. Decide on a stance and apply uniformly across all six recipes. |
| SUGGESTION | L93 (Databricks) and L114 (Trino/Athena) | Vocabulary | "Databricks / Delta Lake" and "Trino / Athena (Iceberg)" — the section titles cluster two product names per heading, which a test engineer scanning the TOC might not parse. Consider single-target headings ("Delta Lake (Spark / Databricks)", "Iceberg (Trino / Athena)") so the reader looking for "I'm on Athena" sees Athena explicitly. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L4, L34, L38, L43, L62 | doc says parquet filename is `12-00_SN001.parquet` | actual format is `{timestamp}_{serial}.parquet` where `timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")` — e.g. `20260508T120000Z_SN001.parquet`. The doc's `12-00` form looks like a clock time but it's not the format the writer emits. A reader literally pasting the path will not find any file. | `src/litmus/data/backends/parquet.py:205` |
| CRITICAL | L176 | doc says ref URIs look like `_ref/{vector_id}/{key}.npy` | actual ref URI scheme is `file://{date}/{session_id}_ref/{key}.npz` (see `ref.py` docstring example `file://2026-03-08/abc123_ref/waveform.npz`) and the on-disk layout per `parquet-schema.md` L18-22 is `{timestamp}_{serial}_ref/` with files like `{vector_id}_scope_waveform.npz` at the top level (no `{vector_id}/` subdirectory, extension is `.npz` not `.npy`). The documented path will mislead a consumer trying to dereference. | `src/litmus/data/ref.py:7`; `src/litmus/data/event_log.py:185`; `docs/reference/parquet-schema.md:18-22` |
| WARNING | L172-173 | doc says schema_version is "stamped in parquet file-level KV metadata" | TRUE (`metadata[b"schema_version"] = SCHEMA_VERSION.encode()` at `src/litmus/data/backends/parquet.py:130`) but the doc never tells the reader the current value (`"1.0"` per `SCHEMA_VERSION = "1.0"` at `schemas.py:25`). Without the version, "gate behavior" is impossible. | `src/litmus/data/schemas.py:25`; `src/litmus/data/backends/parquet.py:130` |
| WARNING | L168-170 | doc says "Litmus's `RUN_ROW_SCHEMA` evolves additively via column adds" | Correct in spirit, but the actual `RUN_ROW_SCHEMA` (`schemas.py:45-133`) only lists the fixed columns; the page does not mention the dynamic columns (`in_*`, `out_*`, `step_instruments_*`, `custom_*`) that get inferred at write-time via `_build_write_schema`. A lakehouse importer hitting an unexpected `in_voltage_v` column needs to know it's expected, not a corruption. | `src/litmus/data/schemas.py:159-193` |
| SUGGESTION | L1-17 (intro table) | doc says two `record_type` values are `'step'` and `'measurement'` | Verified against `RUN_ROW_SCHEMA` and `parquet-schema.md`. Correct. | `src/litmus/data/schemas.py:45-48`; `docs/reference/parquet-schema.md:3-8` |
| VERIFIED | — | 12 claims verified against source: two-row-kind discriminator (L4-12), denormalized run identity (L13-17), `RUN_ROW_SCHEMA` name (L4), columns used in SQL projections — `run_id`, `session_id`, `run_started_at`, `run_ended_at`, `dut_serial`, `dut_part_number`, `station_id`, `station_hostname`, `run_outcome`, `project_name`, `git_commit` (all present in `schemas.py:45-133`), `litmus export` CLI command exists (L159, confirmed at `cli.py:804`), `union_by_name=true` is a real DuckDB option, `mergeSchema=true` is a real Spark/Delta option. | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L3-4 | The doc gives the parquet path as `results/runs/{date}/{timestamp}_{serial}.parquet` but never tells the reader where `results/` lives. Is it under the project root? Under `$LITMUS_DATA_DIR`? Under platformdirs? Tutorial-style hints exist elsewhere (`litmus.yaml` → `data_dir`), but a lakehouse-import reader landing here directly will not know what bucket / glob to point S3 sync at. State the resolution: "Default is `<project>/data/runs/`; override via `litmus.yaml` `data_dir:` or the `LITMUS_DATA_DIR` env var. Link to the [data dir reference](...)." |
| CRITICAL | L172-177 | The "Reference data" note says large outputs live in `_ref/` directories that "consumers either dereference at query time or copy alongside" — but the page never shows HOW to dereference. What does a Spark / Snowflake job do with a `file://2026-03-08/abc123_ref/waveform.npz` URI in an `out_*` column? The integration audience needs at minimum: "the URI is opaque to the lakehouse; load the `.npz` separately if you need the array." Otherwise the reader will try to UDF-parse it inside SQL. |
| WARNING | "Why a single parquet" / L146 | The page mentions ONE record-type-per-file alternative ("not three") but never names the other obvious alternative the lakehouse audience expects: row-group partitioning (one file per day / per product / per shift). State explicitly that Litmus does NOT partition — every run is its own file — and that the importer is responsible for any partitioning at the warehouse side. |
| WARNING | All recipe sections | No "how do I know it worked" guidance. After running the DuckDB / Snowflake / BigQuery transforms, what row counts should the reader see? A reader's first import of an empty parquet returns 0 rows from the `record_type = 'measurement'` filter and they cannot tell if the parquet was bad or the filter wrong. Add a one-line sanity check: `SELECT record_type, COUNT(*) FROM read_parquet(...) GROUP BY record_type` — expect `step` rows = sum of step counts, `measurement` rows = sum of measurement counts. |
| WARNING | L162-167 (Operational notes) | "Idempotent if you use `MERGE` / `ON CONFLICT` on `(run_id, …)` keys" — but the `runs` table key is `run_id`, while the `steps` table key is `(run_id, step_path, vector_index)` and the `measurements` table key is `(run_id, step_path, vector_index, measurement_name)`. The doc handwaves all three with `(run_id, …)`. State the actual primary key for each derived table. |
| WARNING | All recipes (L60-67, L84-91, L101-112, L127-129) | The recipes elide column lists with `/* … */` and `SELECT …`. A reader who actually opens Snowflake / BigQuery needs to know whether to project all ~60 fixed columns plus dynamic `in_*` / `out_*` / `step_instruments_*` / `custom_*`. State: "Use `SELECT *` to capture all dynamic columns; the warehouse will widen the target table on subsequent loads if it supports schema evolution. Otherwise list the fixed columns from `RUN_ROW_SCHEMA` ([reference](../reference/parquet-schema.md))." |
| SUGGESTION | "Why a single parquet" L146-156 | A test engineer with a multi-station deployment wants to know: do I import per-station or pool? The page treats the parquet glob as global. One line: "Add `station_hostname` / `dut_part_number` as natural partition columns if you split per fleet." |
| SUGGESTION | L23-24 | No mention of the rough size profile — is a typical run parquet 10 KB or 100 MB? A lakehouse architect choosing between direct ingest and a Spark batch needs the order of magnitude. |
| SUGGESTION | Operational notes | No mention of retention. If Litmus runs a retention job that deletes old parquets (per `src/litmus/data/retention.py`), how does the lakehouse importer detect deletions? Recommend immutable downstream tables — once imported, the warehouse copy is the system of record. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | Entire page | No outbound links at all. Zero `[text](path)` references. This is an integration page that should anchor on the canonical schema reference (`reference/parquet-schema.md`), the storage concept (`concepts/results-storage.md`), and the outputs reference (`reference/outputs.md` — which DOES link back here at L69). Every fact about `RUN_ROW_SCHEMA`, `record_type`, dynamic columns, and `_ref/` layout should hand off to those pages instead of restating them. |
| WARNING | L4 | First use of `RUN_ROW_SCHEMA` — no link to `reference/parquet-schema.md` where it is documented in full. |
| WARNING | L173 | First use of `_ref/` directories — no link to `concepts/results-storage.md` or `reference/parquet-schema.md#file-layout` (anchor exists: `## File layout` at parquet-schema.md L12). |
| WARNING | L155 | First use of "DuckDB-internal query path" — no link to `concepts/three-stores.md` or `concepts/results-storage.md#the-query-index` (anchor `## The query index` exists at results-storage.md L75). |
| WARNING | No "See also" section | The page has no closing "See also" or "Next steps" block. Integration pages of this scope (the page mirrors `reference/outputs.md` content) should link to: `reference/parquet-schema.md`, `concepts/results-storage.md`, `reference/outputs.md`, `integration/results-api.md` (for the read-side counterpart). |
| SUGGESTION | L159 | `litmus export` mentioned — could link to `reference/cli.md`. Verified `litmus export` exists at `src/litmus/cli.py:804`. |
| SUGGESTION | L13 | First use of `dut_serial`, `station_*`, `git`, `environment` as denormalized-row columns — could link to `reference/parquet-schema.md` sections (`## Identity & timing`, `## What — DUT`, `## Where — station`, `## Environment traceability` all exist). |
| SUGGESTION | L96-112 (Databricks section) | First use of `pyspark.sql.functions as F` and `format("delta")` — no link to Databricks/Delta docs is required, but a footnote saying "requires the Spark Delta connector" would help. |
| SUGGESTION | L171 | "Schema evolution" — could link to `docs/concepts/results-storage.md#parquet-files-and-schema-evolution` (anchor `## Parquet files and schema evolution` at results-storage.md L33). |
| SUGGESTION | L20 | "S3, GCS, or your local lake" — could link to `reference/outputs.md` which has a "Cloud destinations" section (L62) explicitly stating Litmus does not ship a transport. |
