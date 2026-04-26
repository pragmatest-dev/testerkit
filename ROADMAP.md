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

### Lift runner-neutral logic out of `plugin.py`

The per-runner config namespace (above) generalizes the YAML
schema. The remaining barrier to a second runner is that
`plugin.py` (2,700+ lines) still mixes runner-neutral *behavior*
with pytest-specific *delivery*. An OpenHTF / Robot / unittest
wrapper today would have to copy substantial blocks of logic out
of `plugin.py` rather than import them.

Several modules already split cleanly:

- `profiles.py` — profile resolution, facet matching, test-phase
  demotion
- `sidecar.py` — sidecar YAML parsing, limit-band parsing
- `_state.py` — ContextVars for active limits / params /
  connections
- `expand.py` — range expanders (`linspace`, `arange`, etc.)
- `slot_runner.py` — multi-slot orchestrator
- `litmus.instruments.mocks` — `Mock(cls, **values)` factory
- `prompts.core` — operator prompt routing (runner-agnostic)
- `logger.py`, `spec_context.py` — both runner-agnostic

What's still glued inside `plugin.py` that other runners would
need:

| Logic | Why it's runner-neutral | Lift target |
|---|---|---|
| `_expand_litmus_vectors` | zip / cross-product / range-expander mechanics; only the *output shape* is pytest-specific | `execution/vectors.py` (return runner-neutral row dicts; pytest adapter shapes them into `metafunc.parametrize` args) |
| `_translate_retry_markers` | `litmus_retry` field validation + canonicalization; only the final translation step targets `pytest.mark.flaky` | `execution/retry.py` (runner-neutral `RetryPolicy` struct; per-runner adapter writes the runtime mapping) |
| Marker merge cascade — `_litmus_push_limits`, `_litmus_apply_mocks`, `_litmus_resolve_connections` | file → class → test → profile merge order; band resolution; mock dedup-by-target | `execution/marker_merge.py` (takes a list-of-lists of `ConfigEntry`, returns merged dict; pytest fixtures call into it) |
| `_build_run_metadata` | run_id / dut / station / operator assembly | `execution/run_metadata.py` (takes a runner-adapter for inputs) |
| `_mocks_active` (CLI > env > YAML precedence) | precedence rule | `execution/mock_resolution.py` |
| `_prompt_for_serial` / `_prompt_for_slot_serials` | session-start operator prompt for DUT serial | `execution/serial_prompt.py` |
| `_audit_traceability` | calibration / spec / instrument-id audit at end of test | `execution/audit.py` |
| `_autodiscover_product` (find product YAML by DUT serial) | filesystem lookup + name match | `execution/product_discovery.py` |

After the lift, `plugin.py` shrinks to thin adapters:

- pytest hooks (`pytest_configure`, `pytest_collection_modifyitems`,
  `pytest_generate_tests`, `pytest_runtest_*`)
- pytest fixture definitions (`verify`, `logger`, `spec`, `context`,
  `prompt`, `psu`, `dmm`, etc.)
- autouse fixture wrappers that call into the shared logic and
  push results into `_state.py` ContextVars
- pytest-specific concerns: `iter_markers`, `pytest.Item`, `request`
  manipulation, `pytest.UsageError` translation

Each runner wrapper (`openhtf-litmus`, `litmus-unittest`,
`litmus-robot`) imports the shared modules and writes its own
delivery layer — phase decorators / TestCase mixin / Robot library
keywords — that calls into the same merge / expansion / metadata /
audit code.

**Why this matters**: without this lift, "alternate runner
wrappers" (below) is weeks of re-implementation per runner. With
it, each wrapper is a thin adapter — closer to "a week or two"
that the next entry claims.

**Why not now**: the lift is a ~1-week refactor (pure module
moves, no behavior change), and there's no concrete second runner
yet. Do this as the prelude to the first OpenHTF / unittest /
Robot wrapper, not before — otherwise the abstraction shape gets
guessed and we relitigate it when the second runner reveals what
was actually needed.

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
