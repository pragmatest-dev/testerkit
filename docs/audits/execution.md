# Execution Module Audit — Phase 2 Module 2

**Scope:** `litmus/execution/` — 15 files, 6.5k lines.
**Verdict:** structurally sound. No design emergencies.

## What's in here

| File | Role |
|---|---|
| `plugin.py` | pytest plugin: hooks, fixtures, ContextVars |
| `harness.py` | `TestHarness`, `Context`, step/vector lifecycle |
| `logger.py` | `TestRunLogger`, emits to event log |
| `slot_runner.py` | Per-fixture-slot subprocess orchestrator (multi-DUT) |
| `sync.py` | Cross-process sync barriers (multi-DUT) |
| `runner.py` | Subprocess pytest runner used by the web UI |
| `dut_provider.py` | `DUTProvider` protocol + CLI impl |
| `vectors.py` | Vector expansion |
| `limits.py` | Limit resolution |
| `slots.py` | `ResolvedSlot` model + validation |
| `_git.py` | git introspection for run metadata |
| `__init__.py` | public re-exports |
| `accessors.py` | tiny accessor shim |

## Findings

### 1. Public surface

- `__init__.py` re-exports `set_current_harness` and `set_current_logger`.
  These are ContextVar setters owned by the pytest plugin lifecycle; users
  shouldn't be calling them directly. They leak plugin plumbing as public
  API. **FIX NOW.** Drop from `__all__`, keep the functions defined where
  they are (tests use them internally, including `tests/test_execution/`).
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

### 7. `_git.py`

Git metadata captured by the logger for run traceability. Small, focused,
only called by `logger.py`. The underscore is appropriate (internal
helper). **LEAVE.**

### 8. Test coverage gaps

Zero test files for `runner.py`, `_git.py`, `accessors.py`. Low-risk
(thin, functional), but worth a small test file each. Tracked as a
follow-up; not blocking.

### 11. Dead code / TODOs

None. Pyright clean. Ruff clean. No TODO/FIXME/XXX. **LEAVE.**

## Changes to apply now

1. Remove `set_current_logger` from the `litmus.execution` `__all__`.
   It's a ContextVar setter owned by the pytest plugin lifecycle (defined
   in `_state.py`); tests that use it keep working via the explicit
   import path. Drop the re-export line from `__init__.py`.

## Deferred (own commits later)

- Split `plugin.py` into `plugin_hooks.py` / `plugin_state.py` /
  `plugin_config.py` / `plugin_wiring.py`. Needs interface design for
  shared ContextVars.
- Add minimal test files for `runner.py`, `_git.py`.
