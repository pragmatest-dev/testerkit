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

- **Write path:** `EventLog.emit()` → buffered Arrow IPC → Flight `do_put` to DuckDB
- **Read path:** SQL via Flight `do_get`, or direct IPC file reads
- **Storage:** `results/events/{date}/{session_id}.arrow`

Events are normalized — `SessionStarted` carries run metadata once, `MeasurementRecorded` carries only measurement fields. Subscribers denormalize at write time.

## ChannelStore — Time-Series Data

Instrument reads that produce arrays, waveforms, or high-frequency scalar streams go to the ChannelStore. The EventStore gets a compact claim-check URI (`channel://scope.ch1/...`) instead of the raw data.

- **Write path:** `ChannelStore.write()` → buffered Arrow IPC with segment rotation
- **Read path:** `ChannelStore.query()` with LTTB decimation for visualization
- **Storage:** `results/channels/{date}/{channel_id}_{session_short}.arrow`

Each channel gets its own IPC file with a schema inferred from the first write. Segment rotation ensures files are always readable (closed after each flush).

## ParquetBackend — Materialized View

The Parquet backend produces analysis-ready files with one row per measurement, all metadata denormalized. This is what users query with DuckDB, Polars, or Spark for yield analysis and SPC.

- **Write path:** `ParquetSubscriber` listens to events, builds rows, writes on `RunEnded`
- **Read path:** Standard Parquet readers (DuckDB, Polars, pandas)
- **Storage:** `results/runs/{date}/{timestamp}_{serial}.parquet`

The Parquet files are a **materialized view** of the event stream. They can be regenerated from events if the schema changes.

## How They Relate

```
Events (source of truth)
  ├── EventStore (Arrow IPC + DuckDB)
  │     └── ParquetSubscriber → ParquetBackend (materialized view)
  └── ChannelStore (time-series, claim-check URIs in events)
```

1. All activity flows through `EventStore.emit()`
2. `ParquetSubscriber` watches events and builds Parquet rows
3. `InstrumentRead` events with array data write to `ChannelStore`, storing a URI in the event
4. Queries can join across stores using `session_id`

## Storage Layout

```
results/
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
├── runs/                      # ParquetBackend
│   └── 2026-03-10/
│       ├── 20260310T143022_SN001.parquet
│       └── 20260310T143022_SN001_ref/
└── sessions/                  # Session index
    └── sessions.json
```

## See Also

- [Event Log Architecture](event-log.md) — Deep dive into the event system
- [Sessions](sessions.md) — How sessions group activity
- [Flight Streaming](flight-streaming.md) — Cross-process query model
