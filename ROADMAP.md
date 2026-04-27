# Litmus Roadmap

Active backlog (RICE-prioritized) and archive of shipped work. Items
graduate from **Backlog** to **Completed** on merge — never strike
through, just move.

---

## Backlog

### `litmus plan --profile=X` — dry-run what a profile resolves to

Profiles declaratively override vectors, limits, markers/facets, and
addopts. Today the only way to see what a profile *actually does* to a
given test suite is to run it.

A `litmus plan` subcommand would shell out to `pytest --collect-only`
under the given profile/station, then annotate each collected node
with:

- Vectors matrix (base vs profile override)
- Resolved limits per measurement label (base vs profile override,
  band match for condition-indexed limits)
- Active facets / spec / connections / markers
- Effective addopts

Implementation constraint: must **share** the plugin's resolution
helpers (`_resolve_entry`, `resolve_test_connections`, etc.), not fork them
— otherwise plan output drifts from actual runs.

**Why:** declarative config needs a companion "what does this
declarative config actually do" surface. Useful for CI triage, for
explaining a production run, and for catching profile/sidecar
mistakes before hitting hardware.

### Split into `pytest-litmus` + `litmus-test` (monorepo, two wheels)

Today `litmus-test` bundles CLI + platform + pytest plugin + UI + MCP
+ all deps (NiceGUI, FastAPI, uvicorn, duckdb, …). Users who only
want pytest integration pull in the full surface.

Split into:

- **`pytest-litmus`** — thin plugin wheel. `pytest_generate_tests`,
  marker registration, `context` / `verify` / `logger` / `spec` /
  `limits` fixtures, sidecar parsing. Depends on `litmus-test`.
- **`litmus-test`** — CLI, config/store, instruments, results/parquet,
  limits/derivation, models. Server + MCP gated as `[server]` /
  `[mcp]` extras.

Layout: `packages/pytest-litmus/` + `packages/litmus-test/` under a
uv workspace. Shared tests stay at repo root (or split per-package
for independent CI). Watch for circular imports — models
(`TestConfig`, `SpecContext`, `Limit`, `ProductCharacteristic`) must
live in `litmus-test`; the plugin is strictly a consumer.

Two steps — low-risk first:

1. Move UI/MCP/server deps into extras on the current single wheel
   (`litmus-test[server]`, `litmus-test[mcp]`). Captures ~80% of the
   install-weight benefit.
2. Carve `pytest-litmus` into its own wheel under the workspace.

**Why:** "platform, not framework" story — pytest is one consumer of
the platform, not the platform itself. Matches the
`pytest-django` / `pytest-asyncio` convention. Cheaper pre-1.0 than
after users pin transitive deps.

### CLI fallback for operator prompts (multi-DUT aware)

