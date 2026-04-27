# Public API Audit — Phase 2 Module 1

**Scope:** everything importable from the `litmus` package.
**Goal:** make the public surface intentional, documented, and aligned with
how users actually import.

## Today

`litmus/__init__.py` re-exports `LitmusClient`, `connect`, and `__version__`.
External callers (tests, demo, docs, scripts) reach much deeper — 40+ paths,
mostly legitimately public. The gap between declared and actual public API
is moderate.

## Stable public surface (as of 0.1.0)

These are the paths users and documentation should treat as stable. We
commit to pre-1.0 stability for them (breaking changes get a CHANGELOG
entry and a deprecation cycle where practical).

### Top-level

- `litmus.__version__`
- `litmus.LitmusClient` — programmatic access for scripting/interactive use
- `litmus.connect` — session-scoped context manager for ad-hoc work
- `litmus.litmus_test` — primary decorator (added to top level in this audit)
- `litmus.TestHarness` — direct harness construction for advanced cases
  (added to top level in this audit)

### Sub-namespaces (stable as a group)

- `litmus.models.*` — domain types (Product, StationConfig, Limit, …).
  Pure Pydantic, no I/O.
- `litmus.execution.*` — `litmus_test`, `litmus_step`, `measure`,
  `TestHarness`, `Context`, `Vector`, `expand_vectors`, plus contextvar
  setters used by pytest plugin.
- `litmus.config.*` — `load_test_config`, `get_test_config`, enum helpers,
  re-exported model types.
- `litmus.store` — central YAML I/O: `load_*`, `list_*`, `get_*`,
  `create_*`, `save_*` per entity, plus `find_yaml_files`.
- `litmus.matching.service` — capability matching.
- `litmus.data.models` — `TestRun`, `Measurement`, `Outcome`, `DUT`,
  `TestVector`, `TestStep`.
- `litmus.data.events` — event types (`MeasurementRecorded`, `RunStarted`, …).
- `litmus.data.event_log` — `EventLog`, `EventSubscriber`.
- `litmus.instruments.*` — `Mock`, `as_mock`, discovery functions, base
  `Instrument` / `VisaInstrument`. Observer classes stay private.
- `litmus.fixtures` — `FixtureManager`, `PinAccessor`.
- `litmus.products` — `Product`, `ProductCharacteristic`, `ProductFolder`,
  `SpecContext`, `ProductManifest`, workflow helpers.
- `litmus.dialogs` — operator dialogs.
- `litmus.schema_export` — `SCHEMA_MAP`, `FileType`, `export_schemas`.

### Internal (subject to change)

- `litmus.data.backends.*` — Parquet / IPC writer internals.
- `litmus.data.subscribers.*` — built-in exporters; add new ones via the
  `litmus.subscribers` entry point.
- `litmus.data.transports.*` — ship-to-bucket transports.
- `litmus.instruments.observers.*` — per-driver observers.
- `litmus.ui.*` — NiceGUI app internals; use `litmus serve`.
- `litmus.api.*` — FastAPI app internals; users call via HTTP, not Python.
- `litmus.mcp.*` — MCP tools; users invoke over MCP, not Python.
- `litmus.reports.*`, `litmus.grafana.*`, `litmus.analysis.*` — extension
  hooks for later releases; treat as internal for 0.1.0.
- `litmus.pytest_plugin`, `litmus.execution.logger._*`,
  `litmus.execution.accessors._*` — pytest plugin internals.
- Any attribute or module with a leading underscore.

## Changes to apply in the 0.1.0 line

Least-invasive first. Each change keeps the old import path working.

1. **Top-level re-exports.**
   `litmus/__init__.py` adds `litmus_test` and `TestHarness` from
   `litmus.execution`. Update `__all__`. README already uses
   `from litmus.execution import litmus_test`; the top-level path becomes
   the recommended form in new docs.

2. **Explicit `__all__` on `litmus/models/__init__.py`.** The package is
   intentionally empty today; add an `__all__` that re-exports the types
   most commonly imported (Product, ProductCharacteristic, StationConfig,
   StationInstrumentConfig, ProjectConfig, OutputConfig, FixtureConfig,
   TestSequenceConfig, InstrumentCatalogEntry, Limit, RetryConfig, plus
   the shared enums). Submodule imports continue to work.

3. **Explicit `__all__` on `litmus/store.py`.** Currently everything is
   implicitly public. Make the contract explicit by listing the
   `load_*` / `list_*` / `get_*` / `save_*` / `create_*` / `find_*`
   functions per entity. Private helpers (`_resolve_root`, `_read_yaml`,
   `_write_model`) already start with underscore.

4. **Schema namespace.** `litmus.schema_export` is the only path. Any
   stale `litmus.schemas` references in docs or examples get updated to
   `litmus.schema_export` (or to `litmus.models.project` for `OutputConfig`
   et al.). No compat shim — pre-release, no external users.

5. **Docstring pass on `litmus.execution`.** Clarify which helpers are
   public (`litmus_test`, `litmus_step`, `measure`, `TestHarness`,
   `Context`, `Vector`) vs. test/pytest plugin plumbing
   (`set_current_harness`, `set_current_logger`,
   `get_current_step_config`). Leave names alone for 0.1.0 — renaming
   would break tests.

6. **Observer classes in `litmus.instruments.observers`.** Keep the
   modules public (users can add observers) but mark the concrete
   implementations that tests import as "internal reference
   implementations; subject to change." No code change; docstrings only.

## Out of scope (deferred)

- Renaming anything across the 40+ external import sites. Stability
  before tidiness.
- Splitting `litmus.data` into public-types vs. internal-backends
  sub-packages. The current shape is fine; a future release can take this
  on with a deprecation cycle.
- Changing the `litmus.products` vs `litmus.models.product` split. They
  answer different questions (behavior vs. types); both stay.

## Verification

- Re-run the walkthrough from Phase 3 user-experience section using only
  paths listed in **Stable public surface** above.
- `uv run pyright` stays clean.
- `uv run pytest` stays green.
- Example projects (`examples/`) continue to work without changes.
