# Reference — Data

The shapes the system writes. If you're reading parquet, the event log, or any export — these pages describe exactly what's in them.

- [Models](models.md) — every public Pydantic model + ERD of how they reference each other. Generated from source.
- [Event types](event-types.md) — every typed event payload the runtime emits. Generated from source.
- [Parquet schema](parquet-schema.md) — every column in the run parquet, the `record_type` discriminator, how retries land.
- [Output formats](outputs.md) — what `litmus show -f <fmt>` and `litmus export` produce for HTML / PDF / JSON / CSV / STDF / HDF5 / TDMS / MDF4 / ATML.
- [Query API](query-api.md) — `RunsQuery`, `StepsQuery`, `MeasurementsQuery`. The public read path the UI and HTTP API both use. Generated from source.

## See also

- [Concepts → Data](../../concepts/data/index.md) — event log, three stores, sessions, flight streaming
- [How-to → Data](../../how-to/data/index.md) — recipes for querying, exporting, debugging via the data plane
- [Integration → Grafana](../../integration/grafana.md) — pgwire data source over the same DuckDB views
