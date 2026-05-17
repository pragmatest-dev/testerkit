# Litmus Data Architecture

Snapshot for review on 2026-05-08. Reflects the post-`record_type` schema unification on branch `feat/unified-run-parquet`.

## Core idea

Litmus separates **the durable record of what happened** (events) from **the queryable analytical surface** (parquet runs + DuckDB indexes) from **the time-series channel data** (channel store).

- **Events** are the source of truth during a run. Append-only WAL. Per-process Arrow IPC files + a DuckDB events daemon that owns the index.
- **Run parquets** are sealed archival artifacts produced at end-of-run. One file per run, three row kinds (`record_type` of `'run'`, `'step'`, `'measurement'`) in one unified schema. Write-once. Portable, native, downstream-readable.
- **DuckDB indexes** are derived materialized views over parquet files. Auto-rebuilt on daemon spawn, auto-migrate via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- **Channel store** is a parallel data plane for time-series instrument data (waveforms, scalar streams) — separate Flight daemon, Arrow IPC files on disk.

## Stores

| Store | Path | Format | Owner | Mutability |
|---|---|---|---|---|
| Events index | `results/events/_index.duckdb` | DuckDB | events daemon | Append-only |
| Events log files | `results/events/{date}/{session_id}-{pid}.arrow` | Arrow IPC | producer process (per-session) | Append-only, segmented |
| Event refs | `results/events/{date}/{session_id}_ref/{vector_id}/{key}.{ext}` | Raw blobs | producer | Write-once |
| Run parquets | `results/runs/{date}/{timestamp}_{serial}.parquet` | Parquet | producer (or orphan sweep) | **Sealed write-once** |
| Run refs | `results/runs/{date}/{timestamp}_{serial}_ref/{vector_id}/{key}.{ext}` | Raw blobs | producer | Write-once |
| Runs index | `results/runs/_index.duckdb` | DuckDB | runs daemon | Derived from parquets; auto-migrating |
| Channels | `results/channels/{channel_id}_{seg}.arrow` | Arrow IPC | producer via channels daemon | Append-only, segmented |
| Channels index | `results/channels/_index.duckdb` | DuckDB | channels daemon | Derived |
| Catalog YAML | `catalog/`, `instruments/`, `stations/`, `products/`, `fixtures/`, `sequences/` | YAML | user (or `/catalog-from-datasheet`) | Hand-edited |

## Producers — what writes what

### 1. Test execution process (the primary writer)

Every test run is one producer process (a pytest invocation, or an orchestrator + N slot processes). Within that process:

- **EventStore + EventLog** — owns the per-session events. Every event:
  1. Validated against its Pydantic class
  2. Appended to a buffered Arrow IPC writer (`results/events/{date}/{session_id}-{pid}.arrow`); flushed every 50 rows (or on session close)
  3. Pushed via Flight `do_put` to the events daemon for immediate query visibility
  4. Dispatched to in-process `EventSubscriber` instances (e.g., `ParquetSubscriber`)
- **ParquetSubscriber** — an `EventSubscriber` that builds in-memory accumulator state from the event stream and writes the unified per-run parquet at `RunEnded`. Lives entirely in the producer process; dies with it.
- **ChannelStore client** — connects to the channels Flight daemon. `do_put`s channel sample batches; the daemon writes them to disk and propagates to subscribers.

### 2. Runs daemon's orphan-finalize sweep (secondary writer)

If a producer dies before `RunEnded`, the runs daemon's `LiveRunsSubscriber` detects it (PID liveness check, every 30s; wall-clock fallback at 1h) and writes a parquet from accumulator state via a fresh `ParquetSubscriber` instance inside the daemon. The on-disk file is indistinguishable from a clean producer-side close.

### 3. Hand-edited YAML

Catalog, instruments, stations, products, fixtures, profiles. Loaded by `litmus.store` at run start. Not part of the runtime data plane.

## Daemons

Three long-lived single-binary daemons, all spawned via the shared `DaemonManager` (file-locked, ref-counted, version-checked, gracefully upgraded). Each hosts a Flight server on a dynamically-assigned port; clients discover the port via a `_*_flight_port` file.

### Events DuckDB daemon (`_duckdb_daemon.py`)

- **Owns:** `results/events/_index.duckdb` and the events log directory.
- **Schema:** single `events` table with columns `(id, event_type, occurred_at, received_at, session_id, run_id, json, …)`.
- **Read path:** `EventStore.events()` and `EventStore.on_event()` go through Flight to query the `events` table.
- **Replay:** `on_event(replay="active_runs")` returns historical events for runs not yet ended, plus live updates as new events arrive — used by `LiveRunsSubscriber` on attach to rebuild accumulator state.
- **Cross-process subscriptions:** a watcher thread polls the events DB at 500ms intervals (with `received_at` cursor) to deliver new events to in-process subscribers in **other** Python processes. Single-process producers get events synchronously via `EventLog.add_subscriber`; cross-process subscribers go through this poll.

