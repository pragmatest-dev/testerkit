**Summary tally — post H.fix.6:** ~119 undocumented surfaces → ~30 remaining. Generators closed the gap on all per-model field tables, every event class, every `litmus` CLI command, the full `MeasurementFunction` enum, and 5 of 9 shared enums. Real gaps remain in pytest-plugin flags, env vars, the Query API, MCP prompts, and a handful of harness/builder methods.

# Coverage audit (post Phase H.fix.6)
**Date:** 2026-05-17
**Scope:** Whole `docs/` corpus excluding `_internal/`
**Direction:** Source → docs (delta from `audit-coverage.md`)
**Generator inputs verified in sync:** `uv run python scripts/generate_reference_docs.py --all --check` exits clean.

The prior audit flagged ~119 undocumented surfaces. The new generators in `scripts/generate_reference_docs.py` plus the marker-bracketed sections in `event-types.md`, `models.md`, `configuration.md`, `api.md`, and `cli.md` now cover the bulk of them. What follows is **only what a test engineer would still fail to find** on the current corpus.

---

## 1. Pytest-plugin CLI flags — biggest remaining hole

The `litmus` Click tree is now fully introspected in `cli.md`, but **pytest plugin options** registered via `pytest_addoption` in `src/litmus/pytest_plugin/hooks.py:896-1018` are NOT in any generated table. `docs/reference/pytest-native.md` has a hand-curated 8-row table that lists less than half of them.

Missing from every public page:

| Flag | Source | What's missing |
|---|---|---|
| `--dut-serial` | `hooks.py:900` | mentioned in tutorials only; not in plugin-flags table |
| `--dut-serials` | `hooks.py:901-905` | how-to/multi-dut-testing.md only |
| `--slot` | `hooks.py:906-914` | undocumented anywhere |
| `--dut-revision` | `hooks.py:916` | undocumented anywhere |
| `--dut-lot-number` | `hooks.py:917-921` | undocumented anywhere |
| `--operator` | `hooks.py:932` | one occurrence in tutorial; not in any flag table |
| `--no-mock-instruments` | `hooks.py:955-962` | undocumented anywhere |
| `--test-phase` | `hooks.py:973-978` | discussed prose-only in cli.md *Test phase*; not in plugin-flag table |
| `--strict-traceability` | `hooks.py:980-985` | gating flag for production traceability — **0 hits in docs** |
| `--no-test-profile` | `hooks.py:993-998` | listed parenthetically in pytest-native.md; no row |
| Dynamic `--<facet>` flags | `hooks.py:999-1007` (`facet_key_to_cli_flag`) | mechanism not documented; profiles.md doesn't mention CLI surfacing |
| Dynamic `--<required-input>` flags | `hooks.py:1008-1018` (`required_input_key_to_cli_flag`) | mechanism not documented |

**Recommendation:** the CLI generator (`_generate_cli`) walks `litmus.cli:main` — the Click tree. Extend with a second pass that imports `litmus.pytest_plugin.hooks.pytest_addoption`, instantiates a stub `parser`, and renders the option group as a new generated table in `docs/reference/pytest-native.md` (or a new section in `cli.md`). The dynamic facet/required-input flags can't be table-rendered (they depend on `litmus.yaml`), so a *mechanism* paragraph in `how-to/profiles.md` is the right shape.

---

## 2. Environment variables — only 4 of 12 public vars listed

`docs/reference/cli.md` lines 514-522 lists `LITMUS_HOME`, `LITMUS_TEST_PHASE`, `LITMUS_MOCK_INSTRUMENTS`, `LITMUS_SKIP_DAEMON_NOTIFY`. Source grep finds 8 more public vars:

| Variable | Source | Effect | Recommended home |
|---|---|---|---|
| `LITMUS_AUTO_CONFIRM` | `prompts/core.py:30,69` | Auto-resolves dialogs/prompts in non-tty contexts | cli.md env-vars table |
| `LITMUS_SERVER_URL` | grep target file (used by client) | Overrides server URL for subprocess→daemon hop | cli.md env-vars table |
| `LITMUS_TEST_PROFILE` | `hooks.py:988` | Default for `--test-profile` | cli.md env-vars table |
| `LITMUS_DAEMON_IDLE_TIMEOUT` | daemon source | Seconds before daemon self-shuts-down | cli.md env-vars table |
| `LITMUS_DAEMON_SPAWN_TIMEOUT` | daemon source | Seconds to wait for daemon ready | cli.md env-vars table |
| `LITMUS_DUT_SERIAL` | hooks.py | Default for `--dut-serial` (only in how-to/multi-dut-testing.md) | cli.md env-vars table |
| `LITMUS_DUT_PART_NUMBER` | hooks.py | Default for `--dut-part-number` | cli.md env-vars table |
| `LITMUS_DUT_REVISION` | hooks.py | Default for `--dut-revision` | cli.md env-vars table |
| `LITMUS_DUT_LOT_NUMBER` | `hooks.py:920` | Default for `--dut-lot-number` | cli.md env-vars table |
| `LITMUS_FIXTURE_SLOT` | hooks (multi-dut) | JSON-serialized slot config | cli.md env-vars table (cross-link multi-dut) |

