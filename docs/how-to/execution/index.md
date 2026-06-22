# How-To — Execution

Author test code and run it — limits, sweeps, retries, traceability, operator prompts.

- [Writing tests](writing-tests.md) — pytest classes, sidecar YAML, the `verify` pattern
- [Test limits](limits.md) — limit shapes, condition-indexed bands, comparator semantics
- [Test vectors & sweeps](vector-expansion.md) — sidecar `sweeps:`, `@parametrize`, the `vectors` fixture
- [Spec-driven testing](spec-driven-testing.md) — derive limits from the part YAML
- [Read and write the test context](test-context.md) — what `context` knows and how to use it
- [Profiles — named config sets](profiles.md) — select which tests run and how
- [Managing sessions](managing-sessions.md) — connect / disconnect lifecycle for instrument usage
- [Multi-UUT testing](multi-uut-testing.md) — subprocess-per-slot, shared instruments
- [Measurement traceability](traceability.md) — ATML / IEEE 1671 metadata captured automatically
- [Operator prompts](operator-prompts.md) — design guide for the `litmus_prompts` marker and the `prompt` fixture

## See also

- [Concepts → Execution](../../concepts/execution/index.md) — the step model, outcomes, what each step records
- [Reference → pytest fixtures](../../reference/pytest/fixtures.md) and [markers](../../reference/pytest/markers.md) — the per-test surface these recipes use
- [Tutorial](../../tutorial/) — sequential walk through the same recipes
