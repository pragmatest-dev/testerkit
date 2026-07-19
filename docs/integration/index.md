# Integration

TesterKit is built for incremental adoption. Start where the existing pain is, expand from there. Each path below is independent — you don't have to migrate everything at once. Same category axis as the rest of the docs ([concepts](../concepts/), [how-to](../how-to/), [reference](../reference/)).

## Runtime

Bring existing test code, runners, or hardware under TesterKit's runtime — adopt an existing pytest suite, drive TesterKit from a non-pytest runner, plug in your own instrument drivers. Mirrors [reference/runtime](../reference/runtime/) on the other axis.

- [Existing pytest projects](runtime/pytest-existing.md) — adopt TesterKit from a working pytest suite
- [Harness](runtime/harness.md) — the imperative `TestHarness` API for non-pytest runners (OpenHTF bridges, hand-written loops)
- [Instruments](runtime/instruments.md) — bring your own drivers (PyVISA / PyMeasure / vendor SDKs)

## Data

Get TesterKit's data into external systems — warehouse imports, dashboards, log streams, results submission from non-pytest sources.

- [Results API](data/results-api.md) — `TesterKitClient` for submitting runs from any language
- [Logging](data/logging.md) — patterns for capturing measurements alongside existing test code; bridging to Python's `logging`, syncing to external databases, sealing runs to cloud storage
- [Grafana](data/grafana.md) — pgwire data source plus a set of shipped dashboards
- [Lakehouse import](data/lakehouse-import.md) — pull TesterKit parquet runs into your warehouse
