# Results Storage

## Where results go

By default, all litmus results write to a shared directory:

```
~/.local/share/litmus/data/
├── events/      # Event log (Arrow IPC)
├── channels/    # Time-series data (Arrow IPC)
├── runs/        # Test results (Parquet)
└── sessions/    # Session index
```

(See [three-stores](three-stores.md) for what each directory holds: `events/` is the typed [event log](event-log.md), `channels/` is the [ChannelStore](three-stores.md) time-series segments, `runs/` is the materialized Parquet view, `sessions/` is the lightweight session index.) Each Parquet (Apache Parquet — the columnar storage format DuckDB and Spark both read natively) file is one run.

This means every project on the machine shares one results pool. `litmus runs`, `litmus serve`, and DuckDB queries see everything.

**Resolution order** (first match wins):

1. Explicit `--data-dir` argument or `data_dir=` parameter
2. `data_dir` in project `litmus.yaml`
3. `LITMUS_HOME` environment variable
4. `~/.local/share/litmus/data/` (platform default)

To isolate a project's results, add to `litmus.yaml`:

```yaml
name: my-project
data_dir: results    # writes to ./results/ instead of global
```

## Parquet files and schema evolution

Parquet files are the permanent record. Each litmus version may add new columns to the schema (e.g. `project_name`, `test_phase`). Older files simply lack those columns.

When querying across files with different schemas, missing columns appear as NULL:

```sql
-- DuckDB handles mixed schemas automatically
SELECT station_id, project_name, run_outcome
FROM read_parquet('~/.local/share/litmus/data/runs/**/*.parquet',
                  union_by_name=true)
```

Parquet files are never rewritten by version upgrades. The schema at write time is permanent.

### HARD contract — additive evolution only

The parquet artifact is a **HARD contract**: changes must be additive
because written files can't be retroactively rewritten when a new
litmus version ships. Until the 1.0 cut, the following invariants
hold and the project must not break them:

- **New columns only.** Every release may add columns. Existing column
  names, types, and semantics are stable across 0.x releases.
- **No removals or type changes** in 0.x. If a column would otherwise
  be removed or repurposed, it stays in the schema and reads as NULL
  for newly-written rows; the old meaning is documented as deprecated.
- **PK stability.** `(run_id, step_path, vector_index)` is the per-step
  identity in the materialized table; `(run_id, step_path,
  vector_index, measurement_name, vector_retry)` discriminates
  measurement rows. These tuples do not change shape in 0.x.
- **`record_type` discriminator stable.** The `'step'` / `'measurement'`
  values are part of the wire format and do not change.
- **Read with `union_by_name=true`.** Consumer queries that follow the
  recommended `read_parquet(..., union_by_name=true)` pattern survive
  every additive evolution automatically.

Schema rewrites and column removals are deferred to the 1.0 cut, when
a migration story for old files lands. See
[API stability framing](../_internal/explorations/api-stability-and-versioning.md)
for the broader HARD vs SOFT contract picture.

## The query index

Litmus maintains a DuckDB index alongside the parquet files to speed up queries like `litmus runs` and the web UI. This index is a **disposable cache** — it can be deleted and rebuilt at any time without data loss.

When a newer litmus version starts, it checks the index schema version. If the index is older than the running code, litmus deletes it and rebuilds from parquet files automatically. This may cause a brief delay on the first query after a version change.

The index lives at `results/runs/_index.duckdb`. To force a rebuild:

```bash
rm ~/.local/share/litmus/data/runs/_index.duckdb*
```

## Mixed versions on one machine

When multiple projects use different litmus versions but share the global results directory:

| Layer | What happens | User impact |
|-------|-------------|-------------|
| **Parquet files** | Each version writes its own schema. Newer files may have more columns. | NULL values for columns that didn't exist when the file was written |
| **Query index** | The newest version's daemon takes over. Older daemons are stopped automatically. | Brief pause while index rebuilds. Queries from older versions still work — they just see fewer columns. |
| **Web UI / CLI** | Shows whatever the current index has. New columns appear once the newer daemon indexes the files. | Some fields may be empty for older runs |

**The rule:** newer is always a superset. An older litmus version can read results written by a newer version (unknown columns are ignored). A newer version can read older results (missing columns are NULL). No version will corrupt or downgrade another's data.

### When you might notice

- **After upgrading litmus:** first `litmus runs` may be slow (index rebuild). New columns show NULL for old runs.
- **Two projects, different versions:** whichever runs last "wins" the daemon. The other project's next query triggers a brief daemon restart. Both projects' data is always safe.
- **Downgrading litmus:** the older version uses the existing index as-is (no rebuild). Columns it doesn't know about are simply not queried.
