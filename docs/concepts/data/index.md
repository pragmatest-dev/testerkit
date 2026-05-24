# Concepts — Data

Where the run data lives and how the platform stays consistent across processes. The event log is the source of truth; everything else is a derived view.

- [Event log](event-log.md) — the durable, append-only record of every test run; the source of truth
- [Event sourcing](event-sourcing.md) — why the platform is event-sourced rather than mutation-based; what that buys you for replay, debugging, and audit
- [Sessions](sessions.md) — connect-to-disconnect observation windows; how a single session can contain multiple runs (multi-DUT) or a long-running instrument session (operator UI, scripts)
- [Three stores](three-stores.md) — EventStore (events), ChannelStore (time-series), ParquetBackend (run rows); on-disk layout, `data_dir` resolution, schema-evolution contract
- [Flight streaming](flight-streaming.md) — cross-process data access via Apache Arrow Flight; why the platform uses it for low-latency queries

## See also

- [Reference → Event types](../../reference/event-types.md) — every event class the runtime emits, generated from source
- [Reference → Parquet schema](../../reference/parquet-schema.md) — every column in the materialized run parquet
- [Reference → Query API](../../reference/query-api.md) — `RunsQuery`, `StepsQuery`, `MeasurementsQuery` — the read path the UI and HTTP API both use
- [How-to → Querying events](../../how-to/data/querying-events.md), [Querying channels](../../how-to/data/querying-channels.md), [Export results](../../how-to/data/export-results.md)
