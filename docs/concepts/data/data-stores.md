# The Litmus data stores

Litmus uses four complementary data stores, each optimized for a different access pattern. Together they provide a complete picture of test activity.

## Overview

| Store | Data | Format |
|-------|------|--------|
| **EventStore** | Typed events (sessions, measurements, diagnostics) — the source of truth | Arrow files (DuckDB-queryable) |
| **ChannelStore** | Time-series instrument data (waveforms, readings) | Arrow segments |
| **FileStore** | Captured artifacts — images, video, vendor capture files (`file://`) | files + index |
| **RunStore** | Flat test results, one row per measurement | Parquet files |

## EventStore — Source of Truth

The event log captures every significant action as a typed event. It is the canonical record of what happened.

- **Write path:** events are buffered and streamed into the DuckDB-backed event log (see [flight-streaming](flight-streaming.md) for the cross-process detail).
- **Read path:** SQL queries, or direct reads of the Arrow files.
- **Storage:** `<data_dir>/events/{date}/{session_id}-{pid}.arrow`

Each event carries only its own fields — `SessionStarted` carries the session/station/operator metadata once (and never a `run_id`); `RunStarted` carries the run/UUT context; `MeasurementRecorded` carries just the measurement. The flat run rows fill in the shared context when they're written.

## ChannelStore — Time-Series Data

Instrument reads that produce arrays, waveforms, or high-frequency scalar streams go to the ChannelStore. The event carries a compact reference URI (`channel://scope.ch1.waveform?session=…`) that points at the data, instead of the bulk samples.

- **Write path:** samples are buffered and written to rotating Arrow segments (each closed and readable after every flush).
- **Read path:** query the channel back, with optional downsampling for plotting.
- **Storage:** `<data_dir>/channels/{date}/{channel_id}_{session_short}.arrow`

Each channel gets its own IPC file with a schema inferred from the first write. Segment rotation ensures files are always readable (closed after each flush).

## RunStore — Analysis-Ready Results

The run store produces analysis-ready parquet files with one row per measurement, every row carrying its full context. This is what you query with DuckDB, Polars, or Spark for yield analysis and SPC.

- **Write path:** the runs daemon collects events as a run executes and writes the per-run parquet when the run ends.
- **Read path:** any Parquet reader (DuckDB, Polars, pandas).
- **Storage:** `<data_dir>/runs/{date}/{timestamp}_{run_id8}_{serial}.parquet`

The parquet files are built from the event stream — if the schema changes, they can be rebuilt from events.

### Live streaming + crash safety

During execution, the materializer holds row state in-process and flushes to a single per-run parquet at run end. The operator UI subscribes to the in-process event stream for live updates — there is no separate JSONL journal on disk. If the process is killed before the parquet is finalized, the close-time fallback writes whatever rows reached the materializer with `run_outcome = aborted`.

## FileStore — Captured Artifacts

Bulk artifacts — scope screenshots, camera frames, vendor capture files (`.tdms`, NPZ), any blob — go to the FileStore. Like the ChannelStore, the event carries a compact `file://` reference instead of the bytes.

- **Write path:** `observe(name, value)` with an image / `bytes` / `Path`, or `files.write` / `files.stream` directly.
- **Read path:** fetch the artifact by its `file://` reference.
- **Storage:** `<data_dir>/files/{date}/{session_id}/{filename}`

See [the three verbs](three-verbs.md) for how a test routes a value to the FileStore.

## How They Relate

```
Events (source of truth)
  ├── RunStore — per-run parquet, written when the run ends
  ├── ChannelStore — time-series; events carry a channel:// reference
  └── FileStore — artifacts; events carry a file:// reference
```

1. All activity flows through the EventStore — the source of truth.
2. The runs daemon collects events and writes a per-run parquet when the run ends.
3. Array/waveform values write to the ChannelStore and artifacts to the FileStore; the event keeps a `channel://` or `file://` reference.
4. Queries join across stores using `session_id`.

## Storage Layout

```
<data_dir>/
├── events/                    # EventStore
│   └── 2026-03-10/
│       └── {session_id}-{pid}.arrow
├── channels/                  # ChannelStore
│   ├── 2026-03-10/
│   │   ├── dmm.voltage_{session_short}.arrow
│   │   └── scope.ch1.waveform_{session_short}.arrow
│   └── _index.duckdb          # channel descriptors + query index (single file, disposable)
├── files/                     # FileStore
│   ├── 2026-03-10/
│   │   └── {session_id}/
│   │       └── setup_photo.png
│   └── _index.duckdb          # file catalog + query index (single file, disposable)
└── runs/                      # RunStore (parquet)
    ├── 2026-03-10/
    │   └── 20260310T143022Z_a1b2c3d4_SN001.parquet
    └── _index.<fingerprint>.duckdb   # content-addressed query index (see below)
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
data_dir: data       # writes to ./data/ instead of the global pool
```

## Schema evolution — HARD contract

Parquet files are the permanent record. Each litmus version may add columns; older files simply lack them. The parquet artifact is a **HARD contract**: changes must be additive because written files cannot be retroactively rewritten when a new version ships.

Until the 1.0 cut, the following invariants hold:

