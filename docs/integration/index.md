# Integration

Litmus is built for incremental adoption. Start where the existing pain is, expand from there. Each path below is independent — you don't have to migrate everything at once. Same category axis as the rest of the docs ([concepts](../concepts/), [how-to](../how-to/), [reference](../reference/)).

## Configuration

Bring existing hardware, runners, or test code under Litmus.

- [Existing pytest projects](configuration/pytest-existing.md) — adopt Litmus from a working pytest suite
- [Harness](configuration/harness.md) — the imperative `TestHarness` API for non-pytest runners
- [Instruments](configuration/instruments.md) — bring your own drivers (PyVISA / PyMeasure / vendor)

## Data

Get Litmus's data into external systems — warehouse imports, dashboards, log streams, results submission from non-pytest sources.

- [Results API](data/results-api.md) — `LitmusClient` for submitting runs from any language
- [Logging](data/logging.md) — patterns for capturing measurements alongside existing test code; bridging to Python's `logging`, syncing to external databases, sealing runs to cloud storage
- [Grafana](data/grafana.md) — pgwire data source plus ten shipped dashboards
- [Lakehouse import](data/lakehouse-import.md) — pull Litmus parquet runs into your warehouse
