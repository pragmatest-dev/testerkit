# Coverage audit — H.fix.9 verify gate
**Date:** 2026-05-17
**Scope:** Whole `docs/` corpus excluding `_internal/`
**Direction:** Source → docs (delta from `audit-coverage-post-h6.md`)
**Bar:** ≤10 undocumented public surfaces

---

## Headline

**Result: 7 undocumented + 4 shallow remaining. Bar (≤10 undocumented) PASS.**

H.fix.7 closed every item in §1–§6 of the post-H6 audit (plugin flags, env vars, MCP prompts, mid-tier Pydantic models, Query API, harness/builder methods). H.fix.8.5 confirmed: cold first-use links for `verify` / `context` / `logger` / `Limit` / `SpecBand` / `Measurement` are present in `concepts/platform-architecture.md`, `concepts/step-hierarchy.md`, `concepts/architecture.md`.

The residual set is the §7 / §8 leftover the post-H6 audit explicitly deferred — store-class method tables and `RouteManager`. None of these blocks the gate.

---

## H.fix.7 deliverables — verified closed

Spot-grep against current `docs/` for each thing the post-H6 audit flagged. All resolved.

| Post-H6 §  | Item | Verified at |
|---|---|---|
| §1 plugin flags | `--strict-traceability`, `--slot`, `--operator`, `--dut-revision`, `--dut-lot-number`, `--no-mock-instruments`, `--no-test-profile`, dynamic facet/required-input prose | `reference/pytest-native.md:85–104` — 17-row table + dynamic-flags paragraph |
| §2 env vars | `LITMUS_TEST_PROFILE`, `LITMUS_AUTO_CONFIRM`, `LITMUS_SERVER_URL`, `LITMUS_DUT_SERIAL` (+ `_<SLOT_ID>`), `LITMUS_DUT_PART_NUMBER`, `LITMUS_DUT_REVISION`, `LITMUS_DUT_LOT_NUMBER`, `LITMUS_FIXTURE_SLOT`, `LITMUS_DAEMON_IDLE_TIMEOUT`, `LITMUS_DAEMON_SPAWN_TIMEOUT` | `reference/cli.md:518–531` — all 14 vars now in the table |
| §3 Query API | `RunsQuery`, `StepsQuery`, `MeasurementsQuery` with full method tables | `reference/query-api.md` — one h2 per class, 9 + 6 + 12 method anchors |
| §4 MCP prompts | `datasheet-to-test` | `reference/api.md:63–70` — dedicated `## MCP prompts` section |
| §5 harness/builder | `TestHarness.run_all`, `run_with_retry`, `record`, `current_vector`, `retry_config`; `Context.set_params`, `set_observations`, `measure`; `VectorBuilder.fail`, `skip` | `integration/harness.md:86–94, 168–181`; `reference/client.md:128–148` |
| §6 mid-tier models | `RunRow`, `StepRow`, `StepNode`, `FacetSpec`, `FacetOption`, `SummaryCounts`, `ParametricRow`, `HistogramRow`, `FilterSet`, `ChannelDescriptor`, `ChannelSample`, `LaunchRequest`, `RunStatus`, `ActiveRun`, `DialogCreate`, `DialogRespondRequest`, `SaveRequest`, all `*Response` classes | `reference/models.md:1152–1543` — full field tables with anchors |

Generator check parity: `_MODELS_MODULES` now spans the 5 added modules; `_generate_api` emits both tools and prompts; `_generate_query_api` is wired.

---

## Residual undocumented surfaces (7)

These are public symbols a test engineer can reach via `from litmus...` import that still have **zero** mention in `docs/` outside their bare class name.

| # | Symbol | Source | Recommended home |
|---|---|---|---|
| 1 | `RouteManager.activate(connection_name)` | `src/litmus/instruments/route_manager.py:95` | new section in `reference/litmus-fixtures.md` under `routes` fixture, or a new `reference/route-manager.md` page |
| 2 | `RouteManager.deactivate(connection_name)` | `route_manager.py:147` | same as above |
| 3 | `RouteManager.has_routes` (property) | `route_manager.py:86` | same |
| 4 | `RouteManager.active_routes` (property) | `route_manager.py:91` | same |
| 5 | `RouteConflictError` exception | `route_manager.py:30` | same |
| 6 | `EnvironmentSnapshot` model fields | `src/litmus/environment.py` | `reference/parquet-schema.md` already imports it at line 330; add a field table |
| 7 | `DataUnavailable` exception (`RunStore`) | `src/litmus/data/run_store.py` | mention in `concepts/three-stores.md` or `reference/client.md` |

`RouteManager.deactivate_all` and `for_pin` are documented at example level inside `litmus-fixtures.md:141–145` (the `routes` fixture block names `routes.for_pin(...)` and `routes.deactivate_all()` with effects), so they count as SHALLOW rather than UNDOCUMENTED. The five members above are not named anywhere in public docs.