**Recommendation:** the env-vars table in `cli.md` is hand-written. Either (a) grep-generate it from source, or (b) add the missing rows by hand and add a pre-commit grep guard.

---

## 3. Public Query API — undocumented

CLAUDE.md mandates that operator-facing pages read through `RunsQuery`, `StepsQuery`, `MeasurementsQuery`. All three are public, importable, and 0 hits in `docs/`:

| Class | Source | Status |
|---|---|---|
| `RunsQuery` | `src/litmus/analysis/runs_query.py` | UNDOCUMENTED |
| `StepsQuery` | `src/litmus/analysis/steps_query.py` | UNDOCUMENTED |
| `MeasurementsQuery` | `src/litmus/analysis/measurements_query.py` | UNDOCUMENTED |

**Recommendation:** new page `docs/reference/query-api.md` (or section in `reference/client.md`) with one h3 per class, method tables, and a worked DuckDB-vs-Query example. The generator could be extended with a sixth target (`_generate_query_api`) walking the three classes.

---

## 4. MCP prompts — `datasheet-to-test` undocumented

`src/litmus/mcp/server.py` registers one `@mcp.prompt(name="datasheet-to-test")`. The MCP tools generator (`_generate_api`) calls `mcp.get_tools()` only; `get_prompts()` is not enumerated. Zero mentions in `docs/`.

**Recommendation:** extend `_generate_api` with a second `await mcp.get_prompts()` call and a parallel table in a new `## MCP prompts` section under the existing `## MCP tools` heading in `api.md`.

---

## 5. Harness / builder API still has shallow / undocumented members

### `TestHarness` (`src/litmus/execution/harness.py:478+`)

| Member | Source | Status |
|---|---|---|
| `record` | `harness.py:947` | SHALLOW — one mention in integration/harness.md; no key/value semantics, no example |
| `current_vector` (property) | `harness.py:594` | UNDOCUMENTED |
| `retry_config` (property) | `harness.py:599` | UNDOCUMENTED |
| `run_with_retry` | `harness.py:1154` | UNDOCUMENTED |
| `run_all` | `harness.py:1258` | UNDOCUMENTED — primary entry point for "run my full vector set", users have to roll their own loop |

### `Context` (`src/litmus/execution/harness.py:105+`)

| Member | Source | Status |
|---|---|---|
| `set_params` | `harness.py:268` | UNDOCUMENTED |
| `set_observations` | `harness.py:276` | UNDOCUMENTED |
| `measure` (on `Context`, distinct from `harness.measure`) | `harness.py:433` | UNDOCUMENTED |

### `VectorBuilder` (`src/litmus/client.py:75+`)

| Member | Source | Status |
|---|---|---|
| `VectorBuilder.fail` | `client.py:135` | UNDOCUMENTED — same signature as `StepBuilder.fail` which IS documented; users won't discover the vector-scoped variant |
| `VectorBuilder.skip` | `client.py:141` | SHALLOW — referenced inside a vector code block, no method-table row |

**Recommendation:** extend `docs/integration/harness.md` with rows for the 5 undocumented `TestHarness` members and the 3 `Context` members; add a `VectorBuilder` methods table to `docs/reference/client.md` mirroring the existing `StepBuilder` shape.

---

## 6. Mid-tier Pydantic models not in the generator's scope

`_generate_models` walks 10 modules — `models/*.py` plus `data/models.py`. Models living elsewhere that users reach via `from litmus.X import Y`:

| Class | Source | Status | Recommended home |
|---|---|---|---|
| `ChannelDescriptor` | `data/channels/models.py:19` | UNDOCUMENTED | extend `_MODELS_MODULES` with `litmus.data.channels.models` |
| `ChannelSample` | `data/channels/models.py:31` | UNDOCUMENTED | same |
| `EnvironmentSnapshot` | `environment.py` | MENTIONED-ONLY | parquet-schema.md shows construction, no field table |
| `ResolvedSlot` | `execution/slots.py` | UNDOCUMENTED | recommend skipping (multi-slot internals) or adding to models.md |
| `MeasurementRow` | `data/backends/_row_helpers.py` | UNDOCUMENTED | internal helper — probably skip; flag if `_row_helpers` becomes public |
| `FacetSpec`, `FacetOption`, `SummaryCounts`, `ParametricRow`, `HistogramRow`, `FilterSet` | `analysis/measurement_facets.py` | UNDOCUMENTED | part of the Query API (item 3); document alongside |
| `RunRow`, `StepRow`, `StepNode` | `analysis/runs_query.py`, `analysis/steps_query.py` | UNDOCUMENTED | same |
| `LaunchRequest`, `RunStatus`, `ActiveRun`, `DialogCreate`, `DialogRespondRequest`, `SaveRequest` | `api/models.py` | API request shapes — referenced as response_model in `api.md` table but no field tables | extend `_MODELS_MODULES` with `litmus.api.models` and `litmus.api.responses`, render under a new "HTTP API request / response shapes" heading |
| `MatchSingleResponse`, `MatchAllResponse`, all `*Response` classes (~16 total) | `api/responses.py` | same as above | same |