When running without the UI/server, operator prompts (e.g. "insert
DUT", "press button X", "verify LED is green") should fall back to
**terminal prompts** rather than being no-ops or silently blocking on
a UI that isn't running.

Multi-DUT scenarios require context in the prompt: the prompt must
identify **which DUT** ("DUT-2 of 4: insert board into socket B") so
the operator doesn't act on the wrong unit. Resolution path:

- Single source of truth for the prompt API — one `request_input()`
  surface that dispatches to UI (when the server is running) or CLI
  (when it isn't).
- CLI renderer shows the active DUT slot / serial / position from the
  current run manifest.
- Non-interactive mode (CI, `--yes`, `--no-prompt`) returns a default
  or fails loudly — never blocks silently.

**Why:** the bench-user / lab-tech path without the UI is
first-class; operator prompts shouldn't require running `litmus
serve`. Terminal is a perfectly good UI for one-operator-one-bench.

### Alternate runner wrappers — OpenHTF, unittest, Robot

The two-wheel split (above) carves pytest integration into
`pytest-litmus`. The same pattern extends to other test runners —
each one becomes a thin wrapper that consumes `litmus-test` core:

- **`openhtf-litmus`** — OpenHTF phase/plug wrapper. Primary
  migration path for existing OpenHTF suites. Phases call into the
  same `verify` / `logger` / `spec` surface; results land in the
  same parquet store.
- **`litmus-unittest`** — unittest `TestCase` mixin (`LitmusTestCase`)
  that exposes `self.verify(...)` / `self.logger.measure(...)`.
  For shops already on unittest who don't want to adopt pytest.
- **`litmus-robot`** — Robot Framework library that wraps the same
  verbs as keywords.

All three depend on `litmus-test`, share config/store/instruments/
results, and produce identical parquet rows. Differences are surface
only — how the test author declares a step and how the runner
dispatches it. Different entrypoints, same platform.

**Why:** reinforces the "platform, not framework" story. Existing
investments in OpenHTF / unittest / Robot shouldn't force a full
rewrite to benefit from Litmus's config system, instrument layer,
and results store. Each wrapper is a week or two of work once the
two-wheel split lands.

### Sequences for fine-grained execution control

Profiles (config overlay) and pytest classes (test grouping) cover
v1's "validate product X" use case. What they don't cover:
operator-pickable, ordered bundles with step-level dependencies —
"run smoke, then load only if smoke passed, with a dialog before
load." Today the curriculum has zero examples that need this; v1
ships without sequences and the existing `TestSequenceConfig` + UI
get deleted on `experiment/pytest-native-sequences` rather than
maintained as dead code.

If real factory-line demand emerges post-v1, design a minimal
sequence model that translates straight to pytest primitives:

- `tests:` list (test IDs / class IDs) → pytest argument order
- `markers:` filter expression → `-m "<expr>"`
- `steps[].depends_on:` → `pytest-dependency` semantics injected at
  collection time
- `abort_on_failure:` → `-x`

That's the whole shape — about 80% smaller than the deleted
`TestSequenceConfig`. Operator UI lists sequences by `id` /
`description`; picking one runs the translated pytest invocation
under the active profile.

**Why:** profile and sequence are orthogonal axes — profile is the
config lens, sequence is the execution plan. Same profile (config
for product X) supports multiple sequences (smoke / full /
characterization) without duplicating limits or mocks. Worth
rebuilding when there's a real operator-bundle requirement; not
worth carrying dead model surface in the meantime.

---

## In progress

_None._

---

## Completed

### Runner-neutral logic extracted from plugin.py — 2026-04-26

Pulled the test-execution code that doesn't depend on pytest into a
new `litmus.runner` package. The pytest plugin now delegates to it
through thin pytest-shaped adapters; `pytest-litmus` as a separate
distribution depending on `litmus-test` is one rename + entry-point
edit away. OpenHTF / Robot / unittest wrappers consume the same
shared modules.

New modules under `litmus.runner`:

| Module | Surface |
|---|---|
| `markers` | `MarkerSpec`, `entry_to_marker_specs`, `normalize_inline_list_payload`, `enforce_no_inline_stacking`, `extract_specs_characteristic` |
| `sweeps` | `sweep_to_parametrize_args`, `parametrize_calls_for_entry`, `parametrize_call_rows`, `runner_marker_parametrize_calls` |
| `cascade` | `cascade_for(sidecar, profile, cls, func) → TestEntry`, `find_unmatched_profile_keys` |
| `audit` | `audit_traceability(logger, *, strict, spec_active)` |
| `metadata` | `build_run_metadata(...)` taking already-resolved inputs |
| `instrument_events` | `emit_instrument_events(logger, event_log, records)` |
| `outputs` | `make_transport_callback`, `find_format_transport_callback`, `create_subscriber`, `run_configured_outputs` |
| `mocks` | `install_mocks(by_target, *, resolve_fixture, register_cleanup, fixture_lookup_error)` |
| `retry` | `retry_policy_to_flaky_kwargs(RetryPolicy)` |

`plugin.py` shrinks from 2,777 → 2,353 lines (~15%). What's left is
the pytest contract: hooks, fixtures, `pytest.Item` / `metafunc` /
`request` adapters. None of it is pytest-CAN-be-removed; it's
pytest-IS-the-API.

Also killed `parse_retry_marker_kwargs` — it was a one-line wrapper
around `RetryPolicy.model_validate`. Pydantic owns YAML / kwargs
validation; helper functions that re-implement it are dead weight.

**Followups (separate Backlog entries):**
- Rename `litmus.execution.plugin` → `litmus.pytest_plugin` (clearer
  it's the pytest adapter; touches ~20 references).
- Concrete `pytest-litmus` package split — entry-point + wheel
  packaging only; the code is already organized.
- First non-pytest runner wrapper (OpenHTF preferred) to validate the
  `litmus.runner` interface against a second consumer.

### YAML schema generalization — flat marker scope, typed sub-models — 2026-04-26

Sidecar / profile / per-test entries now share one flat shape.
`SidecarConfig`, `ProfileConfig`, and `TestEntry` are all the same
marker-scope model: Litmus marker fields (`limits`, `sweeps`, `mocks`,
`specs`, `connections`, `retry`, `prompts`) live directly at each
entry's root, alongside the reserved `runner:` and `tests:` keys.
Reserved keys are the only namespacing; everything else is a Litmus
marker name with a typed Pydantic sub-model.

```yaml
# tests/test_rail.yaml
limits:
  v_rail: {low: 3.2, high: 3.4, units: V}
sweeps:
  - {vin: [3.3, 5.0]}
runner:
  markers:
    - flaky: {reruns: 2}

tests:
  TestRails:
    limits:
      i_idle: {low: 0.0, high: 0.1, units: A}
    tests:
      test_strict:
        limits:
          v_rail: {low: 3.25, high: 3.35}
```

**Typed end-to-end.** Every Litmus-marker field is a Pydantic model
(`MeasurementLimitConfig`, `SweepEntry` with zip-coherence validator,
`MockEntry` with target shape validator, `ConnectionsBinding`,
`RetryPolicy`, `PromptConfig`). Pydantic validates at YAML load —
typos and type errors fail with structured messages before any test
runs. The hand-rolled parsers (`parse_limits_block`, `_LimitRef`,
`_PolicyLimit`, `_BandSet`, etc.) are gone; one resolver
(`resolve_limit`) walks the typed model directly.

**Catch-all bands.** `MeasurementLimitConfig.bands: list[Self]` makes
the model recursive: every band is itself a `MeasurementLimitConfig`
with its own `when:`. The parent (siblings to `bands:`) acts as the
catch-all when no band matches, by design of the type — no
`{when: {}}` workaround needed.

**Flat `runner:` block.** One runner per session means one schema
validates the whole runner block. `PytestRunner` (Pydantic,
`extra="forbid"`) catches `addopst:`-style typos at session start.
Ecosystem markers go under `runner.markers:` per scope.

**No-stacking enforcement.** Multiple `@pytest.mark.litmus_X(...)`
decorators on one function raise `pytest.UsageError`. Multi-axis
goes in the single payload list; `parametrize` is the explicit
exception via `runner.markers`.

All 1496 tests pass; all 7 example chapters pass end-to-end. Inline
`@pytest.mark.litmus_X` syntax unchanged in user code; YAML drops the
prefix because the entry's root is already Litmus-scoped. Pre-release,
no shims.

JSON Schema falls out of every model (`Model.model_json_schema()`),
ready for VS Code autocomplete via the Red Hat YAML extension once
schema-export is wired into `litmus init`.

**Followups:**
- Schema export → `.vscode/settings.json` for autocomplete in user
  projects (small, deferred).
- Lift runner-neutral logic out of `plugin.py` (separate Backlog
  entry; this PR was the prerequisite).
- Align runtime vocabulary to industry — `spec_*` → `characteristic_*`
  rename (separate Backlog entry; touches parquet schema, exporters,
  every measurement query).
