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

### YAML schema generalization — runner-neutral `config:` + per-runner `runner:` namespace

Three threads landed together: (1) profile YAML's `pytest:` block
bakes pytest vocabulary into what's supposed to be a runner-neutral
schema and closes the door on third-party runners; (2) the existing
`config:` schema is a list-of-single-key-dicts with `litmus_X`
prefixes that are noise inside an already-Litmus-scoped block;
(3) the dict shape Pydantic naturally validates is one entry per
concept, not a list of entries we have to inspect by name. Solve
all three at once.

**Before** (current shape):

```yaml
config:
  - litmus_limits:
      v_rail: {low: 3.2, high: 3.4, units: V}
  - litmus_sweeps:
      - {vin: [3.3, 5.0]}
  - litmus_mocks:
      - {target: dmm.read, return_value: 3.31}
  - litmus_retry: {max_attempts: 3}
profiles:
  production:
    pytest:                       # pytest vocab inside the schema
      addopts: "-x"
      markexpr: production
```

**After** (proposed):

```yaml
config:                           # runner-neutral test config — dict-shaped
  limits:
    v_rail: {low: 3.2, high: 3.4, units: V}
  sweeps:
    - {vin: [3.3, 5.0]}
  mocks:
    - {target: dmm.read, return_value: 3.31}
  retry: {max_attempts: 3}

runner:                           # runner-specific — open-world map
  pytest:
    addopts: "-x"
    markexpr: production
    markers:                      # ecosystem markers (parametrize, flaky, skip) live here
      - flaky: {reruns: 2}
  openhtf:                        # third-party runner block; Litmus core ignores
    output_handler: json
```

**Three structural wins:**

1. **`config:` is dict-shaped** — one entry per Litmus concept
   (`limits`, `sweeps`, `mocks`, `spec`, `connections`, `retry`,
   `prompts`). No more list-of-single-key-dicts. Pydantic validates
   each key against its own typed model. Reads as "the test config
   has these aspects."
2. **No `litmus_` prefix in YAML** — the `config:` block is
   Litmus-scoped already; the prefix is noise. Inline decorators
   keep `litmus_X` because `pytest.mark.*` is shared with pytest
   and ecosystem plugins; YAML doesn't have that constraint.
3. **`runner:` namespace** — pytest-specific knobs (`addopts`,
   `markexpr`, ecosystem markers like `parametrize`, `flaky`,
   `skip`) move under `runner.pytest:`. Other runners get their
   own block. Open-world; third parties register namespaces
   without coordinating with Litmus core.

**No stacking of Litmus markers — let `parametrize` be special.**

The dict shape of `config:` enforces "one Litmus marker per scope"
by construction — you can't have two `sweeps:` keys in the same
YAML mapping. Apply the same rule to inline decorators:

```python
# OK — one of each Litmus marker per function
@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0]}])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4})
def test_x(...): ...

# Error — two litmus_sweeps on one function
@pytest.mark.litmus_sweeps([{"temp": [25, 85]}])
@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0]}])    # plugin errors
def test_x(...): ...

# OK — parametrize stacks (pytest's native behavior, kept)
@pytest.mark.parametrize("a", [1, 2])
@pytest.mark.parametrize("b", [3, 4])
def test_x(a, b): ...
```

If you want multiple sweeps (nested loops), put them in the one
list payload. If you want multiple limits, put them in the one
dict payload. Cross-scope layering (file → class → test → profile)
still merges as before — that's the right way to compose, not
stacking decorators on one function.

LLM guidance gets simpler: "one Litmus marker of each type per
test; use the payload to batch entries." `parametrize` is the
exception because pytest's stacking semantic is its own design
and useful in pytest-native code (inline decorators) that doesn't
go through Litmus's YAML schema at all.

**Inline (Python decorator) surface** stays exactly as it was after
the recent rename — pytest namespace requires the prefix:

```python
@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0]}])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4})
@pytest.mark.flaky(reruns=2)                   # ecosystem marker, no prefix needed
def test_x(...): ...
```

The translation rule between inline and YAML:
- Litmus markers: inline `litmus_X` ↔ YAML `config.X`
- Ecosystem markers: inline `pytest.mark.X` ↔ YAML `runner.pytest.markers: [- X: ...]`

YAML loader's job: map `config.<key>` to `litmus_<key>` marker
registration internally, so the plugin handlers (which look up
markers by their registered names) don't change.

**Pydantic shape** (sketch):

