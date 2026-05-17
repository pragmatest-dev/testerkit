# Meta-audit: Reference section
**Date:** 2026-05-17
**Scope:** 14 pages

## Severity totals
| Page | ❌ | ⚠️ | 💡 |
|---|---|---|---|
| litmus-fixtures | 4 | 15 | 15 |
| litmus-markers | 1 | 12 | 16 |
| pytest-native | 0 | 10 | 14 |
| configuration | 14 | 21 | 9 |
| connect | 2 | 11 | 13 |
| client | 7 | 20 | 12 |
| api | 8 | 14 | 17 |
| models | 11 | 25 | 23 |
| event-types | 16 | 16 | 11 |
| parquet-schema | 4 | 13 | 15 |
| catalog-schema | 7 | 16 | 15 |
| catalog-cookbook | 2 | 14 | 14 |
| outputs | 5 | 14 | 10 |
| cli | 13 | 26 | 14 |
| **Total** | **94** | **227** | **198** |

94 CRITICAL across the reference section. Worst pages: `event-types.md` (16), `configuration.md` (14), `cli.md` (13), `models.md` (11).

---

## Auditor accuracy check (source-verified)

### ✅ Confirmed correct

**`SpecQualifier` enum value is `"nominal"`, not `"limit_nominal"`.**
`src/litmus/models/capability.py:45-66` — `NOMINAL = "nominal"`. The `catalog-schema.md` doc names a value that doesn't exist; following it triggers Pydantic ValidationError.

**`record_type` has 3 values (`run`, `step`, `measurement`), not 2.**
`src/litmus/data/backends/parquet.py:374` emits `record_type='run'`; line 233 emits `record_type='step'`; line 944 reads `record_type=='measurement'`. The stale comment in `schemas.py:28` ("two values") matches the doc but the writer emits three. `parquet-schema.md` is wrong.

**Exporter `output_dir` is double-appended.**
`csv_exporter.py:64` — `self._output_dir = output_dir / "exports" / "csv"`. So the doc's `-o exports/csv/` produces `exports/csv/exports/csv/`. Same pattern in json_exporter, atml. `outputs.md` doc examples create broken paths.

**`--transport sse` is documented but rejected.**
`cli.py:994` declares the option in help text; `cli.py:1014` explicitly rejects anything non-stdio with "not yet supported. Use 'stdio'." `cli.md` is right to flag.

**Package name is `litmus-test`, not `litmus`.**
`pyproject.toml:6` — `name = "litmus-test"`. The `cli.md` `pip install 'litmus[pdf]'` is wrong.

**`_ref/` URI scheme is `file://_ref/...`, not bare `_ref/...`.**
`src/litmus/data/backends/_row_helpers.py:545` — `return f"file://{REF_PATH_PREFIX}{filename}"`. The comment at line 51 even says "(legacy, use file:// URIs)". `parquet-schema.md` examples using bare `_ref/...` are stale.

**`logger.measure` signature does not include `units=` kwarg.**
`src/litmus/execution/logger.py:941-949` — confirmed multiple times in earlier sections; `litmus-fixtures.md:50` repeats the same wrong signature seen in tutorial Bug A.

**`verify` signature is positional `(name, value, limit=None, characteristic=None)`, NOT keyword-only.**
`src/litmus/execution/verify.py:159-164` — `def _verify(name, value, limit=None, characteristic=None)`. No `*,` before `limit`. The `characteristic=` public kwarg is undocumented in `litmus-fixtures.md`.

---

## Cross-page patterns

### Pattern Q (verified, NEW): `record_type` has 3 values not 2
`parquet-schema.md` says 2 (`step`, `measurement`); writer emits 3 (`run`, `step`, `measurement`). The `run` row carries run-level metadata as its own discriminator-tagged row. Affects parquet-schema.md, and possibly any analytic doc that does `WHERE record_type IN (...)`.

### Pattern R (verified, NEW): file path conventions inconsistent
- `outputs.md` uses `data/runs/`
- Other docs use `results/runs/`
- Actual is `<data_dir>/runs/` where `data_dir` resolves through `resolve_data_dir()` and there's no `results/` parent
- Same pattern for `events/`, `channels/`
- This is the same drift as how-to Pattern G but worse — the reference is supposed to be canonical