`ResolvedSlot` is mentioned in `cli.md:528` (in the `LITMUS_FIXTURE_SLOT` description) as the JSON shape, but with no field list — treating that as MENTIONED-ONLY but not flagging because the post-H6 audit explicitly recommended skipping it as multi-slot internal.

---

## Shallow surfaces (4)

Named in docs with prose or example, but no method-signature table.

| # | Symbol set | Source | Where named | What's missing |
|---|---|---|---|---|
| 1 | `EventStore` public methods (`get_shared`, `get_event_log`, `emit`, `flush`, `events`, `sessions`, `events_for_unmaterialized_runs`, `on_event`, `events_dir`, `close`) | `src/litmus/data/event_store.py` | `how-to/querying-events.md:42–66` shows `.events(...)`, `.sessions()`, `.close()` by example; `concepts/event-log.md:154` introduces the class | no signature table; remaining 7 methods invisible |
| 2 | `EventLog` public methods (`emit`, `flush`, `add_subscriber`, `save_ref`, `events`, `close`, `path`) | `src/litmus/data/event_log.py` | `concepts/event-log.md:129–135` describes `emit()` in prose | no method table; `add_subscriber`, `save_ref`, `path` not named |
| 3 | `RunStore` public methods (`list_runs`, `find_run_file`, `get_run`, `get_measurements`, `get_steps`, `find_channel_refs`, `get_measurement`, `rewrite_refs`, `ref_dir_for`, `notify_new_run`, `close`) | `src/litmus/data/run_store.py` | `concepts/step-manifest.md:101–106` shows `RunStore().get_steps(...)` only | 10 of 11 methods invisible; users default to `LitmusClient` which is good, but `RunStore` is the deeper path |
| 4 | `ChannelStore` public methods (`open`, `list_channel_info`, `get_channel_schema`, `write`, `on_channel`, `query`, `flight_location`, `close`, `list_channel_refs`) | `src/litmus/data/channels/store.py` | `how-to/querying-channels.md:39–73` shows `.query(...)` with params | 8 of 9 methods invisible |

These four classes are the same gap the post-H6 audit deferred under §7. The bar permits this; they are reachable but the user-facing primary path is through `LitmusClient` / `connect()` / `RunsQuery` / `StepsQuery` / `MeasurementsQuery`, all of which are fully documented now.

---

## Verified-still-acceptable MENTIONED-ONLY surfaces

These are public free functions / helpers whose post-H6 status was MENTIONED-ONLY. The audit recommended leaving them as-is unless they grow caller-base. Re-checked: still MENTIONED-ONLY, still acceptable.

| Symbol | Where named | Bucket |
|---|---|---|
| `escalate_outcome(current, incoming)` | `concepts/outcomes.md:26` — paragraph names it with source line; no signature block | MENTIONED-ONLY (acceptable; severity ladder is the prose; signature is one line away in source) |
| `validate_station_against_type(station, station_type)` | `reference/configuration.md:143` | MENTIONED-ONLY (acceptable; session-startup validator, not a user-callable API) |
| `band_matches()` | `reference/catalog-schema.md:242` | MENTIONED-ONLY (acceptable; called by spec resolution, not by test code) |
| Channel helpers `SCALAR_SCHEMA`, `ARRAY_SCHEMA`, `encode_value`, `sample_schema`, `sample_to_batch`, `batch_row_to_sample` | 0 hits | INTERNAL helpers; tests/UI never reach them; skip |
| `ParquetBackend.reconstruct_test_run`, `save_from_rows`, `get_vectors`, `get_run_metadata` | 0 hits | reachable but `LitmusClient.get_measurements` / `get_run` is the user-facing wrapper; the raw backend methods are not in the "what a test engineer would fail to find" set |

---

## Methodology

- Re-read `.tmp/public-surface-inventory.md` as the canonical enumeration (last updated 2026-05-16).
- For each H.fix.7 item, ran a targeted `grep -rE` against `docs/` excluding `_internal/` to confirm the documented surface exists and has a defining table-row / signature block.
- For each residual category named in `audit-coverage-post-h6.md` §7, re-grepped to confirm status hasn't changed.
- Bucketing rule unchanged: DEFINED = named section/table-row with signature or field types; SHALLOW = named with prose / example only; UNDOCUMENTED = zero hits in `docs/` excluding internal.
- No generator was re-run; this is a docs-state audit, not a regenerate-and-diff.

## Recommended follow-up (post-gate, not blocking)

1. **Method tables for the four store classes.** A new `reference/stores.md` page (or four sections under `concepts/three-stores.md`) would close all 4 shallow buckets in one PR. The generator could be extended with a sixth `_generate_stores` target walking `EventStore`, `EventLog`, `RunStore`, `ChannelStore`.
2. **`RouteManager` reference.** Two options: (a) extend the `routes` section of `reference/litmus-fixtures.md` with the 6 members + `RouteConflictError`; (b) new `reference/route-manager.md` page. Option (a) is closer to the user's mental model (they reach `RouteManager` through the fixture).
3. **`EnvironmentSnapshot` field table.** Single addition to `reference/parquet-schema.md` near the existing import example at line 330.
