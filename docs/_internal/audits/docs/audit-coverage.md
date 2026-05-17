# Coverage audit: code → docs
**Date:** 2026-05-17
**Scope:** Whole `docs/` corpus (excluding `_internal/`)
**Direction:** Source code → docs (reverse of audit-accuracy)
**Inventory bootstrap:** `.tmp/public-surface-inventory.md` existed and was used; every surface re-verified against current source.

## Summary

| Surface | Total | DEFINED | SHALLOW | MENTIONED-ONLY | UNDOCUMENTED |
|---|---:|---:|---:|---:|---:|
| Pytest fixtures | 20 | 20 | 0 | 0 | 0 |
| Pytest markers | 7 | 7 | 0 | 0 | 0 |
| Per-role auto-fixtures (mechanism) | 1 | 1 | 0 | 0 | 0 |
| MCP tools | 12 | 12 | 0 | 0 | 0 |
| HTTP routes | 47 | 41 | 0 | 4 | 2 |
| CLI command groups | 14 | 10 | 0 | 1 | 3 |
| CLI subcommands | ~46 | 24 | 8 | 2 | 12 |
| Pytest plugin CLI flags | 14 | 9 | 0 | 1 | 4 |
| Pydantic models (public) | 47 | 27 | 11 | 5 | 4 |
| Pydantic fields (sampled) | n/a | n/a | n/a | n/a | n/a |
| Event classes | 31 | 22 | 0 | 7 | 2 |
| Parquet columns (static) | 64 | 64 | 0 | 0 | 0 |
| Parquet dynamic prefixes | 4 | 4 | 0 | 0 | 0 |
| Environment variables (public) | 12 | 4 | 0 | 1 | 7 |
| Top-level package exports | 6 | 6 | 0 | 0 | 0 |
| `LitmusClient` methods | 4 | 4 | 0 | 0 | 0 |
| `RunBuilder` methods + property | 5 | 5 | 0 | 0 | 0 |
| `StepBuilder` methods | 5 | 5 | 0 | 0 | 0 |
| `VectorBuilder` methods | 4 | 3 | 0 | 0 | 1 |
| `StationConnection` methods + props | 18 | 18 | 0 | 0 | 0 |
| `TestHarness` methods + props | 13 | 9 | 1 | 0 | 3 |
| `Context` methods + props | 19 | 16 | 0 | 0 | 3 |
| Range expanders | 5 | 5 | 0 | 0 | 0 |
| `Outcome` enum values | 7 | 7 | 0 | 0 | 0 |
| `Comparator` enum values | 10 | 10 | 0 | 0 | 0 |
| `Direction` enum values | 4 | 4 | 0 | 0 | 0 |
| `MeasurementFunction` enum values | 67 | 0 | 1 | 0 | 66 |
| `PinRole` enum values | 4 | 4 | 0 | 0 | 0 |
| Other enums (StrEnum classes) | 10 | 4 | 0 | 4 | 2 |
| **TOTAL (countable)** | **~430** | **~310** | **~21** | **~25** | **~119** |

The MeasurementFunction count dominates the "undocumented" total: 66 enum members listed in code, only 14 enumerated in the docs. Excluding that single enum, undocumented ≈ 53.

---

## Pytest fixtures

Reference page: `docs/reference/litmus-fixtures.md`. All 20 public fixtures are defined with scope, return type, and at least one code example.

| Symbol | Source | Status | Defining page |
|---|---|---|---|
| `logger` | `src/litmus/pytest_plugin/__init__.py:369` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `run_context` | `src/litmus/pytest_plugin/__init__.py:422` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `product_context` | `src/litmus/pytest_plugin/__init__.py:437` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `mock_instruments` | `src/litmus/pytest_plugin/__init__.py:559` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `station_config` | `src/litmus/pytest_plugin/__init__.py:571` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `fixture_config` | `src/litmus/pytest_plugin/__init__.py:606` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `instrument_records` | `src/litmus/pytest_plugin/__init__.py:652` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `instruments` | `src/litmus/pytest_plugin/__init__.py:695` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `instrument` | `src/litmus/pytest_plugin/__init__.py:762` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `dut` | `src/litmus/pytest_plugin/__init__.py:776` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `routes` | `src/litmus/pytest_plugin/__init__.py:877` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `pins` | `src/litmus/pytest_plugin/__init__.py:897` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `fixture_manager` | `src/litmus/pytest_plugin/__init__.py:919` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `sync` | `src/litmus/pytest_plugin/__init__.py:942` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `context` | `src/litmus/pytest_plugin/__init__.py:974` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `connections` | `src/litmus/pytest_plugin/__init__.py:980` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `verify` | `src/litmus/pytest_plugin/__init__.py:1008` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `limits` | `src/litmus/pytest_plugin/__init__.py:1020` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `vectors` | `src/litmus/pytest_plugin/__init__.py:1104` | DEFINED | `docs/reference/litmus-fixtures.md` |
| `prompt` | `src/litmus/pytest_plugin/__init__.py:1149` | DEFINED | `docs/reference/litmus-fixtures.md` |

Per-role auto-fixtures (dynamic from station YAML, `hooks.py:232-274`) — DEFINED in `litmus-fixtures.md#per-role-auto-fixtures` (mechanism + example).

## Pytest markers

| Symbol | Source | Status | Defining page |
|---|---|---|---|
| `litmus_limits` | `markers.py:31` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_sweeps` | `markers.py:32` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_mocks` | `markers.py:33` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_characteristics` | `markers.py:34` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_connections` | `markers.py:35` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_retry` | `markers.py:36` | DEFINED | `docs/reference/litmus-markers.md` |
| `litmus_prompts` | `markers.py:37` | DEFINED | `docs/reference/litmus-markers.md` |

## MCP tools

All 12 tools defined in `docs/reference/api.md#mcp-tools` (and summarized in `docs/reference/cli.md` setup section).