### Runs DuckDB daemon (`_runs_duckdb_daemon.py`)

- **Owns:** `results/runs/_index.duckdb` and the runs directory.
- **Tables:** `runs_persisted`, `steps_persisted`, `measurements_persisted`, `measurement_stats`, `measurement_io_schema`, `measurement_refs`, `_ingested` (file ledger).
- **Views:** `runs`, `steps`, `measurements` — `_persisted` UNION ALL the in-flight overlay (`inflight_runs`, `inflight_steps`, `inflight_measurements`).
- **Ingest:** a sweep walks `runs/{date}/*.parquet` on a thread, ingests new files into `_persisted` via `INSERT BY NAME ... FROM read_parquet({batch})` with `union_by_name=true`. Idempotent re-ingest via `DELETE WHERE file_path = ?` + insert.
- **Live overlay:** owns a `LiveRunsSubscriber` connected to the events daemon. On every event, dispatches to an `AccumulatorPool` keyed by `run_id`. The pool produces snapshot Arrow tables (`inflight_runs`, `inflight_steps`, `inflight_measurements`) registered with the daemon's DuckDB connection. The `runs` / `steps` / `measurements` views combine persisted + in-flight rows in one query.
- **Orphan sweep:** every 30s, walks the accumulator pool. For each open run, looks up the producer pid (captured from `SessionStarted`) and runs `os.kill(pid, 0)`. If dead, calls `_write_orphan_parquet` — synthesizes `RunEnded(outcome=aborted)` into the accumulator, writes a parquet via a fresh `ParquetSubscriber`, and emits `RunEnded` to the events DB so `events_for_active_runs` no longer reports the run as live.

### Channels Flight daemon (`channels/_flight_daemon.py`)

- **Owns:** `results/channels/`.
- **Storage:** per-channel Arrow IPC files, segmented by row count.
- **Read path:** Flight `do_get` over a SQL string; handler queries the index DB and the IPC files.
- **Write path:** Flight `do_put` from producer; daemon writes to disk and propagates the row to subscribers.

## Read clients

| Client | Reads from | Path |
|---|---|---|
| UI (NiceGUI) | runs daemon, events daemon, channels daemon | `RunsQuery` / `StepsQuery` / `MeasurementsQuery` / `EventStore.on_event` / channels client → Flight |
| HTTP API (FastAPI) | runs daemon | Same query classes; serves JSON to UI |
| MCP tools | runs daemon | Same query classes; serves AI agents |
| CLI (`litmus runs`, `litmus show`) | runs daemon | Same query classes |
| External (Pandas, Polars, Spark, Trino, …) | parquet directly | `read_parquet` glob on `results/runs/{date}/*.parquet` |
| External warehouse ingest | parquet directly | `COPY INTO` / `read_parquet` — must filter `WHERE record_type IN (...)` to split into normalized tables |

## Cross-process flow — happy path

```
Test process                                  Events daemon                    Runs daemon
─────────────                                  ─────────────                    ───────────
SessionStarted ─► EventLog
                  ├─► IPC file
                  └─► Flight do_put ──────► events table
                                              │
                                              └─► poll watcher ─► LiveRunsSubscriber ─► AccumulatorPool

RunStarted ────► (same path)                                                     │
                                                                                 ▼
StepStarted ───► (same path)                                                  inflight_runs / inflight_steps tables
MeasurementRecorded × N ────► (same path)                                     refresh; runs/steps/measurements
StepEnded ─────► (same path)                                                  views combine persisted + inflight
RunEnded ──────► (same path) ────► triggers ParquetSubscriber._write
                                   ↓
                          {timestamp}_{serial}.parquet ────► notify_daemon ─► ingest sweep ─► runs_persisted etc.
```

## Cross-process flow — recovery

```
Test process                            Events daemon                    Runs daemon
─────────────                            ─────────────                    ───────────
... emits events through RunStarted ...
... emits StepStarted, measurements ...
[process dies before RunEnded]          events table holds                AccumulatorPool holds
                                        partial run                        partial run state
                                              │
                                              └─── (every 30s) ─► LiveRunsSubscriber._sweep_once
                                                                     ├─► os.kill(pid, 0) → dead
                                                                     ├─► synthesize RunEnded(aborted)
                                                                     ├─► ParquetSubscriber._write
                                                                     │     → {timestamp}_{serial}.parquet
                                                                     ├─► event_store.emit(RunEnded(aborted))
                                                                     └─► AccumulatorPool.evict(run_id)

Daemon-offline window ──► events keep accumulating in IPC files + (when daemon comes back) events DB
                          On daemon spawn: replay="active_runs" rebuilds AccumulatorPool from events.
                          Next sweep cycle writes parquets for any still-orphaned runs.
```