**Recommendation:** add three entries to `_MODELS_MODULES` (`litmus.data.channels.models`, `litmus.api.models`, `litmus.api.responses`). The analysis facets / row models are part of the Query API gap in item 3 and should land together.

---

## 7. Other public surfaces still missing

| Surface | Source | Status | Recommended home |
|---|---|---|---|
| `RouteManager` class + 6 public methods (`activate`, `deactivate`, `deactivate_all`, `for_pin`, `has_routes`, `active_routes`) | `instruments/route_manager.py:36+` | `routes` fixture yields this object — mentioned by name in litmus-fixtures.md, never defined | new `docs/reference/route-manager.md` or section in `docs/concepts/fixtures.md` |
| `ChannelStore` public methods (`open`, `list_channel_info`, `get_channel_schema`, `write`, `on_channel`, `query`, `flight_location`, `close`) | `data/channels/store.py` | name appears in `concepts/three-stores.md` and `how-to/querying-channels.md`; no method-level reference | extend `docs/how-to/querying-channels.md` with a methods table, or new `docs/reference/channel-store.md` |
| `EventStore` / `RunStore` / `EventLog` public methods | `data/event_store.py`, `data/run_store.py`, `data/event_log.py` | named in `concepts/three-stores.md` only; no method tables | section in `docs/concepts/three-stores.md` or a new `docs/reference/stores.md` |
| `escalate_outcome` free function | `data/models.py:140-167` | now MENTIONED in `concepts/outcomes.md` (one paragraph, no signature) | extend `concepts/outcomes.md` with a signature block |
| `validate_station_against_type` free function | `models/station.py` | MENTIONED in `configuration.md` (one paragraph) | acceptable as MENTIONED for a validator; promote to DEFINED if used outside session startup |
| `band_matches` free function | `models/capability.py` | MENTIONED in `catalog-schema.md` (one paragraph) | same — acceptable |

---

## 8. Items from the prior audit that *are* now resolved

For the record, the post-H.fix.6 generators closed:

- All 31 event classes (was: 7 MENTIONED-ONLY) — full per-class field tables.
- Event category constants (`SESSION_EVENTS` etc., `ALL_EVENTS`) — added to event-types.md prose.
- Every Pydantic model in `models/*.py` and `data/models.py` (was: ~22 UNDOCUMENTED/SHALLOW) — full field tables with types and defaults.
- `MeasurementFunction` enum — all 67 values now listed (was: 53 undocumented).
- `WaveformShape`, `TerminalRole`, `ConnectorType`, `MatchDepth`, `InstrumentType`, `SpecQualifier`, `ConditionKey`, `WorkflowStep`, `ChannelKind` enums — all enumerated.
- Every `litmus` Click command including `validate`, `sbom`, `discover`, `catalog datasheet`, `instrument {list,show,cal}`, `schema {export,refresh}`, `station {init,validate,update}`, `new-test`, `metrics retest`, `data {prune,reindex}`, `daemon {status,restart,stop}` — full option tables.
- All MCP tools — generated parameter list + summary.
- All HTTP routes including `/runs/{run_id}/measurements`, `/runs/{run_id}/steps`, `/runs/{run_id}/steps/tree`, `/products/{product_id}/requirements`, `/stations/{station_id}/capabilities`, `/channels/_recent` — grouped tables with response_model.
- Configuration file → model index — generated, links to `models.md` anchors.
- `Limit` is now re-exported at the top level (`from litmus import Limit`) — closes the import-depth gripe.

---

## Methodology

- Re-grep'd `src/litmus/` for every surface category from `audit-coverage.md`.
- Verified each "still missing" item by `grep -rln <symbol> docs/ | grep -v _internal`.
- Ran the generator in `--check` mode — clean, so generated sections faithfully reflect source.
- Bucketing rule unchanged from prior audit: DEFINED requires a named section/table-row with field types or signature; SHALLOW = named with prose but no example or field types; UNDOCUMENTED = zero hits.

## Top-5 by user reach (recommended fix order)

1. **Pytest plugin flags** (`--strict-traceability`, `--slot`, `--operator`, `--dut-revision`, `--dut-lot-number`, dynamic facet flags) — every production-tier user hits these.
2. **Environment variables** (`LITMUS_AUTO_CONFIRM`, the 4 DUT vars, the 2 daemon vars, `LITMUS_SERVER_URL`) — operators tuning the box won't find them.
3. **Query API** (`RunsQuery` / `StepsQuery` / `MeasurementsQuery`) — explicitly the public path per CLAUDE.md, zero docs.
4. **`TestHarness.run_all` / `run_with_retry` / `record`** — primary entry points for non-trivial harness use.
5. **MCP `datasheet-to-test` prompt** — only prompt registered, advertised nowhere.
