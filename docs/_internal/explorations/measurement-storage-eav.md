# Measurement storage redesign — nested-struct at-rest + EAV projection

**Status:** spike (`spike/variant-at-rest-eav`), design locked, implementation not started.
**Supersedes:** the JSON / VARIANT framing of ROADMAP #37/#38.
**Benches:** `scripts/bench_measurement_storage.py`, `scripts/bench_at_rest_encoding.py`.

## Problem

Today `in_*` / `out_*` / `custom_*` are wide, per-key, dynamically-typed parquet
columns (`schemas.py:_build_write_schema`). Four defects:

1. **Within-run type collision** → a name that is `float` on one vector and `str`
   on another fails `validate_observation_kinds` (`_row_helpers.py:498`), which
   raises, which `materialize_run_to_parquet` swallows to a `logger.warning`
   (`_runs_duckdb_daemon.py:1602`) → the run is **silently dropped** on a green CI.
2. **Cross-run VARCHAR flip** → `read_parquet(union_by_name)` reconciles a name
   typed differently across runs by promoting the whole column to VARCHAR
   corpus-wide (`1.5` → `'1.5'`), breaking typed/Cpk queries retroactively.
3. **Column explosion** → distinct names × types each became a column. With
   inconsistent naming across a multi-product lab this reaches tens of thousands
   of columns; schema/catalog ops are O(columns) (measured 25× at 4k cols), and
   it churns `ADD COLUMN` on every new name.
4. **int → float64 collapse** → `_infer_type_from_value` maps `int|float` →
   `float64`, so integers lose their type.

## Decision

**At-rest:** replace the wide per-key columns with **one nested
`LIST<STRUCT>` column per side** on the measurement row:

```
outputs: LIST<STRUCT<
  name      VARCHAR,        -- the measurement/observation name
  kind      VARCHAR,        -- observation_kind(): scalar:int|scalar:float|
                            --   scalar:bool|scalar:str|uri|list|dict|other:*
  value_int     BIGINT,     -- exactly one value_* lane populated per entry
  value_double  DOUBLE,
  value_bool    BOOLEAN,
  value_text    VARCHAR,    -- scalar:str AND uri (kind disambiguates)
  value_json    VARCHAR,    -- residual: list / dict / other
  unit          VARCHAR     -- RESERVED, not plumbed yet (on-path slot)
>>
```

(same shape for `inputs` and `custom`). `kind` **reuses `observation_kind()`** —
no parallel taxonomy.

**Projection:** the runs-daemon index builds a LONG/EAV table by `UNNEST`-ing the
nested column — `(file_path/run_id, step_index, side, name, kind, value_int,
value_double, value_bool, value_text, value_json, unit)`, indexed on `name`. The
query API reads the long table; nobody queries the nested at-rest form on DuckDB.

### Why this shape (bench evidence)

`bench_at_rest_encoding.py`, 50k entities × 8 outputs = 400k values:

| encoding | on-disk (zstd) | rebuild→long | int |
|---|---|---|---|
| **LIST<STRUCT>** | **284 KB (1.00×)** | **166 ms native UNNEST** | BIGINT ✅ |
| VARIANT | 300 KB (1.06×) | 409 ms (via JSON→MAP) | UBIGINT ✅ |
| JSON text | 644 KB (2.27×) | 232 ms | ✅ |

