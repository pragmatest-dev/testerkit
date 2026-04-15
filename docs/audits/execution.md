# Execution Module Audit — Phase 2 Module 2

**Scope:** `litmus/execution/` — 15 files, 6.5k lines.
**Verdict:** structurally sound. No design emergencies.

## What's in here

| File | LOC | Role |
|---|---|---|
| `plugin.py` | 2161 | pytest plugin: hooks, fixtures, ContextVars, output wiring |
| `harness.py` | 1213 | `TestHarness`, `Context`, step/vector lifecycle |
| `logger.py` | 677 | `TestRunLogger`, emits to event log |
| `decorators.py` | 450 | `@litmus_test`, `@litmus_step`, `@measure` |
| `slot_runner.py` | 305 | Per-fixture-slot subprocess orchestrator (multi-DUT) |
| `sync.py` | 292 | Cross-process sync barriers (multi-DUT) |
| `runner.py` | 287 | Subprocess pytest runner used by the web UI |
| `typing_utils.py` | 238 | Actually AST helpers for `litmus new-test` scaffolding |
| `dut_provider.py` | 201 | `DUTProvider` protocol + CLI impl |
| `vectors.py` | 205 | Vector expansion |
| `limits.py` | 153 | Limit resolution |
| `slots.py` | 133 | `ResolvedSlot` model + validation |
| `_git.py` | 129 | git introspection for run metadata |
| `__init__.py` | 56 | public re-exports |
| `accessors.py` | 55 | tiny accessor shim |

## Findings

### 1. Public surface

- `__init__.py` re-exports `set_current_harness` and `set_current_logger`.
  These are ContextVar setters owned by the pytest plugin lifecycle; users
  shouldn't be calling them directly. They leak plugin plumbing as public
  API. **FIX NOW.** Drop from `__all__`, keep the functions where they
  are (tests use them internally, including `tests/test_execution/`).
- `measure` decorator is exported but only tests import it. Leave —
  `@measure` is a legitimate authoring tool, just not popular yet.
- Everything else in `__all__` is load-bearing.

### 2. ContextVar pattern

All 17 ContextVars follow the documented pattern (`_foo_var` private,
`get_foo()` / `set_foo()` public, setters return `None`). Session-scoped
getters create-and-store on first access; per-test state is cleaned in
`pytest_sessionfinish`. No stale-state risk. **LEAVE.**

### 3. `plugin.py` at 2161 lines

Four distinct responsibilities: pytest hooks, ContextVar state management,
step/sequence config resolution, DUT/fixture/instrument wiring. Natural
split into `plugin_hooks.py` + `plugin_state.py` + `plugin_config.py` +
`plugin_wiring.py`, but the functions are tightly coupled (hooks mutate
state, config resolution reads state). **REFACTOR later.** Tracked as a
follow-up; not urgent. Document the internal section boundaries with
header comments in the interim.

### 4. Harness / logger / plugin separation

Clean. Harness runs, logger emits, plugin wires. No duplication. **LEAVE.**

### 5. `runner.py` vs pytest plugin

Not a duplicate: `runner.py` is an async subprocess orchestrator used by
`litmus/api` and `litmus/ui` to run pytest out-of-process and stream
results to the browser. Different job from the in-process pytest plugin.
**LEAVE.** (But note the lack of tests — see §10.)

### 6. `sync.py` / `slots.py` / `slot_runner.py` / `dut_provider.py`

Multi-DUT testing machinery. Names are appropriate:

- `slots.py` = `ResolvedSlot` model (one fixture slot per DUT).
- `slot_runner.py` = subprocess runner per slot.
- `dut_provider.py` = DUT identity resolver (serial, part number).
- `sync.py` = cross-process barriers via the event store.

All load-bearing, all clear. **LEAVE.**

### 7. `typing_utils.py` — misnamed

Despite the name, this module is **not** general type-system helpers. It's
AST parsing/rewriting for the `litmus new-test` and `litmus update-types`
CLI commands — finds `@litmus_test` functions, injects instrument role
annotations. **FIX NOW.** Rename to `scaffold.py`, update the two CLI
import sites.

### 8. `_git.py`

Git metadata captured by the logger for run traceability. Small, focused,
only called by `logger.py`. The underscore is appropriate (internal
helper). **LEAVE.**

### 9. `decorators.py`

`@litmus_test` delegates to `_resolve_test_config()` and
`_resolve_instruments()` — clean internal factoring. **LEAVE.**

### 10. Test coverage gaps

Zero test files for `runner.py`, `typing_utils.py`, `_git.py`,
`accessors.py`. Low-risk (thin, functional), but worth a small test file
each. Tracked as a follow-up; not blocking.

### 11. Dead code / TODOs

None. Pyright clean. Ruff clean. No TODO/FIXME/XXX. **LEAVE.**

## Changes to apply now

1. Remove `set_current_harness` and `set_current_logger` from the
   `litmus.execution` `__all__`. They stay defined in `decorators.py`;
   tests that use them keep working via the explicit import path. Drop
   the re-export line from `__init__.py`.
2. Rename `litmus/execution/typing_utils.py` → `litmus/execution/scaffold.py`.
   Update importers (`litmus/cli.py` and any in-tree consumers). Confirm
   pyright/ruff/pytest stay green.

## Deferred (own commits later)

- Split `plugin.py` into `plugin_hooks.py` / `plugin_state.py` /
  `plugin_config.py` / `plugin_wiring.py`. Needs interface design for
  shared ContextVars.
- Add minimal test files for `runner.py`, `scaffold.py` (post-rename),
  `_git.py`.