## Architecture diagram

### Mermaid

```mermaid
flowchart TB
    subgraph Producer["Test execution process (one per pytest invocation / slot)"]
        EL[EventLog<br/>per-session writer]
        ES_p[EventStore]
        PS[ParquetSubscriber<br/>in-process EventSubscriber]
        CSC[ChannelStore client]
        EL --> PS
        ES_p --> EL
    end

    subgraph EventsD["Events daemon (process, single-binary)"]
        ED_DB[(events table<br/>DuckDB)]
        ED_FS[Flight server]
        ED_DB --- ED_FS
    end

    subgraph RunsD["Runs daemon (process, single-binary)"]
        RD_FS[Flight server]
        AP[AccumulatorPool<br/>per-run in-memory state]
        LRS[LiveRunsSubscriber<br/>+ orphan sweep thread]
        IS[Ingest sweep thread]
        RD_DB[(runs_persisted<br/>steps_persisted<br/>measurements_persisted<br/>+ measurement_stats /<br/>io_schema / refs<br/>DuckDB)]
        RD_VIEWS[runs / steps / measurements VIEWS<br/>= _persisted UNION ALL inflight_*]
        AP --> RD_VIEWS
        RD_DB --> RD_VIEWS
        RD_VIEWS --- RD_FS
        IS --> RD_DB
        LRS --> AP
    end

    subgraph ChanD["Channels daemon (process, single-binary)"]
        CD_FS[Flight server]
        CD_DB[(channel registry<br/>DuckDB)]
        CD_FS --- CD_DB
    end

    subgraph Disk["On-disk durable stores"]
        EL_FILES[results/events/{date}/<br/>{session_id}-{pid}.arrow]
        EL_REF[results/events/{date}/<br/>{session_id}_ref/]
        PARQUETS[results/runs/{date}/<br/>{timestamp}_{serial}.parquet]
        PARQUET_REF[results/runs/{date}/<br/>{timestamp}_{serial}_ref/]
        CHAN_FILES[results/channels/<br/>{channel_id}_{seg}.arrow]
        EVENTS_DB[(results/events/<br/>_index.duckdb)]
        RUNS_DB[(results/runs/<br/>_index.duckdb)]
        CHAN_DB[(results/channels/<br/>_index.duckdb)]
    end

    subgraph Clients["Read clients (separate processes)"]
        UI[UI / NiceGUI server]
        API[HTTP API / FastAPI]
        MCP[MCP server]
        CLI[CLI: litmus runs/show/discover]
        EXT[External: Pandas, Polars, Spark<br/>read parquet directly]
    end

    %% Producer write paths
    EL -->|append IPC<br/>flush every 50| EL_FILES
    EL -->|Flight do_put| ED_FS
    PS -->|atomic write at RunEnded| PARQUETS
    PS -->|save_ref large blobs| PARQUET_REF
    EL -->|save_ref large blobs| EL_REF
    CSC -->|Flight do_put| CD_FS

    %% Daemon ownership of disk
    ED_DB --- EVENTS_DB
    RD_DB --- RUNS_DB
    CD_DB --- CHAN_DB
    CD_FS -->|writes Arrow IPC| CHAN_FILES

    %% Cross-daemon: events → runs daemon
    ED_FS -.events subscription<br/>+ replay='active_runs'.-> LRS

    %% Runs ingest
    PARQUETS -.notify_daemon.-> IS
    IS -.read_parquet<br/>INSERT BY NAME.-> PARQUETS

    %% Orphan recovery
    LRS -.PID liveness check.-> Producer
    LRS -.orphan sweep<br/>writes ParquetSubscriber.-> PARQUETS
    LRS -.emit RunEnded(aborted).-> ED_FS

    %% Read paths
    UI -.Flight query.-> RD_FS
    UI -.Flight query.-> ED_FS
    UI -.Flight query.-> CD_FS
    API -.Flight query.-> RD_FS
    MCP -.Flight query.-> RD_FS
    CLI -.Flight query.-> RD_FS
    EXT -.read_parquet.-> PARQUETS

    classDef daemon fill:#e8f0fe,stroke:#1a73e8;
    classDef store fill:#fff4e0,stroke:#e8710a;
    classDef producer fill:#e6f4ea,stroke:#188038;
    classDef client fill:#fce8e6,stroke:#d93025;
    class EventsD,RunsD,ChanD daemon;
    class Disk,EL_FILES,EL_REF,PARQUETS,PARQUET_REF,CHAN_FILES,EVENTS_DB,RUNS_DB,CHAN_DB store;
    class Producer,EL,ES_p,PS,CSC producer;
    class Clients,UI,API,MCP,CLI,EXT client;
```