- **New columns only.** Every release may add columns. Existing column names, types, and semantics are stable across 0.x releases.
- **No removals or type changes** in 0.x. If a column would otherwise be removed or repurposed, it stays in the schema and reads as NULL for newly-written rows; the old meaning is documented as deprecated.
- **PK stability.** `(run_id, step_path, vector_index)` is the per-step identity in the materialized table; `(run_id, step_path, vector_index, measurement_name, vector_retry)` discriminates measurement rows. These tuples do not change shape in 0.x.
- **`record_type` discriminator stable.** The at-rest `'run'` / `'step'` / `'vector'` values are part of the wire format and do not change (the daemon also projects a `'measurement'` type at query time).
- **Read with `union_by_name=true`.** Consumer queries that follow the recommended `read_parquet(..., union_by_name=true)` pattern survive every additive evolution automatically.

```sql
-- DuckDB handles mixed schemas automatically
SELECT station_id, project_name, run_outcome
FROM read_parquet('~/.local/share/litmus/data/runs/**/*.parquet',
                  union_by_name=true)
```

Schema rewrites and column removals are deferred to the 1.0 cut, when a migration story for old files lands.

## The DuckDB query index

Every store keeps a DuckDB index alongside its data files, to speed up queries like `litmus runs` and the web UI. The index is always a **disposable cache** — delete it and it rebuilds from the parquet or Arrow files underneath. Deleting an index file never loses data.

Two index shapes are in use, depending on the store:

**RunStore — content-addressed.** The runs index file name embeds a short fingerprint of the query it serves — the shape of the projection plus the Litmus version that built it — as `_index.<fingerprint>.duckdb`. A query or schema change never rewrites an existing file in place: it produces a different fingerprint, and the next build writes a *new* file alongside any older ones. Multiple fingerprints can sit in the same `<data_dir>/runs/` directory at once — an older Litmus version (or another project pinned to an older release) reading the same results keeps opening its own file, untouched by a newer index being built next to it.

**ChannelStore and FileStore — a single `_index.duckdb`.** These two stores have not adopted the content-addressed model yet; each keeps one index file that's reused (or, on a schema change, deleted and rebuilt) in place. Same disposable-cache guarantee, no coexisting versions.

### Managing runs-index files

`litmus data index` manages the RunStore's index files (called *epochs* — one per fingerprint):

```cli
$ litmus data index list
  FINGERPRINT   SCHEMA  BUILT BY  SEEN BY       RUNS   SIZE    LAST SEEN
* a1b2c3d4e5f6  3       0.3.0     0.3.0         1,204  18.2 MB just now
  f0e1d2c3b4a5  2       0.2.4     0.2.4, 0.3.0  1,204  17.9 MB 6 days ago

$ litmus data index build            # warm the current epoch
$ litmus data index rm f0e1d2c3b4a5  # drop one epoch by its fingerprint prefix
$ litmus data index prune            # reap epochs no recent version has touched
```

- `list` — every epoch present, with its fingerprint, schema version, the Litmus version that built it, every version that has since read it, row count, size, and last access.
- `build` — warms the epoch matching the currently installed Litmus version by ingesting from parquet. `--rebuild` discards it first for a clean rebuild.
- `rm <fingerprint-prefix>` — deletes one epoch. Refuses to remove the epoch actively serving queries unless `--force`.
- `prune` — removes epochs no recent Litmus version has touched, always keeping the current epoch and (by default) the three most-recently-seen others.

None of these touch the parquet files. Every command is safe to run at any time — the worst case is that the next query pays the cost of a rebuild.

To force a full rebuild of the channels or files index, delete `_index.duckdb` under that store's directory — it rebuilds from the Arrow segments or file catalog on next access.

## Mixed versions on one machine

When multiple projects use different Litmus versions but share the global results directory:

| Layer | What happens | User impact |
|---|---|---|
| Parquet files | Each version writes its own schema. Newer files may have more columns. | NULL values for columns that didn't exist when the file was written. |
| Runs query index | Content-addressed: each Litmus version reads and writes its own epoch file, keyed by its own fingerprint. Old and new epochs coexist in `<data_dir>/runs/` without contention. | `litmus data index list` shows every epoch present; nothing to migrate by hand. Run `litmus data index prune` to reclaim disk from epochs no version uses anymore. |
| Channels / files query index | A single shared index file per store. | Rare cross-version friction can still surface here; delete `_index.duckdb` in that store's directory to force a rebuild. |
| Web UI / CLI | Shows whatever the current process's index has. | Some fields may be empty for older runs. |

The rule: newer is always a superset. An older Litmus version reading newer results ignores unknown columns; a newer version reading older results sees NULL for missing columns. No version corrupts or downgrades another's data.

## See also

**Same topic, other quadrants:**

- [Reference → Parquet schema](../../reference/data/parquet-schema.md) — column-level reference for the materialized run rows
- [Reference → Query API](../../reference/data/query-api.md) — `RunsQuery` / `StepsQuery` / `MeasurementsQuery` — the read path over the run store
- [Reference → `litmus data index`](../../reference/cli.md#cli-data-index) — full flag reference for `list` / `build` / `rm` / `prune`
- [How-to → Querying events](../../how-to/data/querying-events.md), [Querying channels](../../how-to/data/querying-channels.md) — task recipes against each store
- [How-to → Export results](../../how-to/data/export-results.md) — pulling rows out of the parquet store
- [Integration → Lakehouse import](../../integration/data/lakehouse-import.md) — pulling Litmus parquet into your warehouse

**Sibling concepts:**

- [Event log](event-log.md) — the source-of-truth event stream
- [Event sourcing](event-sourcing.md) — why the platform is event-sourced
- [Sessions](sessions.md) — the observation window that keys events
- [Flight streaming](flight-streaming.md) — the cross-process query model
