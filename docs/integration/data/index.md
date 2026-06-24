# Integration — Data

Get Litmus's data into external systems — warehouse imports, dashboards, log streams, results submission from non-pytest sources.

- [Results API](results-api.md) — `LitmusClient` for submitting runs from any language (the canonical Python entry point for LabVIEW / TestStand bridges and scripts)
- [Logging](logging.md) — patterns for capturing measurements alongside existing test code; bridging to Python's `logging`, syncing to external databases, sealing runs to cloud storage
- [Grafana](grafana.md) — pgwire data source plus ten shipped dashboards
- [Lakehouse import](lakehouse-import.md) — pull Litmus parquet runs into your warehouse

## See also

- [Concepts → Data](../../concepts/data/index.md) — event log, data stores, sessions, flight streaming
- [How-to → Data](../../how-to/data/index.md) — recipes for querying, exporting, debugging via the data plane
- [Reference → Data](../../reference/data/index.md) — Pydantic models, event types, parquet schema, query API
- [Reference → Runtime](../../reference/runtime/index.md) — `LitmusClient`, `connect()`, HTTP + MCP API
