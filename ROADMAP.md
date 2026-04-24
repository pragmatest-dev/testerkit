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
- Active facets / bindings / markers
- Effective addopts

Implementation constraint: must **share** the plugin's resolution
helpers (`_resolve_entry`, `_load_test_binding`, etc.), not fork them
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

---

## In progress

_None._

---

## Completed

_None yet — this roadmap was seeded 2026-04-23._
