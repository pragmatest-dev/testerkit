# Integration — Runtime

Bring existing test code, runners, or hardware under Litmus's runtime. Mirrors [reference/runtime](../../reference/runtime/index.md) on the other axis: where the reference describes `LitmusClient` / `connect()` / the HTTP+MCP API, this section is the playbook for adopting each.

- [Existing pytest projects](pytest-existing.md) — adopt Litmus from a working pytest suite
- [Harness](harness.md) — the imperative `TestHarness` API for non-pytest runners (OpenHTF bridges, hand-written loops)
- [Instruments](instruments.md) — bring your own drivers (PyVISA / PyMeasure / vendor SDKs)

## See also

- [Reference → Runtime](../../reference/runtime/index.md) — the API surface these integration paths target
- [Concepts → pytest](../../concepts/overview/pytest.md) — why pytest is the primary runner; non-pytest paths are alternates
- [How-to → Configuring stations](../../how-to/configuration/configuring-stations.md), [Custom drivers](../../how-to/configuration/custom-drivers.md) — task recipes that build on the runtime bridges here
