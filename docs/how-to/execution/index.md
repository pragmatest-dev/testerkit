# How-To — Execution

Author test code and run it — limits, sweeps, retries, traceability, operator prompts.

- [Writing tests](writing-tests.md) — pytest classes, sidecar YAML, the `verify` pattern
- [Test limits](limits.md) — limit shapes, condition-indexed bands, comparator semantics
- [Test vectors & sweeps](vector-expansion.md) — sidecar `sweeps:`, `@parametrize`, the `vectors` fixture
- [Spec-driven testing](spec-driven-testing.md) — derive limits from the part YAML
- [Read and write the test context](test-context.md) — what `context` knows and how to use it
- [Profiles — named config sets](profiles.md) — select which tests run and how
- [Managing sessions](managing-sessions.md) — open, query, and prune interactive instrument sessions
- [Multi-UUT testing](multi-uut-testing.md) — run multiple UUTs in parallel, with shared instruments
- [Measurement traceability](traceability.md) — UUT / part / pin / instrument identity captured automatically
- [Operator prompts](operator-prompts.md) — pause a test for operator input with the `testerkit_prompts` marker and `prompt` fixture
- [Build a custom operator UI page](custom-operator-ui.md) — reuse the shared layout primitives and live-channel bindings to add your own NiceGUI page

## See also

- [Concepts → Execution](../../concepts/execution/index.md) — the step model, outcomes, what each step records
- [Reference → pytest fixtures](../../reference/pytest/fixtures.md) and [markers](../../reference/pytest/markers.md) — the per-test surface these recipes use
- [Tutorial](../../tutorial/) — sequential walk through the same recipes
