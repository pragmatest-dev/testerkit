# Flight Cross-Process Model

Litmus uses Apache Arrow Flight for cross-process data access. This enables real-time queries from any process — the operator UI, CLI tools, AI agents, or Grafana — without file locking or polling.

## Why Arrow Flight

Arrow Flight provides:

- **Zero-copy** — Arrow record batches transfer between processes without serialization overhead
- **Cross-process** — Multiple processes query the same data through a shared gRPC server
- **Language-agnostic** — Any Arrow Flight client (Python, Go, Rust, Java) can connect
- **SQL queryability** — [DuckDB](https://duckdb.org/) (an embedded analytical SQL engine that reads Parquet/Arrow directly) runs as the in-memory query engine behind the Flight server

The alternative — having each process read Arrow IPC files directly — creates file locking issues and can't provide real-time access to buffered (unflushed) data.

## Architecture

```
Process A (pytest)          Process B (litmus serve)
  │                           │
  ├── EventLog.emit()         ├── EventStore.events()
  │     ├── IPC file write    │     └── Flight do_get (SQL)
  │     └── Flight do_put ──► │                │
  │                           │                ▼
  │                      DuckDB Daemon (in-memory)
  │                           │
  │                      Flight gRPC Server
  │                           │
  └───────────────────────────┘
```

A ref-counted daemon process manages the DuckDB instance:

1. **First caller** spawns the daemon (detached process)
2. **Subsequent callers** increment the ref count and connect
3. **On release**, the ref is decremented; daemon exits after idle timeout

## How `connect()` Starts the Server

When `EventStore` is created, it calls `duckdb_manager.acquire(events_dir)` which:

1. Checks for an existing daemon at the events directory
2. If none exists, spawns one and writes the gRPC location to a lock file
3. Returns the `grpc://host:port` location string

The daemon bootstraps by scanning existing Arrow IPC files and registering them as a DuckDB table. New data arrives via Flight `do_put`.

## Dual-Write Path

EventStore uses a dual-write pattern for crash safety + queryability:

1. **Arrow IPC file** — append-only, survives crashes. One file per session, date-partitioned.
2. **Flight do_put** — pushes batches to in-memory DuckDB for immediate SQL access.

If the Flight push fails, data is still safe in IPC files. The daemon rebuilds its state from files on restart.

## Channel Queries with LTTB

ChannelStore has its own Flight server for time-series data. Queries support LTTB (Largest Triangle Three Buckets) decimation — a visually lossless downsampling algorithm that preserves peaks and valleys.

```python
# Query with decimation for visualization
table = channel_store.query(
    "scope.ch1_waveform",
    session_id="abc123",
    max_points=1000,  # LTTB downsample to 1000 points
)
```

This is critical for the operator UI, which may need to display waveforms with millions of samples.

## See Also

- [Event Log Architecture](event-log.md) — How events flow through the system
- [Three Stores Architecture](three-stores.md) — All three data stores
- [Querying Channels Guide](../how-to/querying-channels.md) — Practical channel queries
