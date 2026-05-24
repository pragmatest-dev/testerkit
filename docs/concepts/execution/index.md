# Concepts — Execution

How a test run unfolds — the step model, what each step records, and how outcomes roll up from leaves to runs.

- [Step hierarchy](step-hierarchy.md) — how test classes, methods, and parametrize vectors nest into a tree of step events
- [Step manifest](step-manifest.md) — what each step records (inputs, outputs, measurements, retries) and how the materializer turns events into parquet rows
- [Outcomes](outcomes.md) — the severity ladder (`passed` → `failed` → `errored` → `skipped` → `done` → `terminated` → `aborted`) and how parent steps roll up child outcomes

## See also

- [How-to → Writing tests](../../how-to/writing-tests.md) — task recipe for authoring test code that fits the step model
- [Reference → Pytest fixtures](../../reference/litmus-fixtures.md) — the fixtures the step model exposes (`context`, `verify`, `logger`)
- [Data](../data/index.md) — where step events and the materialized run go after execution
