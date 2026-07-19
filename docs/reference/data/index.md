# Reference — Data

The shapes the system writes. If you're reading parquet, the event log, or any export — these pages describe exactly what's in them.

- [Models](models.md) — every public Pydantic model + ERD of how they reference each other. Generated from source.
- [Event types](event-types.md) — every typed event payload the runtime emits. Generated from source.
- [Parquet schema](parquet-schema.md) — every column in the run parquet, the `record_type` discriminator, how retries land.
- [Events schema](events-schema.md) — the Arrow IPC format for the event log: the envelope columns every event carries, segment rotation, and the `schema_version` / `event_catalog_version` stamps.
- [Channels schema](channels-schema.md) — the Arrow IPC format for streaming numeric channels: per-channel columns, segment rotation, and the `schema_version` stamp.
- [Files schema](files-schema.md) — the blob + `.meta.json` sidecar format for file artifacts: on-disk layout, the sidecar fields, and the `schema_version` stamp.
- [Output formats](outputs.md) — what `testerkit show -f <fmt>` and `testerkit export` produce for HTML / PDF / JSON / CSV / STDF / HDF5 / TDMS / MDF4.
- [Query API](query-api.md) — `RunsQuery`, `StepsQuery`, `MeasurementsQuery`. The public read path the UI and HTTP API both use. Generated from source.

## See also

- [Concepts → Data](../../concepts/data/index.md) — event log, data stores, sessions, flight streaming
- [How-to → Data](../../how-to/data/index.md) — recipes for querying, exporting, debugging via the data plane
- [Integration → Grafana](../../integration/data/grafana.md) — pgwire data source over the same DuckDB views