```python
class TestConfig(BaseModel):
    """Runner-neutral test config — one entry per concept."""
    model_config = {"extra": "forbid"}
    limits: dict[str, LimitConfig] = Field(default_factory=dict)
    sweeps: list[SweepDict] = Field(default_factory=list)
    mocks: list[MockDict] = Field(default_factory=list)
    spec: SpecBinding | None = None
    connections: ConnectionsBinding | None = None
    retry: RetryPolicy | None = None
    prompts: dict[str, PromptConfig] = Field(default_factory=dict)

class ProjectConfig(BaseModel):
    ...
    config: TestConfig = Field(default_factory=TestConfig)
    runner: dict[str, dict[str, Any]] = Field(default_factory=dict)

class ProfileConfig(BaseModel):
    ...
    config: TestConfig = Field(default_factory=TestConfig)
    runner: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

Each Litmus marker gets a typed Pydantic shape; `runner` stays
opaque dicts (each runner validates its own block with its own
schema). The `config:` schema is now strictly typed end to end.

**The `runner:` part of the proposal — what it carries:**

```yaml
# litmus.yaml — session-wide runner defaults
runner:
  pytest:
    addopts: "-x"
    keyword: rails

# profiles/production.yaml — overrides per scenario
runner:
  pytest:
    keyword: rails and not slow
  my_custom_runner:        # Litmus knows nothing about this; that's fine
    whatever: 42
```

```python
# Pydantic models in litmus core
class ProjectConfig(BaseModel):
    ...
    runner: dict[str, dict[str, Any]] = Field(default_factory=dict)

class ProfileConfig(BaseModel):
    ...
    runner: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

Litmus core stores the dict-of-dicts as opaque blobs and never
validates contents. Each runner extracts its own namespace at
session start and validates against its own Pydantic model:

```python
# pytest plugin — lives in litmus core (primary path)
project_block = project.runner.get("pytest", {})
profile_block = profile.runner.get("pytest", {}) if profile else {}
config = PytestRunner.model_validate({**project_block, **profile_block})

# Third-party MyRunner plugin — lives outside litmus
my_block = profile.runner.get("my_custom_runner", {})
config = MyRunnerConfig.model_validate(my_block)
```

**Open-world**: a third party ships a runner, claims a namespace,
and works without coordinating with Litmus core. Profiles authored
for runners Litmus doesn't know about pass through verbatim.

**What lives at each scope**

Project-level (`litmus.yaml: runner.<name>:`) holds always-on
defaults; profile-level (`profiles/<x>.yaml: runner.<name>:`) holds
per-scenario overrides. Sidecars and per-test stay out — sidecars
are test config (limits, vectors, mocks), per-test is markers.

For pytest specifically:

| Knob | Project | Profile |
|---|---|---|
| `addopts` (CLI args) | "always `--tb=short -v`" | append `-x` for production |
| `markexpr` (`-m`) | rare | **filtering: "production runs only `production` tag"** |
| `keyword` (`-k`) | rare | "characterization runs only `-k power_sweep`" |
| Plugins (`-p ...`) | "always load `pytest-xdist`" | "production also loads `pytest-html`" |
| Parallelism (`-n`) | default workers | "thermal-soak runs serial" |
| Timeout | session default | "soak profile relaxes to 1800s" |

OpenHTF, Robot, unittest map to similar shapes — output handlers,
filter expressions, stop-on-fail policies — but each runner names
its own fields. Litmus core neither knows nor cares.

**Filter use case (the killer profile demo)**

Today profiles override test config (limits, vectors, mocks). With
`runner.pytest.markexpr`, profiles also override **what runs**:

```yaml
# profiles/smoke.yaml
description: "Quick CI check"
facets: {test_phase: smoke}
runner:
  pytest:
    markexpr: "smoke and not slow"
    addopts: "-x"

# profiles/production.yaml
description: "Full production validation"
facets: {test_phase: production}
runner:
  pytest:
    markexpr: "production"
    addopts: "-x --tb=short"
```

`pytest --test-phase=smoke` selects the smoke profile, applies
`-m "smoke and not slow" -x`. Test selection becomes a profile
concern, not a CLI-args concern — the same lever that already
controls limits and vectors now also controls *which tests run*.

**Merge semantics — runner's choice**

Different fields merge differently. Each runner owns its merge
function; Litmus core hands the dicts and steps back.

| Field shape | Typical merge rule |
|---|---|
| Strings like `addopts` | **Concat** — `f"{project} {profile}"`. Appending args, not replacing. |
| Filter strings like `markexpr` | **AND** — `f"({project}) and ({profile})"`. Profile narrows. |
| Lists like `plugins` | **Extend** — profile adds to project's loadout. |
| Scalars like `parallelism`, `timeout` | **Override** — profile wins. |