### Pattern S (verified, NEW): exporter `-o` paths double-append
The exporter classes themselves append `exports/<fmt>/` to whatever `output_dir` they receive. Documenting `-o exports/csv/` produces `exports/csv/exports/csv/`. This is either a documentation bug or a CLI ergonomic bug — pick one.

### Pattern T: HTTP / MCP / CLI surface mismatches (continued from how-to)
- `api.md`: `?start=`/`?end=` instead of `?since=`/`?until=` (same as how-to G)
- `api.md`: `litmus_open` documents `project` param; actual third param is `base_url`
- `api.md`: `litmus_project` documents 3 of 7 actions and 3 of 7 params
- `api.md`: missing endpoints `/runs/{run_id}/measurements`, `/steps`, `/steps/tree`, `/channels/_recent`
- `cli.md`: omits 12+ commands (`validate`, `new-test`, `discover`, `export`, `sbom`, `schema *`, `catalog datasheet`, `station *`, `instrument *`, `grafana *`, `metrics retest`)
- `cli.md`: `daemon restart/stop` documented as no-arg; actually take TARGETS + `--all`
- `cli.md`: `--transport sse` documented as supported; rejected at runtime
- `client.md`: `submit_result` LabVIEW example function doesn't exist in `litmus.client`

### Pattern U: Wrong/missing required fields and types
- `models.md`: `StationConfig` has `hostname:`, not `station_hostname:`
- `models.md`: `InstrumentConfig` lives in `station.py`, not `instrument.py`
- `models.md`: `InstrumentAsset` is actually `InstrumentAssetFile`
- `models.md`: `TestRun.environment_json` is `str | None`, not `dict[str, Any]`
- `models.md`: `TestRun.session_inputs` is `dict[str, str]`, not `dict[str, Any]`
- `models.md`: `TestRun.session_id` defaults to `uuid4()`, never None
- `configuration.md`: 7 type mismatches (`runner:` is dict not str, `required_inputs:` is dict[str, PromptConfig] not list, `StationInstrumentConfig.channels` is dict[str,str] not list[str], `funcgen` instrument alias doesn't exist, `ProductCharacteristic.channel`/`channels`/`schematic_ref` documented but absent under `extra="forbid"`, fixture YAML missing required `name:`, catalog `base:` merge wrong)
- `event-types.md`: 16 critical — `SessionEnded.outcome`/`RunEnded.outcome` defaults misstated; 4 whole event families missing (Slot, Sync, Route, RunMaterialized); `event_type` discriminator missing from base fields table; multiple required-vs-optional reversals
- `parquet-schema.md`: filename example drops the date prefix the writer actually emits
- `litmus-fixtures.md`: 6 internal autouse fixtures (the ones that make context.get_param, verify limit chain, connections, mocks work) completely missing
- `litmus-markers.md`: Resolution order puts inline BEFORE profile, contradicting the cascade code
- `cli.md`: `setup show` example output is fabricated (lists stale tool names like `list_products` that aren't current)

### Pattern V: Phantom/missing API surfaces
- `client.md`: `submit_result` function named; doesn't exist
- `client.md`: no `VectorBuilder` API reference section while `RunBuilder` and `StepBuilder` have one
- `api.md`: zero MCP invocation examples on a page listing 12 MCP tools
- `connect.md`: `data_dir: Path | str` claim — actually only accepts `Path | None`
- `connect.md`: `event_log`/`event_store`/`channel_store` properties — never tells the reader what happens before `start()`
- `outputs.md`: never describes what's actually IN any output format (CSV columns, JSON shape, etc.) — biggest content gap for the page

### Pattern W: Duplicate API surfaces with no contract for ownership
- `client.md` ↔ `integration/results-api.md`: same API surface documented twice; guaranteed drift
- Same smell as connect.md/connect-api.md (already merged) and capability-schema/capability-examples (already renamed)

### Pattern X: Stale architecture names persist in reference
- `outputs.md:73` — `ParquetSubscriber` / `LiveRunsSubscriber` (same drift as concepts section Pattern 1)
- `configuration.md`: claims `StationType` references for fields that don't exist
- `cli.md`: `setup show` example shows old MCP tool names from before the current 12

### Pattern Y: Cookbook/schema duplication and drift (catalog pages)
- `catalog-schema.md`: `catalog_entry:` YAML wrapping is fictional — actual catalog files are flat at root
- `catalog-cookbook.md`: Recipe 2 has a working bug (`acquisition_mode: {min: 0, max: 0}` for a control with string `options:`)
- `catalog-cookbook.md`: zero links to model definitions
- Both reference `catalog_entry:` as if it were a real key

### Pattern Z: First-use cold drops persist into reference
Same pattern as tutorial, concepts, and how-to. Reference pages should be authoritative — first use should always link to its definition.

---

## Reference-section-specific concerns

The reference section is supposed to be the canonical source-of-truth. **94 CRITICAL findings here is worse than the tutorial.** When the reference is wrong, every other doc has nothing to fall back to.

Specific implications:

- **`event-types.md` is most-broken** (16 critical). Multiple event families missing from a page whose only job is to enumerate them. Required/optional reversals will trip every consumer.
- **`cli.md` claims to be exhaustive but is missing 12+ commands.** That's worse than being short — readers stop looking.
- **`models.md` field-type drift** (11 critical). Pydantic models are the contract; reference docs out of sync with `extra="forbid"` models cause silent validation failures.
- **`configuration.md` (14 critical)** is the YAML schema reference and gets fields/types wrong on every entity model.

---

## Recommended fix order

**Pre-fix sweep:** Run the audits for the Integration section, then collect all 7+ cross-cutting bugs (A–G + Q, R, S) and grep all 80+ pages for each before touching individual files.

**Cross-cutting sweeps (apply to all sections):**

1. **Bug A (`Limit` dict vs model)** — sweep all sections
2. **Bug E (`litmus_sweeps(vin=[...])` kwargs)** — sweep all sections; affects vector-expansion, possibly tutorial 03/05, litmus-markers reference
3. **Bug F (`logger.measure` kwargs)** — sweep all sections
4. **Bug G/T (HTTP query params)** — `?since=`/`?until=`; cascade through api.md, querying-channels.md
5. **Pattern R (`results/` prefix)** — drop everywhere; use `<data_dir>/`
6. **Pattern Q (`record_type` values)** — update parquet-schema.md to say 3 not 2
7. **Pattern S (exporter `-o` double-append)** — either fix docs to omit `exports/<fmt>/` or fix the CLI to not double-append

**Per-page fixes (reference section, priority order):**

1. `event-types.md` — rebuild against `src/litmus/data/events.py` source from scratch
2. `cli.md` — generate from Click introspection; never hand-write
3. `models.md` — rebuild field tables from Pydantic models programmatically
4. `configuration.md` — same; generate from model schemas
5. `api.md` — generate route table from FastAPI `app.routes`; MCP tools from `server.py` decorators
6. `client.md` — verify every documented method exists; merge with `integration/results-api.md`
7. `litmus-fixtures.md` — add the 6 autouse internals; fix `verify`/`logger.measure` signatures
8. `catalog-schema.md` — drop `catalog_entry:` wrapping; show full root-level YAML example
9. `catalog-cookbook.md` — fix recipe 2 (broken band matching); add links to model defs
10. `parquet-schema.md` — fix record_type count, file path examples, `_ref/` URI scheme
11. `outputs.md` — describe what's IN each format; fix the `data/runs/` vs canonical paths
12. `litmus-markers.md` — fix resolution order (profile after inline)

---

## Generation strategy

A pattern across `event-types`, `models`, `configuration`, `api`, `cli` is that they're all enumerative reference content that derives from source structures (Pydantic models, FastAPI routes, Click commands, event class definitions). **They should be generated, not hand-maintained.** Otherwise drift is guaranteed. Post-audit, consider:

- `litmus schema export` already exists (per `cli.md`) — confirm it produces canonical JSON-schema for models
- FastAPI auto-generates OpenAPI from routes — `api.md` should reference that, not duplicate it
- Click can introspect itself — `cli.md` could be generated from `--help` walks
- Event classes are Pydantic — could generate event-types.md from `src/litmus/data/events.py` discriminated union
