# Results Storage

## Where results go

By default, all litmus results write to a shared directory:

```
~/.local/share/litmus/results/
├── events/      # Event log (Arrow IPC)
├── channels/    # Time-series data (Arrow IPC)
├── runs/        # Test results (Parquet)
└── sessions/    # Session index
```

This means every project on the machine shares one results pool. `litmus runs`, `litmus serve`, and DuckDB queries see everything.

**Resolution order** (first match wins):

1. Explicit `--results-dir` argument or `results_dir=` parameter
2. `results_dir` in project `litmus.yaml`
3. `LITMUS_RESULTS_DIR` environment variable
4. `~/.local/share/litmus/results/` (platform default)

To isolate a project's results, add to `litmus.yaml`:

```yaml
name: my-project
results_dir: results    # writes to ./results/ instead of global
```

## Parquet files and schema evolution

Parquet files are the permanent record. Each litmus version may add new columns to the schema (e.g. `project_name`, `test_phase`). Older files simply lack those columns.

When querying across files with different schemas, missing columns appear as NULL:

```sql
-- DuckDB handles mixed schemas automatically
SELECT station_id, project_name, outcome
FROM read_parquet('~/.local/share/litmus/results/runs/**/*.parquet',
                  union_by_name=true)
```

Parquet files are never rewritten by version upgrades. The schema at write time is permanent.

## The query index

Litmus maintains a DuckDB index alongside the parquet files to speed up queries like `litmus runs` and the web UI. This index is a **disposable cache** — it can be deleted and rebuilt at any time without data loss.

When a newer litmus version starts, it checks the index schema version. If the index is older than the running code, litmus deletes it and rebuilds from parquet files automatically. This may cause a brief delay on the first query after a version change.

The index lives at `results/runs/_index.duckdb`. To force a rebuild:

```bash
rm ~/.local/share/litmus/results/runs/_index.duckdb*
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