```python
# pytest plugin's merge function (illustrative)
def merge_pytest_runner(proj: dict, prof: dict) -> PytestRunner:
    merged: dict[str, Any] = {**proj, **prof}     # scalar override default
    if "addopts" in proj or "addopts" in prof:
        merged["addopts"] = " ".join(filter(None, [proj.get("addopts"), prof.get("addopts")]))
    if "markexpr" in proj and "markexpr" in prof:
        merged["markexpr"] = f"({proj['markexpr']}) and ({prof['markexpr']})"
    if "plugins" in proj or "plugins" in prof:
        merged["plugins"] = [*proj.get("plugins", []), *prof.get("plugins", [])]
    return PytestRunner.model_validate(merged)
```

**Prior art**: this is the same shape as `pyproject.toml [tool.<name>]`
(PEP 518/621). The standard validates a few well-known tables
(`[build-system]`, `[project]`) and reserves `[tool.*]` for
arbitrary tools to claim their own namespace. Tools read their own
table; tools that don't recognize a `[tool.<x>]` block silently
ignore it. Python users have muscle memory for this pattern.

**Tradeoff**: open-world means typos in runner names (`pytset:`
instead of `pytest:`) silently fall through — the pytest plugin
sees an empty block and uses defaults; the user's intent is lost.
Mitigations: each runner logs when its block is empty / when it
applies non-default config; tooling can lint against known
runners.

**Touch points** (rough sweep, all pre-release — no shims):

- `src/litmus/config/test_config.py` — replace `ConfigEntry` /
  list-based `SidecarConfig.config` / `TestEntry.config` with the
  new `TestConfig` dict-shaped Pydantic model. Each key (`limits`,
  `sweeps`, `mocks`, ...) gets its own typed sub-model.
- `src/litmus/models/project.py` — `ProjectConfig.config: TestConfig`
  + `ProjectConfig.runner: dict[str, dict[str, Any]]`. Same on
  `ProfileConfig`. Drop `ProfilePytest` model; pytest plugin owns
  its own validation.
- `src/litmus/execution/sidecar.py` — sidecar loader produces
  `TestConfig` instances, not lists of `ConfigEntry`. Recursive
  `tests:` tree carries `TestConfig` at each scope.
- `src/litmus/execution/plugin.py` — pull markers from
  `TestConfig.<key>` instead of walking a list. Translate Litmus
  config keys to `litmus_<key>` for pytest's marker registration
  internally; ecosystem markers under `runner.pytest.markers`
  apply via `node.add_marker(name, ...)` in collection-time hook.
  Define `PytestRunner` Pydantic model; `pytest_configure` merges
  project + profile blocks and validates. **Plugin errors on
  duplicate `litmus_X` markers per function** (one per scope rule).
- All YAML files across the project — full schema migration:
  - `examples/04-sidecar-markers/tests/test_rail.yaml` and chapter
    5/6/7 sidecars/profiles → dict-shaped `config:`, drop
    `litmus_` prefix.
  - `tests/test_execution/test_*.py` pytester YAMLs and
    `tests/test_config/test_*.py` — dozens of YAML strings.
  - `tests/fixtures/specs/*.yaml` — verify (specs use SpecBand,
    not the config schema, so likely unaffected).
- All test code referencing the old shapes — `tests/test_config/`,
  `tests/test_execution/`.
- All inline decorators stay as-is in user code; plugin enforces
  no-stacking-Litmus rule at collection time with a clear error
  pointing to the consolidated form.
- `src/litmus/skills/refs/profiles.md` — schema example + marker
  table.
- `docs/reference/configuration.md` — profile + sidecar shape
  rewrites. Already touched recently; needs another pass.
- `docs/guides/*.md` — vocabulary throughout.
- `ROADMAP.md` — this entry merges with the dropped/expanded
  scope; nothing else to touch here.

**Why this all happens together**: each thread reinforces the
others. Dict shape eliminates list-noise. Dropping prefix
eliminates `litmus_` repetition. `runner:` namespace cleans up
the leftover pytest vocabulary in profile YAML. Doing them
piecemeal forces three migration passes through every YAML file
in the project; doing them together is one pass and a single
mental-model shift for users.

**Why now**: this is the last YAML-schema polish before v1.
Pre-release, no shim. Migration is one big sweep through tests,
examples, and docs (all of which have already been touched
recently for the marker rename, so this is the second pass that
locks the v1 surface).

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

_None yet — this roadmap was seeded 2026-04-23._
