# Three Stores Architecture

Litmus uses three complementary data stores, each optimized for a different access pattern. Together they provide a complete picture of test activity.

## Overview

| Store | Data | Format | Query Layer |
|-------|------|--------|-------------|
| **EventStore** | Typed events (sessions, measurements, diagnostics) | Arrow IPC files | DuckDB via Flight |
| **ChannelStore** | Time-series instrument data (waveforms, readings) | Arrow IPC segments | Flight server |
| **ParquetBackend** | Denormalized test results (one row per measurement) | Parquet files | DuckDB |

## EventStore — Source of Truth

The event log captures every significant action as a typed event. It is the canonical record of what happened.

- **Write path:** `EventLog.emit()` → buffered Arrow IPC → Flight `do_put` to DuckDB (see [flight-streaming](flight-streaming.md) for `do_put`/`do_get` / DuckDB daemon details; Arrow IPC is Apache Arrow's on-disk record-batch format)
- **Read path:** SQL via Flight `do_get`, or direct IPC file reads
- **Storage:** `<data_dir>/events/{date}/{session_id}.arrow`

Events are normalized — `SessionStarted` carries session/station/operator metadata once (and must NOT carry `run_id`); `RunStarted` carries the run/DUT context per test run; `MeasurementRecorded` carries only measurement fields. Subscribers denormalize at write time.

## ChannelStore — Time-Series Data

Instrument reads that produce arrays, waveforms, or high-frequency scalar streams go to the ChannelStore. The EventStore gets a compact claim-check URI (`channel://scope.ch1/...`) instead of the raw data.

- **Write path:** `ChannelStore.write()` → buffered Arrow IPC with segment rotation
- **Read path:** `ChannelStore.query()` with LTTB decimation for visualization
- **Storage:** `<data_dir>/channels/{date}/{channel_id}_{session_short}.arrow`

Each channel gets its own IPC file with a schema inferred from the first write. Segment rotation ensures files are always readable (closed after each flush).

## ParquetBackend — Materialized View

The Parquet backend produces analysis-ready files with one row per measurement, all metadata denormalized. This is what users query with DuckDB, Polars, or Spark for yield analysis and SPC.

- **Write path:** The runs daemon accumulates events via `AccumulatorPool` and calls `materialize_run_to_parquet()` on `RunEnded`
- **Read path:** Standard Parquet readers (DuckDB, Polars, pandas)
- **Storage:** `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet`

The Parquet files are a **materialized view** of the event stream. They can be regenerated from events if the schema changes.

### Live streaming + crash safety

During execution, the materializer holds row state in-process and flushes to a single per-run parquet at run end. The operator UI subscribes to the in-process event stream for live updates — there is no separate JSONL journal on disk. If the process is killed before the parquet is finalized, the close-time fallback writes whatever rows reached the materializer with `run_outcome = aborted`.

## How They Relate

```
Events (source of truth)
  ├── EventStore (Arrow IPC + DuckDB)
  │     └── runs daemon (AccumulatorPool + materialize_run_to_parquet on RunEnded)
  └── ChannelStore (time-series, claim-check URIs in events)
```

1. All activity flows through `EventStore.emit()`
2. The runs daemon accumulates events via `AccumulatorPool` and materializes a per-run parquet on `RunEnded` via `materialize_run_to_parquet()`
3. `InstrumentRead` events with array data write to `ChannelStore`, storing a URI in the event
4. Queries can join across stores using `session_id`

## Storage Layout

```
<data_dir>/
├── events/                    # EventStore
│   ├── 2026-03-10/
│   │   ├── {session_id}.arrow
│   │   └── {session_id}_ref/  # Large reference data
│   └── 2026-03-11/
├── channels/                  # ChannelStore
│   ├── 2026-03-10/
│   │   ├── dmm.voltage_{session_short}.arrow
│   │   └── scope.ch1_{session_short}.arrow
│   └── _registry.json         # Channel metadata
└── runs/                      # ParquetBackend
    └── 2026-03-10/
        ├── 20260310T143022Z_SN001.parquet
        └── 20260310T143022Z_SN001_ref/
```

Sessions are not a stored entity — they're derived from events at query time.

## Where the data dir lives

`<data_dir>` defaults to a shared per-user directory so every project on the machine sees the same results pool — `litmus runs`, `litmus serve`, and DuckDB queries see everything.

Resolution order (first match wins):

1. Explicit `--data-dir` argument or `data_dir=` parameter
2. `data_dir` field in the project's `litmus.yaml`
3. `LITMUS_HOME` environment variable
4. `~/.local/share/litmus/data/` (platform default via `platformdirs`)

To isolate a project's results from the shared pool, add to `litmus.yaml`:

```yaml
name: my-project
data_dir: results    # writes to ./results/ instead of the global pool
```

## Schema evolution — HARD contract

Parquet files are the permanent record. Each litmus version may add columns; older files simply lack them. The parquet artifact is a **HARD contract**: changes must be additive because written files cannot be retroactively rewritten when a new version ships.

Until the 1.0 cut, the following invariants hold:

- **New columns only.** Every release may add columns. Existing column names, types, and semantics are stable across 0.x releases.
- **No removals or type changes** in 0.x. If a column would otherwise be removed or repurposed, it stays in the schema and reads as NULL for newly-written rows; the old meaning is documented as deprecated.
- **PK stability.** `(run_id, step_path, vector_index)` is the per-step identity in the materialized table; `(run_id, step_path, vector_index, measurement_name, vector_retry)` discriminates measurement rows. These tuples do not change shape in 0.x.
- **`record_type` discriminator stable.** The `'run'` / `'step'` / `'measurement'` values are part of the wire format and do not change.
- **Read with `union_by_name=true`.** Consumer queries that follow the recommended `read_parquet(..., union_by_name=true)` pattern survive every additive evolution automatically.

```sql
-- DuckDB handles mixed schemas automatically
SELECT station_id, project_name, run_outcome
FROM read_parquet('~/.local/share/litmus/data/runs/**/*.parquet',
                  union_by_name=true)
```

Schema rewrites and column removals are deferred to the 1.0 cut, when a migration story for old files lands.

## The DuckDB query index

Litmus maintains a DuckDB index alongside the parquet files to speed up queries like `litmus runs` and the web UI. The index is a **disposable cache** — it can be deleted and rebuilt at any time without data loss. The index file lives at `<data_dir>/runs/_index.duckdb`.

If a schema column the index doesn't yet know about appears in a parquet file, the index runs `ALTER TABLE … ADD COLUMN IF NOT EXISTS` to absorb it. There is no version-gated drop-and-rebuild.

To force a full rebuild:

```bash
rm ~/.local/share/litmus/data/runs/_index.duckdb*
```

## Mixed versions on one machine

When multiple projects use different litmus versions but share the global results directory:

| Layer | What happens | User impact |
|---|---|---|
| Parquet files | Each version writes its own schema. Newer files may have more columns. | NULL values for columns that didn't exist when the file was written. |
| Query index | Schema is additive (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`) per data_dir. The runs daemon is one process per data_dir, not per version. | New columns appear in the index once a newer-version process writes them. |
| Web UI / CLI | Shows whatever the current index has. | Some fields may be empty for older runs. |

The rule: newer is always a superset. An older litmus version reading newer results ignores unknown columns; a newer version reading older results sees NULL for missing columns. No version corrupts or downgrades another's data.

## See Also

- [Event Log Architecture](event-log.md) — Deep dive into the event system
- [Sessions](sessions.md) — How sessions group activity
- [Flight Streaming](flight-streaming.md) — Cross-process query model
