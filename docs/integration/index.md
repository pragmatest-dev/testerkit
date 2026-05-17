# Integration

Litmus is built for incremental adoption. Start where the existing pain is, expand from there. Each path below is independent — you don't have to migrate everything at once.

## Start with results

You keep your existing test runner (LabVIEW, TestStand, plain Python) and only post results to Litmus.

- [Results API](results-api.md) — `LitmusClient` for submitting runs from any language
- [Logging](logging.md) — patterns for capturing measurements alongside existing test code

## Move test execution onto Litmus

You bring your existing pytest suite or OpenHTF tests under the Litmus runtime to pick up event sourcing, traceability, and the operator UI.

- [Existing pytest projects](pytest-existing.md) — adopt Litmus from a working pytest suite
- [OpenHTF adapter](openhtf-adapter.md) — bridge OpenHTF phase records into Litmus
- [Harness](harness.md) — the imperative `TestHarness` API for non-pytest runners

## Hardware and data

- [Instruments](instruments.md) — bring your own drivers (PyVISA / PyMeasure / vendor)
- [Lakehouse import](lakehouse-import.md) — pull Litmus parquet runs into your warehouse