### Layered view (text)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Read clients (separate processes)                                      │
│   UI · HTTP API · MCP · CLI · external Pandas/Polars/Spark              │
└─────────────────────────────────────────────────────────────────────────┘
                              │ Flight queries
                              ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Events       │     │ Runs         │     │ Channels     │
│ daemon       │     │ daemon       │     │ daemon       │
│              │ ──► │  + Live      │     │              │
│ DuckDB       │ events  overlay   │     │ DuckDB       │
│ events table │ sub  AccumulatorPool    │ registry     │
└──────────────┘     └──────────────┘     └──────────────┘
       ▲                    ▲                    ▲
       │ Flight do_put      │ notify_daemon      │ Flight do_put
       │ + IPC files        │ + read parquet     │ + Arrow IPC
       │                    │                    │
┌──────────────────────────────────────────────────────────────┐
│  Producer — test execution process                            │
│   EventLog ──► IPC files                                      │
│   EventStore ─► Flight do_put                                 │
│   ParquetSubscriber ─► seal parquet at RunEnded               │
│   ChannelStore client ─► channels Flight                      │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Durable on-disk stores                                                 │
│   events/_index.duckdb · events/{date}/{session}-{pid}.arrow            │
│   runs/_index.duckdb · runs/{date}/{timestamp}_{serial}.parquet         │
│   channels/_index.duckdb · channels/{id}_{seg}.arrow                    │
│   _ref/ blob directories alongside each parquet / events session        │
└─────────────────────────────────────────────────────────────────────────┘
```

## Key invariants

1. **Events are the WAL.** Every event is durable on disk (IPC file) before the producer claims success on its emit call. The Flight `do_put` is best-effort — if it fails, the event is still in the IPC file and will be ingested by the daemon on its next scan / restart replay.
2. **Parquet is sealed write-once.** A run produces exactly one parquet, written atomically (`tmp.parquet → mv`) once at end-of-run (or by orphan sweep). The parquet is the archival record; everything in `_persisted` is derived.
3. **Per-run identity:** `(run_id, step_path, vector_index)` is the canonical identity for step rows. Measurements add `measurement_name` to that key. The run row is keyed by `run_id` alone. `record_type` is the explicit row-kind discriminator: `'run'`, `'step'`, or `'measurement'`.
4. **The events DB and the runs DB are independently versioned.** Each uses `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` so adding a column requires no migration script — existing on-disk DBs auto-upgrade on next daemon spawn.
5. **No cross-daemon writes.** Each daemon is the sole writer to its own DuckDB index. Cross-daemon coordination is via events (events daemon → runs daemon's `LiveRunsSubscriber`) and parquet drops (producer → runs daemon's ingest sweep).
6. **Recovery is via replay.** If any daemon restarts, it rebuilds in-memory state by replaying from durable storage (events IPC files + DuckDB events table for events daemon; parquet + events DB for runs daemon's accumulator).

## Open architectural questions

- **Recovery completeness.** When `daemon offline + producer dies + nobody starts daemon`, events stay in DB but no parquet is produced. A `litmus data materialize` recovery pass on daemon startup (replay events DB → parquets for any run with no parquet) would close this gap. Currently relies on the orphan sweep firing eventually.
- **`record_type` ingestion friction.** Lakehouse table formats (DuckLake, Delta, Iceberg) assume one parquet → one logical table. Our unified parquet uses `record_type` as a discriminator for three logical tables. Downstream lakehouse adoption uses a 3-line filtered `INSERT INTO runs / steps / measurements ... WHERE record_type = ...` transform — recipes for DuckDB / Snowflake / BigQuery / Delta / Iceberg / Pandas at `docs/integration/lakehouse-import.md`.
- **DuckLake as catalog replacement.** Could replace ~3K lines of hand-rolled `_runs_duckdb_daemon.py` ingest sweep + `_persisted` table management. Path A (one DuckLake table + views) is the smaller migration. 0.2.0 evaluation.
- **Channel store integration.** Currently a parallel data plane (separate daemon, separate format, separate catalog). Could be unified into the events / runs catalog if a future migration absorbs both. Out of scope for 0.1.0.