- **Smallest on disk** — VARIANT's compactness never materialized (6% larger).
- **Fastest rebuild** — `UNNEST` is native; VARIANT has no key enumeration
  (no `variant_keys`, can't `UNNEST` a variant) so its only route is
  variant→JSON→MAP→unnest, 2.4× slower.
- **Most portable** — `LIST<STRUCT>` is the Dremel-native Parquet model, read by
  every engine; it's the idiomatic nested type in BigQuery (`ARRAY<STRUCT>`) and
  Spark (`ArrayType(StructType)`), and Snowflake reads it (semi-structured
  `FLATTEN`, or structured types). VARIANT-in-Parquet is a 2025 extension with
  patchy reader support (DuckDB's own read path is immature — `bench_measurement_storage.py`
  shows shredded-variant scans ~125× slower than typed, verified through 1.5.3).
- **Lossless incl. int** — `value_int BIGINT` fixes the float64 collapse.

Query speed of the long projection (`bench_measurement_storage.py`): a numeric
op on a type lane runs at clean-typed-column speed (~0.8 ms parquet-scan), vs the
variant path's 50–125 ms. Mixed types never coerce — numbers and strings live in
different lanes, so the corpus-wide VARCHAR flip is structurally impossible.

### What this fixes, in one change

within-run collision (lanes absorb it → no raise → **no silent drop**) ·
cross-run VARCHAR flip (no per-key columns to union) · column explosion (names
are struct-field *values*, not columns — at rest *and* in the projection) ·
int collapse (`value_int`) · projection (trivial `UNNEST`, no `variant_extract`)
· at-rest query / export (nested Parquet is directly `UNNEST`-able and portable;
the export bundle can be these files).

## Current chain (touch points)

### Write path (verified)
`observe()` dispatch (`harness.py:351-409`) routes arrays/waveforms→ChannelStore
(`channel://`), blobs→FileStore (`file://`); scalars/URIs inline. So **outputs
are never raw arrays in the row** — big arrays are refs; only small `list`/`dict`
land inline (`_row_helpers.py:540`, the `value_json` residual case).
→ `MeasurementRecorded` event carries `inputs`/`outputs`/`custom` dicts
(`events.py:477-517`, **no change**)
→ `_event_accumulator._build_row` (`426-476`, passes dicts, **no change**)
→ `MeasurementRow.to_flat_dict` (`_row_helpers.py:211-233`, **prefix-flatten →
   replace with nested-struct encode**)
→ `schemas._build_write_schema` (`159-193`, **per-key columns → one nested col
   per side**); `table_from_rows` mixed-type detection (`196-224`) drops away
→ `validate_observation_kinds` raise (`_row_helpers.py:467-503`) — **kind still
   classifies (routes to lane) but no longer raises on mixed** (this is what
   removes the silent-drop bug)
→ parquet write (`parquet.py:242-248`, schema-driven, **no change**)

### Read path (verified)
`MeasurementsQuery` (`measurements_query.py`) → SQL → Flight
(`_flight_query.py`) → daemon.
- `dynamic_attrs` today is a `MAP(VARCHAR,VARCHAR)` built at ingest from the wide
  parquet columns (`_runs_duckdb_daemon.py:832-835` `MAP([keys],[TRY_CAST vals])`);
  `dynamic_attrs['k'][1]` is the map-struct's value field (`[0]`=key, `[1]`=value),
  stringified → **replace with the long EAV table + lane select**.
- `measurements_materialized` + inflight overlay UNION view (`311-370`, `1301-1329`).
- `_col_expr` (`48-64`) `TRY_CAST(dynamic_attrs['k'][1] AS T)` → **select the lane
  matching the query's type expectation from the long table**.
- `distinct_values` (`825`), `describe_columns` (`666`), `parametric` (`685`),
  `facet_options` (`825-857`), `steps_query.list_for_run` (`171-178`) → repoint
  to the long table.
- returns `list[dict]` → **return Pydantic models** (seals the swap boundary).

## Phased plan

Order chosen so each phase is independently testable. 0.2.0-breaking (wipe data,
no backcompat). Units NOT plumbed (reserved slot only); cross-filter conditions
cube deferred; at-rest direct query only via the export bundle.

- **P1 — At-rest nested encoding (write path).** Encode `inputs`/`outputs`/`custom`
  as `LIST<STRUCT<lanes>>`; `observation_kind` → lane router + `kind` tag; drop the
  per-key schema inference and the mixed-type raise; preserve `int` via
  `value_int`. Touch: `_row_helpers.py`, `schemas.py`, `parquet.py`.
- **P2 — Projection (index).** Daemon UNNESTs the nested column into a long
  `measurements_dynamic` table (+ steps); add table DDL + `name` index; rebuild
  the `measurements` view; build the long rows for the inflight overlay. Touch:
  `_runs_duckdb_daemon.py`, `_accumulator_pool.py`, `_event_accumulator.py`.
- **P3 — Query API.** `_col_expr` + builders select lanes from the long table;
  repoint `distinct_values`/`describe_columns`/`parametric`/`facet_options`/
  `steps_query`; return Pydantic models. Touch: `measurements_query.py`,
  `steps_query.py`, `measurement_facets.py`.
- **P4 — Tests + parity.** Mixed-type lossless (no drop), int preservation,
  no cross-run VARCHAR flip, projection rebuild, query parity, cross-filter.

## Out of scope (reserved / deferred)

- **Units** — `unit` struct slot reserved; not plumbed from config → observe →
  event → row → projection. Separate follow-on.
- **Cross-filter conditions cube** — long-`INTERSECT` is interactive (~13 ms);
  the wide conditions dimension is a transparent later optimization.
- **Direct at-rest query** — live store stays API/index only; the export bundle
  is the at-rest-queryable artifact (now literally these nested files).