| Symbol | Source | Status |
|---|---|---|
| `litmus_project` | `mcp/server.py:282` | DEFINED |
| `litmus_discover` | `mcp/server.py:341` | DEFINED |
| `litmus_match` | `mcp/server.py:361` | DEFINED |
| `litmus_run` | `mcp/server.py:412` | DEFINED |
| `litmus_open` | `mcp/server.py:434` | DEFINED |
| `litmus_schema` | `mcp/server.py:454` | DEFINED |
| `litmus_events` | `mcp/server.py:474` | DEFINED |
| `litmus_sessions` | `mcp/server.py:499` | DEFINED |
| `litmus_channels` | `mcp/server.py:514` | DEFINED |
| `litmus_metrics` | `mcp/server.py:537` | DEFINED |
| `litmus_runs` | `mcp/server.py:589` | DEFINED |
| `litmus_steps` | `mcp/server.py:612` | DEFINED |

Also: `@mcp.prompt(name="datasheet-to-test")` — UNDOCUMENTED. Recommended home: `docs/reference/api.md` (MCP prompts subsection).

## HTTP routes

`docs/reference/api.md` defines most routes. Hidden docs routes (`/openapi.json`, `/docs`, `/redoc`) are linked but not table-listed (intentional — they're the OpenAPI surfaces themselves; counted as DEFINED).

| Method | Path | Status | Notes |
|---|---|---|---|
| GET | `/runs` | DEFINED | api.md#runs |
| GET | `/runs/{run_id}` | DEFINED | api.md#runs |
| GET | `/runs/{run_id}/measurements` | UNDOCUMENTED | api.md#runs lists this surface elsewhere but not explicitly; recommend adding |
| GET | `/runs/{run_id}/steps` | UNDOCUMENTED | similar |
| GET | `/runs/{run_id}/steps/tree` | MENTIONED-ONLY | implied by `litmus_steps` action="tree"; no HTTP row |
| GET | `/runs/{run_id}/ref` | DEFINED | api.md#runs |
| POST | `/runs` | DEFINED | api.md#runs |
| GET | `/runs/{run_id}/status` | DEFINED | api.md#runs |
| GET | `/active` | DEFINED | api.md#runs |
| GET | `/dialogs` | DEFINED | api.md#dialogs |
| POST | `/dialogs` | DEFINED | api.md#dialogs |
| GET | `/dialogs/{dialog_id}` | DEFINED | api.md#dialogs |
| GET | `/dialogs/{dialog_id}/wait` | DEFINED | api.md#dialogs |
| POST | `/dialogs/{dialog_id}/respond` | DEFINED | api.md#dialogs |
| GET | `/events` | DEFINED | api.md#events |
| GET | `/sessions` | DEFINED | api.md#sessions |
| GET | `/sessions/{session_id}` | DEFINED | api.md#sessions |
| GET | `/channels` | DEFINED | api.md#channels |
| GET | `/channels/_recent` | MENTIONED-ONLY | not explicitly tabled |
| GET | `/channels/{channel_id}` | DEFINED | api.md#channels |
| GET | `/products` | DEFINED | api.md#products |
| GET | `/products/{product_id}` | DEFINED | api.md#products |
| GET | `/products/{product_id}/requirements` | MENTIONED-ONLY | not in api.md HTTP tables |
| GET | `/stations` | DEFINED | api.md#stations |
| GET | `/stations/{station_id}` | DEFINED | api.md#stations |
| GET | `/stations/{station_id}/capabilities` | MENTIONED-ONLY | not in api.md HTTP tables |
| GET | `/match` | DEFINED | api.md#matching |
| GET | `/instruments/types` | DEFINED | api.md#instruments |
| GET | `/instruments/catalog/{entry_id}` | DEFINED | api.md#instruments |
| GET | `/instruments/assets` | DEFINED | api.md#instruments |
| GET | `/instruments/assets/{asset_id}` | DEFINED | api.md#instruments |
| GET | `/metrics/{summary,pareto,cpk,trend,retest,time-loss}` | DEFINED (×6) | api.md#metrics |
| GET | `/discover` | DEFINED | api.md#discovery |
| GET | `/open` | DEFINED | api.md#discovery |
| GET | `/schema/{yaml_type}` | DEFINED | api.md#discovery |
| POST | `/save/{entity_type}/{entity_id}` | DEFINED | api.md#discovery |
| GET | `/read` | DEFINED | api.md#discovery |
| GET | `/enum/{abbrev}` | DEFINED | api.md#discovery |
| GET | `/enum-reference` | DEFINED | api.md#discovery |

## CLI commands

`docs/reference/cli.md` has full sections for the headline commands. Many subcommands and flags are missing.

### Top-level groups (counted)

| Group | Status | Defining page |
|---|---|---|
| `litmus init` | DEFINED | cli.md#litmus-init |
| `litmus new-test` | UNDOCUMENTED | `docs/reference/cli.md` (mentioned once in a code-block example but no section) |
| `litmus validate` | UNDOCUMENTED | `docs/reference/cli.md` |
| `litmus serve` | DEFINED | cli.md#litmus-serve |
| `litmus runs` | DEFINED | cli.md#litmus-runs |
| `litmus show` | DEFINED | cli.md#litmus-show |
| `litmus export` | DEFINED (in outputs.md) | `docs/reference/outputs.md` |
| `litmus sbom` | UNDOCUMENTED | `docs/reference/cli.md` |
| `litmus discover` | MENTIONED-ONLY | tutorial mentions; no cli.md section |
| `litmus catalog datasheet` | UNDOCUMENTED | `docs/reference/cli.md` |
| `litmus station init/validate/update` | MENTIONED-ONLY | tutorial mentions `litmus station init`; no flags/section in cli.md |
| `litmus instrument list/show/cal` | UNDOCUMENTED | `docs/reference/cli.md` |
| `litmus schema export/refresh` | UNDOCUMENTED | `docs/reference/cli.md` |
| `litmus setup *` | DEFINED (×6) | cli.md#setup-commands |
| `litmus mcp serve` | DEFINED | cli.md#litmus-mcp-serve |
| `litmus metrics *` | DEFINED (×5 of 6) | cli.md#yield-manufacturing-metrics |
| `litmus metrics retest` | UNDOCUMENTED | cli.md (other 5 have subsections; retest is missing) |
| `litmus data prune/reindex` | SHALLOW | cli.md has stubs; no flag list, no example output |
| `litmus daemon status/restart/stop` | SHALLOW | cli.md has stubs; no flag list |
| `litmus grafana serve/setup/export` | DEFINED | `docs/how-to/grafana-dashboards.md` |

### Flags on documented commands

`litmus init --tier` accepts `bringup`/`bench`/`factory` — DEFINED.
`litmus init --ai` accepts `claude-code`/`claude-desktop`/`copilot` — DEFINED, but source also accepts `cursor` and `cline` (they're separate `setup` subcommands; the `--ai` integration option is the documented short list).
`litmus show -t/--template` and `--env` — `--env` flag UNDOCUMENTED (exists in code, not in cli.md).
`litmus metrics --group-by` (pareto) — UNDOCUMENTED.
`litmus data prune --type` (multi-value) — UNDOCUMENTED.
`litmus data prune --dry-run` — UNDOCUMENTED (in example but not in option table).
`litmus daemon restart/stop` — `TARGETS` argument and `--all` flag UNDOCUMENTED.
`litmus catalog datasheet -f/-o` — UNDOCUMENTED (whole command UNDOCUMENTED).
`litmus grafana setup` — flags like `--grafana-token`, `--grafana-url`, `--folder`, `--host`, `--port` mentioned in grafana-dashboards.md (DEFINED).

### Pytest plugin CLI flags (from `hooks.py:896` `pytest_addoption`)

| Flag | Status | Defining page |
|---|---|---|
| `--dut-serial` | DEFINED | reference/pytest-native.md, how-to/multi-dut-testing.md |
| `--dut-serials` | DEFINED | how-to/multi-dut-testing.md |
| `--slot` | UNDOCUMENTED | `docs/how-to/multi-dut-testing.md` |
| `--dut-part-number` | DEFINED | reference/pytest-native.md |
| `--dut-revision` | UNDOCUMENTED | `docs/reference/pytest-native.md` |
| `--dut-lot-number` | UNDOCUMENTED | `docs/reference/pytest-native.md` |
| `--station` | DEFINED | reference/pytest-native.md |
| `--operator` | MENTIONED-ONLY | examples only |
| `--data-dir` | DEFINED | reference/pytest-native.md |
| `--product` | DEFINED | reference/pytest-native.md |
| `--guardband` | DEFINED | reference/pytest-native.md, how-to/spec-driven-testing.md |
| `--mock-instruments` / `--no-mock-instruments` | DEFINED | reference/pytest-native.md |
| `--fixture` | DEFINED | reference/pytest-native.md |
| `--test-phase` | DEFINED | reference/cli.md (Test phase section) |
| `--strict-traceability` | UNDOCUMENTED | `docs/reference/pytest-native.md` |
| `--test-profile` / `--no-test-profile` | DEFINED | reference/pytest-native.md |
| Dynamic per-facet flags | UNDOCUMENTED | `docs/how-to/profiles.md` (mechanism note belongs there) |
| Dynamic per-required-input flags | UNDOCUMENTED | `docs/how-to/profiles.md` |

## Pydantic models (public)

`docs/reference/models.md` is the canonical home. It uses a mermaid ERD plus per-model field tables for the runtime classes. Coverage is patchy beyond the main runtime models.

| Model | Source | Status | Notes |
|---|---|---|---|
| `Outcome` | data/models.py:42 | DEFINED | models.md, parquet-schema.md, concepts/outcomes.md |
| `escalate_outcome` (free fn) | data/models.py | UNDOCUMENTED | concepts/outcomes.md is a natural home |
| `StimulusRecord` | data/models.py:18 | DEFINED | models.md |
| `Measurement` | data/models.py:170 | DEFINED | models.md |
| `Waveform` | data/models.py:452 | SHALLOW | mentioned in parquet-schema.md as a ref type; no field listing or example |
| `TestVector` | data/models.py:230 | DEFINED | models.md |
| `TestStep` | data/models.py:269 | DEFINED | models.md |
| `CollectedItem` | data/models.py:320 | MENTIONED-ONLY | named in `TestRun.collected_items` field; no defining row |
| `DUT` | data/models.py:351 | DEFINED | models.md |
| `RunSummary` | data/models.py:360 | SHALLOW | mentioned as return type; field list not enumerated |
| `TestRun` | data/models.py:385 | DEFINED | models.md |
| `Pin` | models/product.py:44 | DEFINED | models.md, configuration.md |
| `PinRole` | models/product.py | DEFINED | models.md, configuration.md (table) |
| `BusSignal` | models/product.py:76 | SHALLOW | shown in YAML schema in configuration.md; no field table |
| `SignalGroup` | models/product.py:94 | SHALLOW | YAML shape only |
| `ProductCharacteristic` | models/product.py | DEFINED | models.md, concepts/capability-model.md |
| `Product` | models/product.py:224 | DEFINED | models.md, configuration.md |
| `StationInstrumentConfig` | models/station.py:22 | DEFINED | models.md |
| `StationConfig` | models/station.py:50 | DEFINED | models.md, configuration.md |
| `InstrumentConfig` | models/station.py:73 | UNDOCUMENTED | `docs/reference/models.md` |
| `StationType` | models/station.py:84 | SHALLOW | ERD only, no field list |
| `validate_station_against_type` (free fn) | models/station.py | UNDOCUMENTED | `docs/reference/models.md` |
| `SpecQualifier` | models/capability.py | UNDOCUMENTED | `docs/reference/models.md` + capability-model.md |
| `RangeSpec` | models/capability.py:69 | SHALLOW | ERD only |
| `PointSpec` | models/capability.py:79 | UNDOCUMENTED | `docs/reference/models.md` |
| `ListSpec` | models/capability.py:92 | UNDOCUMENTED | `docs/reference/models.md` |
| `AccuracySpec` | models/capability.py:105 | SHALLOW | YAML shape in configuration.md; no field table; `total_uncertainty()` not documented |
| `ResolutionSpec` | models/capability.py:135 | SHALLOW | YAML shape only |
| `ChannelTopology` | models/capability.py:146 | DEFINED | catalog-schema.md |
| `SpecBand` | models/capability.py:171 | DEFINED | concepts/capability-model.md |
| `Signal` | models/capability.py:214 | DEFINED | concepts/capability-model.md |
| `Condition` | models/capability.py:251 | DEFINED | concepts/capability-model.md |
| `Control` | models/capability.py:278 | DEFINED | concepts/capability-model.md |
| `Attribute` | models/capability.py:312 | DEFINED | concepts/capability-model.md |
| `ConditionKey` | models/capability.py | SHALLOW | mentioned with a sample of keys in concepts/capability-model.md; no full enumeration of all 27 values |
| `Capability` | models/capability.py:431 | DEFINED | concepts/capability-model.md |
| `InstrumentCapability` | models/capability.py | DEFINED | concepts/capability-model.md |
| `band_matches` (free fn) | models/capability.py | UNDOCUMENTED | `docs/concepts/capability-model.md` |
| `SweepEntry` | models/test_config.py:45 | MENTIONED-ONLY | named in models.md, no defined shape |
| `MockEntry` | models/test_config.py:72 | DEFINED | reference/litmus-markers.md (#litmus_mocks) |
| `RetryConfig` | models/test_config.py:104 | DEFINED | reference/litmus-markers.md (#litmus_retry) |
| `TestEntry` | models/test_config.py:120 | DEFINED | models.md (ERD), configuration.md |
| `SidecarConfig` | models/test_config.py:183 | DEFINED | models.md, configuration.md |
| `Limit` | models/test_config.py:233 | DEFINED | models.md, configuration.md, reference/litmus-markers.md |
| `SwitchRoute` | models/test_config.py:345 | UNDOCUMENTED | `docs/reference/models.md` + `docs/concepts/fixtures.md` |
| `FixtureConnection` | models/test_config.py:366 | DEFINED | models.md |
| `FixtureSlot` | models/test_config.py:427 | UNDOCUMENTED | `docs/reference/models.md` + `docs/how-to/multi-dut-testing.md` |
| `FixtureConfig` | models/test_config.py:452 | DEFINED | models.md, configuration.md |
| `PromptConfig` | models/test_config.py:519 | DEFINED | reference/litmus-markers.md (#litmus_prompts) |
| `LimitLookupConfig` | models/test_config.py:536 | UNDOCUMENTED | `docs/reference/models.md` + `docs/how-to/limits.md` |
| `LimitStepConfig` | models/test_config.py:556 | UNDOCUMENTED | `docs/reference/models.md` + `docs/how-to/limits.md` |
| `MeasurementLimitConfig` | models/test_config.py:579 | DEFINED | reference/litmus-markers.md, how-to/limits.md |
| `InstrumentCatalogEntry` | models/catalog.py:20 | DEFINED | catalog-schema.md, models.md |
| `ChannelKind` | models/instrument.py | UNDOCUMENTED | `docs/reference/models.md` |
| `InstrumentInfo` | models/instrument.py:33 | UNDOCUMENTED | `docs/reference/models.md` |
| `CalibrationInfo` | models/instrument.py:83 | UNDOCUMENTED | `docs/reference/models.md` + `docs/concepts/fixtures.md` |
| `InstrumentRecord` | models/instrument.py:115 | MENTIONED-ONLY | named in litmus-fixtures.md as a return type; no field listing |
| `InstrumentAssetFile` | models/instrument_asset.py:14 | UNDOCUMENTED | `docs/reference/models.md` |
| `ProfileConfig` | models/project.py | MENTIONED-ONLY | name only in models.md; no field table |
| `MultiSlotConfig` | models/project.py:52 | MENTIONED-ONLY | one line in configuration.md, no fields |
| `ProjectConfig` | models/project.py:69 | DEFINED | configuration.md |
| `WorkflowStep` | models/product_manifest.py | UNDOCUMENTED | `docs/reference/models.md` |
| `FileReferences` | models/product_manifest.py:33 | UNDOCUMENTED | `docs/reference/models.md` |
| `ProductManifest` | models/product_manifest.py:45 | UNDOCUMENTED | `docs/reference/models.md` |

## Event classes

`docs/reference/event-types.md` is the canonical reference. Several events are not documented despite the page claiming to be complete.

| Class | event_type | Status | Notes |
|---|---|---|---|
| `EventBase` | (base) | DEFINED | event-types.md base-fields section |
| `SessionStarted` | session.started | DEFINED | event-types.md |
| `SessionEnded` | session.ended | DEFINED | event-types.md |
| `RunStarted` | run.started | DEFINED | event-types.md |
| `RunEnded` | run.ended | DEFINED | event-types.md |
| `RunMaterialized` | run.materialized | MENTIONED-ONLY | named in concepts/event-log.md only; no field table |
| `SlotStarted` | slot.started | MENTIONED-ONLY | concepts/event-log.md only; no field table |
| `SlotCompleted` | slot.completed | MENTIONED-ONLY | concepts/event-log.md only |
| `SyncArrived` | sync.arrived | MENTIONED-ONLY | concepts/event-log.md only |
| `SyncRelease` | sync.release | MENTIONED-ONLY | concepts/event-log.md only |
| `InstrumentConnected` | fixture.instrument_connected | DEFINED | event-types.md |
| `IdentityVerified` | fixture.identity_verified | DEFINED | event-types.md |
| `CalibrationWarning` | fixture.calibration_warning | DEFINED | event-types.md |
| `DutScanned` | fixture.dut_scanned | DEFINED | event-types.md |
| `InstrumentDisconnected` | fixture.instrument_disconnected | DEFINED | event-types.md |
| `StepsDiscovered` | test.steps_discovered | DEFINED | event-types.md |
| `StepStarted` | test.step_started | DEFINED | event-types.md |
| `MeasurementRecorded` | test.measurement | DEFINED | event-types.md |
| `RecordEvent` | test.record | DEFINED | event-types.md |
| `StepEnded` | test.step_ended | DEFINED | event-types.md |
| `RouteClosed` | route.closed | MENTIONED-ONLY | concepts/event-log.md only |
| `RouteOpened` | route.opened | MENTIONED-ONLY | concepts/event-log.md only |
| `InstrumentRead` | instrument.read | DEFINED | event-types.md |
| `InstrumentSet` | instrument.set | DEFINED | event-types.md |
| `InstrumentConfigure` | instrument.configure | DEFINED | event-types.md |
| `DiagnosticWarning` | diagnostic.warning | DEFINED | event-types.md |
| `DiagnosticError` | diagnostic.error | DEFINED | event-types.md |
| `StreamStarted` | stream.started | DEFINED | event-types.md |
| `StreamEnded` | stream.ended | DEFINED | event-types.md |
| `StreamFrameIndex` | stream.frame_index | DEFINED | event-types.md |
| `DialogOpened` | dialog.opened | DEFINED | event-types.md |
| `DialogResponded` | dialog.responded | DEFINED | event-types.md |

Category constants (`SESSION_EVENTS`, `RUN_EVENTS`, `SLOT_EVENTS`, `FIXTURE_EVENTS`, `TEST_EVENTS`, `ROUTE_EVENTS`, `INSTRUMENT_EVENTS`, `DIAGNOSTIC_EVENTS`, `STREAM_EVENTS`, `DIALOG_EVENTS`, `ALL_EVENTS`) — UNDOCUMENTED. Recommended home: `docs/reference/event-types.md`.

`Event` discriminated-union alias — DEFINED at event-types.md§Discriminated Union.

## Parquet columns

`docs/reference/parquet-schema.md` is canonical. All 64 static columns are listed with type and description. All 4 dynamic prefixes (`in_*`, `out_*`, `step_instruments_*`, `custom_*`) are explained with examples. The `_INSTR_ARRAY_TYPES` derivative (`step_instruments_mocked` as `list<bool>` while others are `list<string>`) is correctly documented.

`SCHEMA_VERSION` constant — DEFINED in parquet-schema.md§File-level metadata.

## Environment variables (public, non `_LITMUS_*`)

| Variable | Status | Defining page |
|---|---|---|
| `LITMUS_HOME` | DEFINED | cli.md (Environment Variables table) |
| `LITMUS_MOCK_INSTRUMENTS` | DEFINED | cli.md |
| `LITMUS_TEST_PHASE` | DEFINED | cli.md, plus reference/cli.md#test-phase chain |
| `LITMUS_TEST_PROFILE` | DEFINED | reference/pytest-native.md (default for `--test-profile`); cli.md misses it |
| `LITMUS_AUTO_CONFIRM` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_SERVER_URL` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_DAEMON_IDLE_TIMEOUT` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_DAEMON_SPAWN_TIMEOUT` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_DUT_SERIAL` | MENTIONED-ONLY | how-to/multi-dut-testing.md table only; not in cli.md env-var table |
| `LITMUS_DUT_PART_NUMBER` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_DUT_REVISION` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |
| `LITMUS_DUT_LOT_NUMBER` | UNDOCUMENTED | `docs/reference/cli.md#environment-variables` |

(Underscore-prefixed `_LITMUS_*` variables are internal multi-slot IPC; excluded from this audit.)

## Top-level package exports

| Symbol | Source | Status | Defining page |
|---|---|---|---|
| `__version__` | __init__.py | DEFINED | cli.md (`--version`) |
| `arange` | __init__.py → expand.py:44 | DEFINED | how-to/vector-expansion.md |
| `geomspace` | __init__.py → expand.py:60 | DEFINED | how-to/vector-expansion.md |
| `linspace` | __init__.py → expand.py:36 | DEFINED | how-to/vector-expansion.md |
| `logspace` | __init__.py → expand.py:52 | DEFINED | how-to/vector-expansion.md |
| `repeat` | __init__.py → expand.py:68 | DEFINED | how-to/vector-expansion.md |

## `LitmusClient` and builder classes

| Method/Property | Source | Status | Defining page |
|---|---|---|---|
| `LitmusClient.__init__` | client.py:360 | DEFINED | reference/client.md |
| `LitmusClient.start_run` | client.py:368 | DEFINED | reference/client.md |
| `LitmusClient.list_runs` | client.py:407 | DEFINED | reference/client.md |
| `LitmusClient.get_run` | client.py:418 | DEFINED | reference/client.md |
| `LitmusClient.get_measurements` | client.py:429 | DEFINED | reference/client.md |
| `RunBuilder.id` (property) | client.py:293 | DEFINED | reference/client.md |
| `RunBuilder.step` | client.py:298 | DEFINED | reference/client.md |
| `RunBuilder.finish` | client.py:319 | DEFINED | reference/client.md |
| `RunBuilder.abort` | client.py:329 | DEFINED | reference/client.md |
| `StepBuilder.vector` | client.py:166 | DEFINED | reference/client.md |
| `StepBuilder.measure` | client.py:189 | DEFINED | reference/client.md |
| `StepBuilder.fail` | client.py:235 | DEFINED | reference/client.md |
| `StepBuilder.skip` | client.py:241 | DEFINED | reference/client.md |
| `VectorBuilder.measure` | client.py:75 | DEFINED | reference/client.md |
| `VectorBuilder.fail` | client.py:135 | UNDOCUMENTED | reference/client.md (StepBuilder.fail is documented; VectorBuilder.fail isn't, even though it has identical signature) |
| `VectorBuilder.skip` | client.py:141 | DEFINED | reference/client.md (in vector code block) |

## `connect()` and `StationConnection`

All 18 public methods/properties DEFINED in `docs/reference/connect.md`:
`start`, `stop`, `instrument`, `release`, `configure`, `events`, `on_event`, `observe`, `sync`, `start_instrument_server`, properties `session_id`, `config`, `instruments`, `event_log`, `event_store`, `channel_store`, `instrument_server_address`, plus `__enter__`/`__exit__`.

## `TestHarness` + `Context`

`docs/integration/harness.md` is canonical for `TestHarness`. `docs/reference/models.md§Context (Execution Module)` is canonical for `Context`.

### `TestHarness`

| Member | Source | Status |
|---|---|---|
| `TestHarness.__init__` | harness.py:505 | DEFINED |
| `vectors` (property) | harness.py:588 | DEFINED |
| `current_vector` (property) | harness.py:594 | UNDOCUMENTED |
| `retry_config` (property) | harness.py:599 | UNDOCUMENTED |
| `context` (property) | harness.py:604 | DEFINED |
| `run_context` (property) | harness.py:621 | DEFINED |
| `prompt` | harness.py:625 | DEFINED |
| `measure` | harness.py:824 | DEFINED |
| `record` | harness.py:947 | SHALLOW (one mention, no example; key/value semantics not described) |
| `run_vector` | harness.py:1065 | DEFINED |
| `run_with_retry` | harness.py:1154 | UNDOCUMENTED (Recommended home: integration/harness.md) |
| `step` | harness.py:1205 | DEFINED |
| `run_all` | harness.py:1258 | UNDOCUMENTED (Recommended home: integration/harness.md) |

### `Context`

| Member | Source | Status |
|---|---|---|
| `child` | harness.py:167 | DEFINED |
| `configure` | harness.py:179 | DEFINED |
| `observe` | harness.py:190 | DEFINED |
| `changed` | harness.py:209 | DEFINED |
| `last` | harness.py:225 | DEFINED |
| `configure_all` | harness.py:250 | DEFINED |
| `observe_all` | harness.py:259 | DEFINED |
| `set_params` | harness.py:268 | UNDOCUMENTED |
| `set_observations` | harness.py:276 | UNDOCUMENTED |
| `get_param` | harness.py:288 | DEFINED |
| `get_observation` | harness.py:304 | DEFINED |
| `params` (property) | harness.py:321 | DEFINED |
| `observations` (property) | harness.py:330 | DEFINED |
| `characteristics` (property) | harness.py:339 | DEFINED |
| `limits` (property) | harness.py:350 | DEFINED |
| `run` (property) | harness.py:366 | DEFINED |
| `station` (property) | harness.py:379 | DEFINED |
| `product` (property) | harness.py:392 | DEFINED |
| `get_limit` | harness.py:406 | DEFINED |
| `measure` | harness.py:433 | UNDOCUMENTED (`context.measure` is in source but no doc mentions; `harness.measure` is documented but `context.measure` is a separate call shape) |
| `LimitsView` (class) | harness.py:44 | DEFINED (as the type of `context.limits` / `limits` fixture; no class-level field table needed) |

## Range expanders

| Symbol | Source | Status | Defining page |
|---|---|---|---|
| `linspace` | expand.py:36 | DEFINED | how-to/vector-expansion.md (table + example) |
| `arange` | expand.py:44 | DEFINED | how-to/vector-expansion.md |
| `logspace` | expand.py:52 | DEFINED | how-to/vector-expansion.md |
| `geomspace` | expand.py:60 | DEFINED | how-to/vector-expansion.md |
| `repeat` | expand.py:68 | DEFINED | how-to/vector-expansion.md |

## Enums

### `Outcome` (data/models.py:42) — 7 values, all DEFINED in models.md§Outcome and parquet-schema.md§Outcome.
### `Comparator` (enums.py:264) — 10 values, all DEFINED in configuration.md§Comparator + parquet-schema.md§Comparator + models.md.
### `Direction` (enums.py:15) — 4 values (`INPUT, OUTPUT, BIDIR, TRANSFORM`), DEFINED in models.md ERD; `TRANSFORM` is the only one with shallow coverage (no example).
### `PinRole` (product.py) — 4 values (`SIGNAL, GROUND, POWER, REFERENCE`) — DEFINED in models.md + configuration.md§Pin Types.

### `MeasurementFunction` (enums.py:24) — 67 values

models.md§MeasurementFunction ERD lists ~14 values + "etc"; configuration.md lists ~9 in YAML schema with "...". 53 values are UNDOCUMENTED. Examples: `RF_PM`, `RF_AM`, `RF_FM`, `RF_SWEEP`, `RF_IQ`, `RF_PULSE`, `S_PARAMETERS`, `PHASE_NOISE`, `NOISE_FIGURE`, `HARMONICS`, `DIGITAL_PATTERN`, `SERIAL_DATA`, `DIODE`, `CONTINUITY`, `DC_RATIO`, `QUALITY_FACTOR`, `DISSIPATION_FACTOR`, `TIME_INTERVAL`, `PULSE_WIDTH`, `RISE_TIME`, `FALL_TIME`, `PHASE`, `POWER_QUALITY`, `JITTER`, `EYE_DIAGRAM`, `THD`, `SNR`, `GAIN`, `RETURN_LOSS`, `INSERTION_LOSS`, `VSWR`, `GROUP_DELAY`, `WAVELENGTH`, `CHARGE`, `MAGNETIC_FIELD`, `POSITION`, `LOCK_IN_DETECTION`, `HEATER_POWER`, `EXCITATION_CURRENT`, `PULSE_GENERATION`, `TRIGGER`, `REFERENCE_CLOCK`, `CONDUCTANCE`, `REACTANCE`, `SUSCEPTANCE`, `DYNAMIC_LOAD`, `IMPEDANCE`, `INDUCTANCE`, `CAPACITANCE`, `RESISTANCE_4W`, `PERIOD`, `DC_POWER`, `AC_POWER`, `SPECTRUM`, `DUTY_CYCLE`, `OPTICAL_POWER`, `HUMIDITY`, `DIGITAL_IO`, `RF_CW`, `RF_POWER`. Recommended home: `docs/reference/models.md` — replace the truncated ERD with a full enumeration table.

### Other enums

| Enum | Source | Status | Notes |
|---|---|---|---|
| `MatchDepth` | enums.py:246 | MENTIONED-ONLY | named in models.md ERD; values listed but no defining section |
| `InstrumentType` | enums.py:297 | UNDOCUMENTED | 24 values; not enumerated anywhere |
| `WaveformShape` | enums.py:155 | UNDOCUMENTED | 8 values; not enumerated anywhere |
| `TerminalRole` | enums.py:173 | MENTIONED-ONLY | partial list in catalog-schema.md inline comment (`hi, lo, sense_hi, sense_lo, guard, signal, trigger`); full 12 values not enumerated; missing `SENSE_HI`, `SENSE_LO`, `HCUR`, `HPOT`, `LCUR`, `LPOT` distinction from prose |
| `ConnectorType` | enums.py:198 | MENTIONED-ONLY | catalog-schema.md inline comment names a few; 19 values total |
| `GroundTopology` | enums.py:190 | DEFINED | catalog-schema.md inline value list (`floating, shared, earth`) — all 3 values listed |
| `ChannelKind` | models/instrument.py | UNDOCUMENTED | 4 values (`read, set, control, configure`) |
| `SpecQualifier` | capability.py | UNDOCUMENTED | 4 values (`GUARANTEED, TYPICAL, NOMINAL, SUPPLEMENTAL`) |
| `ConditionKey` | capability.py | SHALLOW | concepts/capability-model.md groups some keys ("Signal" → `signal_level, crest_factor`), but the full 27-value enum is not enumerated |
| `WorkflowStep` | product_manifest.py | UNDOCUMENTED | 6 values |
| Module constants `COAXIAL_CONNECTORS`, `TRIAX_CONNECTORS` | enums.py | UNDOCUMENTED | (frozensets of `ConnectorType`) |

---

## Findings

### High-impact undocumented surfaces

Ranked by likely user reach (high = a beginner running `pytest` or `litmus serve` will hit it without warning):

1. **`MeasurementFunction` enum — 53 of 67 values undocumented.** Every product spec / catalog YAML uses one of these values in `function:`. Users guessing by analogy will get unhelpful Pydantic validation errors with no doc to point them at. **Highest-impact gap.**
2. **Environment variables: `LITMUS_AUTO_CONFIRM`, `LITMUS_SERVER_URL`, `LITMUS_DUT_PART_NUMBER` / `_REVISION` / `_LOT_NUMBER`, `LITMUS_DAEMON_IDLE_TIMEOUT` / `_SPAWN_TIMEOUT`.** All exist in source and several are operator-facing knobs; cli.md's "Environment Variables" table lists only 3 of the 12 public vars.
3. **`litmus validate`, `litmus sbom`, `litmus discover`, `litmus catalog datasheet`, `litmus instrument {list,show,cal}`, `litmus schema {export,refresh}`, `litmus station {init,validate,update}`, `litmus new-test`, `litmus metrics retest`.** Nine top-level CLI commands users will hit from `litmus --help` with no documentation. `litmus discover` and `litmus station init` get name-drops in tutorials but no flag tables.
4. **Pytest plugin flags: `--strict-traceability`, `--slot`, `--dut-revision`, `--dut-lot-number`, the dynamic `--<facet>` and `--<required-input>` flags.** `--strict-traceability` is the gate for production-grade traceability and is silently undocumented; the dynamic per-facet flag mechanism is the headline feature of profiles and is referenced from nowhere.
5. **`SlotStarted`, `SlotCompleted`, `SyncArrived`, `SyncRelease`, `RouteClosed`, `RouteOpened`, `RunMaterialized` events.** event-types.md claims to be the canonical reference and is missing seven event classes. Multi-DUT and switch-route users will look here, find nothing about their events, and assume the events don't exist.

### Coverage gaps by section

| Section | Total surfaces in scope | Undocumented | Notes |
|---|---:|---:|---|
| `docs/reference/cli.md` | ~46 CLI subcommands + 12 env vars + 14 plugin flags | ~25 | Biggest single doc-area gap. The page documents the "headline" UX path (init, serve, runs, show, setup) but skips the entity-management commands and most env vars. |
| `docs/reference/models.md` | ~47 Pydantic models + 14 enums | ~22 | Has good coverage of runtime models (`TestRun`, `Measurement`, `TestStep`, `TestVector`) and the capability ERD, but the test-config schema (`SwitchRoute`, `FixtureSlot`, `LimitLookupConfig`, `LimitStepConfig`) and the instrument-side models (`InstrumentInfo`, `CalibrationInfo`, `InstrumentRecord`, `InstrumentAssetFile`) are missing. The truncated `MeasurementFunction` enum is the single biggest contributor. |
| `docs/reference/event-types.md` | 31 event classes | 7 (all MENTIONED-ONLY elsewhere) | The page's own opening line ("Complete reference for all Litmus event types") doesn't hold. Multi-DUT, sync, and route events are entirely absent. |
| `docs/reference/api.md` | 47 HTTP routes | 6 (MENTIONED-ONLY or UNDOCUMENTED) | Missing rows for `/runs/{run_id}/measurements`, `/runs/{run_id}/steps`, `/runs/{run_id}/steps/tree`, `/products/{product_id}/requirements`, `/stations/{station_id}/capabilities`, `/channels/_recent`. |

### Shallow-documentation hotspots

- **`docs/reference/models.md`** documents `TestRun`, `Measurement`, `TestStep`, `TestVector`, `DUT`, `StimulusRecord` with full field tables, but for `RunSummary`, `CollectedItem`, `Waveform`, `ChannelSample`, `ChannelDescriptor` they're only named. `RunSummary` is the type of `LitmusClient.list_runs()` and is what users will be reading in scripts.
- **`docs/reference/configuration.md`** documents the product / station / fixture YAML shapes but never enumerates the bottoming-out specs (`AccuracySpec`, `ResolutionSpec`, `RangeSpec`) as Pydantic models — only as YAML keys. A user reading code (not YAML) finds no defining row.
- **`docs/reference/litmus-markers.md§litmus_retry`** documents the marker but never enumerates `RetryConfig.on`'s accepted exception-name strings beyond the example.
- **`docs/integration/harness.md`** describes `step`, `run_vector`, `measure`, `prompt` but not `run_with_retry`, `run_all`, `record`, or `current_vector`/`retry_config` properties. A user looking for "run my full vector set" without writing the loop themselves won't find `run_all`.
- **`docs/reference/cli.md`** for `litmus data prune/reindex` and `litmus daemon status/restart/stop` shows the command shape with no flag table, no example output, no exit-code semantics. The whole "Data management" and "Daemon" sections are stubs.

---

## Methodology note

- Enumeration grounded in `src/litmus/` source as of 2026-05-17.
- Source files read or grepped:
  - `src/litmus/__init__.py` (top-level exports)
  - `src/litmus/pytest_plugin/__init__.py` (20 fixtures + 1 private)
  - `src/litmus/pytest_plugin/markers.py` (7 markers)
  - `src/litmus/pytest_plugin/hooks.py` (CLI options, per-role auto-fixture mechanism, env vars)
  - `src/litmus/cli.py` (CLI commands + flags)
  - `src/litmus/grafana/cli.py` (grafana CLI commands)
  - `src/litmus/mcp/server.py` (12 MCP tools + 1 prompt)
  - `src/litmus/api/app.py` (47 HTTP routes)
  - `src/litmus/client.py` (`LitmusClient` + 3 builder classes)
  - `src/litmus/connect.py` (`connect()` + `StationConnection`)
  - `src/litmus/execution/harness.py` (`TestHarness` + `Context` + `LimitsView`)
  - `src/litmus/expand.py` (5 range expanders)
  - `src/litmus/models/*.py` (all Pydantic models + enums)
  - `src/litmus/data/models.py` (runtime models + `Outcome`)
  - `src/litmus/data/events.py` (31 event classes)
  - `src/litmus/data/schemas.py` (`RUN_ROW_SCHEMA`, 64 columns)
  - `src/litmus/data/channels/models.py` (`ChannelDescriptor`, `ChannelSample`)
  - All files under `src/litmus/` grepped for `os.environ` references.
- Inventory comparison: bootstrapped from `.tmp/public-surface-inventory.md`, every surface re-verified. The inventory matched current source on every spot-check (fixture count 20, marker count 7, MCP tool count 12, event class count 31, expanders count 5, top-level exports 6).
- Bucketing rule: a symbol counts as DEFINED only when the doc has a section / table row / dedicated paragraph naming it AND showing field types / parameters / a usage example. SHALLOW = named with explanation but missing example or field types. MENTIONED-ONLY = appears once in prose. UNDOCUMENTED = zero hits.
