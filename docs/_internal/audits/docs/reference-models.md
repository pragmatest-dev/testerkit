# Page audit: docs/reference/models.md

**Quadrant:** Reference (Pydantic model index + ERD)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 3 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 3 |
| Accuracy | 9 | 11 | 6 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 0 | 4 | 6 |
| **Total** | **11** | **25** | **23** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L502-541 ("Type vs Instance Models") | The ASCII diagram introduces a model name `(Fixture Instance)` that is not a real model — it's a conceptual instance — but the page hasn't yet flagged this distinction in prose. A reader scanning the ERD first will look for a `FixtureInstance` class. |
| WARNING | L862-940 ("Context (Execution Module)" and "RunContext (Legacy)") | The page is titled "Pydantic Models" / ERD, and the ERD at the top makes no mention of `Context` or `RunContext`. These execution classes appear at the end without being foreshadowed. The page's scope drifts from "Pydantic models" to "Pydantic models + execution context API." |
| SUGGESTION | L485-501 ("Module Organization" table) | The module table appears after the ERD. For a reference page, putting the at-a-glance module table BEFORE the wall-of-mermaid would let readers orient themselves first. |
| SUGGESTION | L627-794 ("Data Models Field Reference") | The field reference is ordered Outcome → Measurement → TestVector → StimulusRecord → TestStep → DUT → TestRun. The hierarchy in the rest of the page is TestRun → TestStep → TestVector → Measurement (top-down). Reverse the field reference to match. |
| SUGGESTION | L924-940 ("RunContext (Legacy)") | The label "(Legacy)" suggests deprecation but the prose contradicts this ("provides RunContext-compatible API for custom metadata"). Either explain what is legacy about it or drop the label. Ordering issue: the deprecation header lands before the reader knows what is current. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L924 | Inconsistent person | "The `RunContext` class provides RunContext-compatible API for custom metadata" — self-referential ("RunContext provides RunContext-compatible API"). Reads like a placeholder. |
| SUGGESTION | L631 | Hedging / vague | "Test outcome per ATML/IEEE 1671 terminology." — fine, but the rest of the section drops to passive voice in places. |
| SUGGESTION | L862-870 | Throat-clearing | "The `Context` class provides hierarchical context with scoped inheritance:" — leads with what the class is rather than what the reader does with it. For a reference, prefer "Use `Context` to set values that flow from run → step → vector." |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L502-541 ("Type vs Instance Models" ASCII diagram) | Anti-audience content / programmer-pattern framing | The "TYPES vs INSTANCES" framing borrows OO-pattern language. Test engineers think in "spec vs run," "what we tested vs what we got." Reword to "Spec models (what we plan to test)" and "Result models (what happened)." |
| WARNING | L796-860 ("JSON Example") | Wrong vocabulary | The example shows `"station_id": "bench_001"` but Litmus operator UI convention is `station_hostname`. The example perpetuates the `station_id` identifier that operator-facing prose should avoid. |
| SUGGESTION | L862 | Programmer jargon | "hierarchical context with scoped inheritance" — "scoped inheritance" is OO jargon. Test engineers read this as "values flow from run → step → vector," which is exactly what the bullets below already say. Lead with the bullets. |
| SUGGESTION | L122 (`Product` ERD row) | Wrong vocabulary | ERD shows `Product { id string PK, ... part_number string, ... }` — the operator-facing column is `dut_part_number` (per project memory). The ERD could note the operator label. |
| SUGGESTION | L862-940 | Audience drift | The "Context" and "RunContext" sections are written for a test-author audience (someone writing a test). The rest of the page is a model index for a system-design audience. Either split into two pages or signal the audience shift. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L175 (StationConfig ERD) | `station_hostname string` field on `StationConfig` | Actual field name is `hostname: str | None` — there is no `station_hostname` on this model. (The `TestRun` model has `station_hostname`, which may be the source of confusion.) | `src/litmus/models/station.py:66` |
| CRITICAL | L500 (Module Organization table) | `src/litmus/models/instrument.py | Instrument runtime config | InstrumentConfig` | Module contains `ChannelKind, InstrumentInfo, CalibrationInfo, InstrumentRecord`. `InstrumentConfig` is in `station.py`, not `instrument.py`. | `src/litmus/models/instrument.py:1-149` |
| CRITICAL | L496 (Module Organization table) | `src/litmus/models/instrument_asset.py | Calibration / asset records | InstrumentAsset` | Actual class name is `InstrumentAssetFile`, not `InstrumentAsset`. | `src/litmus/models/instrument_asset.py:14` |
| CRITICAL | L765-792 (TestRun field table) | `environment_json | dict[str, Any]` | Code: `environment_json: str | None = None` — a JSON-encoded string, not a dict. | `src/litmus/data/models.py:449` |
| CRITICAL | L780 (TestRun) | `session_inputs | dict[str, Any] | Required-input values` | Code: `session_inputs: dict[str, str]` — strictly string values. | `src/litmus/data/models.py:426` |
| CRITICAL | L767 (TestRun) | `session_id | UUID | None | Session this run belongs to` | Code: `session_id: UUID = Field(default_factory=uuid4)` — always populated, never `None`. | `src/litmus/data/models.py:391` |
| CRITICAL | L646-666 (Measurement field table) | Lists 15 fields | Missing two fields: `step_path: str = ""` and `characteristic_id: str | None = None`. Both are documented columns in the parquet schema. | `src/litmus/data/models.py:170-191`, `src/litmus/data/schemas.py:116` |
| CRITICAL | L273-280 (Limit ERD row) | `Limit { low, high, nominal, units, spec_ref, comparator }` | Code adds `characteristic_id: str | None` (the structured-traceability counterpart to `spec_ref`). | `src/litmus/models/test_config.py:253` |
| CRITICAL | L460-466 (Relationships block) | `SidecarConfig ||--o{ Limit : "limits:"` | `SidecarConfig.limits` is `dict[str, MeasurementLimitConfig]`, not `dict[str, Limit]`. The `Limit` model is what the resolver produces; the YAML carries `MeasurementLimitConfig`. The ERD is missing the `MeasurementLimitConfig` node entirely. | `src/litmus/models/test_config.py:159, 579-708` |
| WARNING | L65 (Signal ERD row) | `bands list` | Code: `bands: list[SpecBand] | None = None` (nullable, not just a list). Same caveat applies to Condition / Control / Attribute. | `src/litmus/models/capability.py:247, 275, 309, 349` |
| WARNING | L84-87 (Attribute ERD row) | `Attribute { value string, units string }` | Missing fields: `range, options, bands, qualifier`. `value` is `float | str | bool | None`, not just `string`. | `src/litmus/models/capability.py:345-350` |
| WARNING | L100-108 (InstrumentCatalogEntry) | `{ id, manufacturer, model, name, type, channels, capabilities }` | Missing fields: `description, base, scaffold, driver, interfaces, form_factor, attributes`. | `src/litmus/models/catalog.py:58-71` |
| WARNING | L110-115 (ChannelTopology) | `{ label, terminals, connector, ground }` | Missing fields: `connector_pin: dict[str, int | str] | None`, `optional: bool`. | `src/litmus/models/capability.py:163-168` |
| WARNING | L121-129 (Product ERD row) | `{ id, name, part_number, base, description, revision, datasheet }` | Missing fields: `schematic, driver, pins, signal_groups, characteristics`. ERD shorthand is acceptable but the absent dict fields are the meaningful ones for the relationships drawn. | `src/litmus/models/product.py:252-267` |
| WARNING | L151-164 (ProductCharacteristic ERD) | `{ function, direction, units, pin, net, signal_group, datasheet_ref, signals, conditions, controls, attributes, bands }` | Missing the `pins: str | list[str]` field (multi-pin/range selector). Also missing `resolved_pins` computed field. | `src/litmus/models/product.py:153-176` |
| WARNING | L170-178 (StationConfig ERD) | `{ id, name, station_type, location, station_hostname, instruments, supported_phases }` | Missing `description`; renames `hostname` → `station_hostname` (see CRITICAL above). | `src/litmus/models/station.py:55-70` |
| WARNING | L202-208 (FixtureConfig ERD) | `{ id, name, product_id, product_family, product_revision }` | Missing: `station_types, dut_resource, connections, slots, description`. Notable that `slots` and `connections` define the entire model's purpose. | `src/litmus/models/test_config.py:472-494` |
| WARNING | L210-217 (FixtureConnection ERD) | `{ name, dut_pin, net, instrument, instrument_channel, instrument_terminal }` | Missing: `description, function, route`. `function` and `route` are documented elsewhere as load-bearing. | `src/litmus/models/test_config.py:404-424` |
| WARNING | L265-271 (PromptConfig ERD) | `{ id string PK, message, prompt_type, choices, timeout_seconds }` | There is no `id` field on `PromptConfig`. The dict key in `prompts: dict[str, PromptConfig]` is the id; the model itself doesn't carry one. | `src/litmus/models/test_config.py:519-533` |
| WARNING | L402-409 (Dialog ERD) | `Dialog { id, type, title, message, run_id, timeout_seconds }` plus `timeout_seconds int` | Missing: `step_name`, `blocking`. `timeout_seconds` is `float | None`, not `int`. | `src/litmus/api/dialogs/models.py:18-28` |
| WARNING | L411-418 (DialogResponse ERD) | `{ dialog_id, confirmed, choice, value, timed_out, cancelled }` | Missing `choices: list[int] | None` (multi-select) and `image_data: str | None`. `choice` is `int | None`, not `int`. | `src/litmus/api/dialogs/models.py:69-79` |
| SUGGESTION | L20-36 (MeasurementFunction enum) | Lists 14 specific values plus `etc` | Real enum has ~70 values across DC/AC/RF/optical/thermal/etc. The "etc" is an honest signpost but readers cannot tell from the ERD which functions are supported. | `src/litmus/models/enums.py:24-152` |
| SUGGESTION | L489 (Module Organization) | `enums.py | Direction, MeasurementFunction, Comparator, MatchDepth, TerminalRole` | Missing significant enums: `WaveformShape, GroundTopology, ConnectorType, InstrumentType, SpecQualifier, ConditionKey, PinRole, ChannelKind, DialogType`. | `src/litmus/models/enums.py`, `capability.py`, `product.py`, `instrument.py`, `dialogs/models.py` |
| SUGGESTION | L498 (Module Organization) | `test_config.py | ... SidecarConfig, TestEntry, ..., Limit, FixtureConfig, FixtureSlot, FixtureConnection, SwitchRoute, PromptConfig` | Missing `MeasurementLimitConfig`, `LimitLookupConfig`, `LimitStepConfig` — these are the user-facing limit-config models. | `src/litmus/models/test_config.py:519-708` |
| SUGGESTION | L497 (Module Organization) | `project.py | Project-level config | ProjectConfig, ProfileConfig` | Missing `MultiSlotConfig`. | `src/litmus/models/project.py:52-66` |
| SUGGESTION | L493 (Module Organization) | `product.py | Product specifications | Product, Pin, ProductCharacteristic, SignalGroup` | Missing `PinRole` enum and `BusSignal` model. | `src/litmus/models/product.py:29-118` |
| SUGGESTION | L398-418 (Dialogs ERD) | Only shows `Dialog` and `DialogResponse` | The actual module has typed subclasses `ConfirmDialog, ChoiceDialog, InputDialog, ImageDialog` that the page never mentions. | `src/litmus/api/dialogs/models.py:31-66` |
| SUGGESTION | L796-860 (JSON example) | `"started_at": "2025-01-31T12:00:00Z"` | Trivial dating drift; the project clock is 2026. Cosmetic. | — |
| VERIFIED | — | 44 claims verified against source | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | "Module Organization" table (L487-500) | The table is the primary reference index for finding a model, but it omits about 30% of the actual models in `src/litmus/models/` (see Accuracy SUGGESTIONS). A reader who can't find their model here will assume it doesn't exist. |
| CRITICAL | Whole page | The page is titled "Pydantic Models" but never states which models are user-authored YAML schemas, which are runtime/result models, and which are internal. The reader needs this distinction to know which models they edit vs. which are produced for them. The "Type vs Instance" ASCII diagram gestures at this but it's not authoritative and excludes most models. |
| WARNING | L646-666 (Measurement) | The "Parquet Column" column appears only on the Measurement table. Readers viewing other tables (TestStep, TestVector, StimulusRecord) will wonder where the parquet column information is for those models. Link or state that only Measurement-row fields land in the parquet measurement rows; the others are derived/joined. |
| WARNING | L502-541 ("Type vs Instance Models") | The diagram lists 4 type+4 instance pairs but doesn't explain the relationships: how does a `StationType` produce a `StationConfig` (Station Instance)? How is a "Fixture Instance" created? Reader cannot get to the verb. |
| WARNING | L543-600 ("Data Flow" diagram) | The diagram shows the spec→runtime flow but doesn't mention: how is a TestRun started? Who writes it? Where does the SidecarConfig get *parsed* in this flow? The reader knows what models exist but not how they're connected. |
| WARNING | "Module Organization" (L487) | Each row lists the module but doesn't link to the source file or the per-model API docs. A reference page should at minimum say where to read the docstring. |
| WARNING | L626-794 ("Data Models Field Reference") | The field reference covers Outcome, Measurement, TestVector, StimulusRecord, TestStep, DUT, TestRun — i.e., only `src/litmus/data/models.py`. None of the config models (Product, Station, Fixture, SidecarConfig, Limit, MeasurementLimitConfig) get field tables. A reader looking up a YAML-authored model will not find its fields documented here. |
| SUGGESTION | L602-623 ("Capability Matching") | Shows a code-style snippet using `→` arrows but does not say which API entrypoint actually does the matching. The reader cannot find `capability_satisfies()` in the page. |
| SUGGESTION | L862-921 ("Context API") | Doesn't state which methods are read-only and which are write. `params`/`observations` are properties (read), but `configure`/`observe` are mutators. Not flagged as such. |
| SUGGESTION | L796-860 (JSON example) | One JSON example for `TestRun`. None for any other model. A reference page benefits from a YAML/JSON example per primary user-facing model (Product, StationConfig, FixtureConfig, SidecarConfig, MeasurementLimitConfig). |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| WARNING | Whole page | No "See also" / "Next steps" section. For a reference page this dense, the absence of related-page pointers is a gap. |
| WARNING | L631 (Outcome section) | First use of "Outcome" as a domain term with no link to `docs/concepts/outcomes.md`, which explains the cascade ladder in prose. |
| WARNING | L646-666 (Measurement section, "Parquet Column" column) | First reference to parquet columns; no link to `docs/reference/parquet-schema.md` which is the authoritative parquet schema reference. |
| WARNING | L601-622 ("Capability Matching") | Mentions `capability_satisfies()` (implicitly) and direction pairing — no link to where this is documented. The only earlier link (L3) is to the concept page, but the matching algorithm itself has no doc page anchor in this section. |
| SUGGESTION | L122 (Product ERD) | First use of `Product` — no link to `docs/concepts/products.md`. |
| SUGGESTION | L170 (StationConfig ERD) | First use of `StationConfig` — no link to `docs/concepts/stations.md`. |
| SUGGESTION | L202 (FixtureConfig ERD) | First use of `FixtureConfig` — no link to `docs/concepts/fixtures.md`. |
| SUGGESTION | L226-248 (SidecarConfig, TestEntry) | First use of "sidecar YAML" — no link to `docs/reference/configuration.md` or `docs/reference/litmus-markers.md` (which documents how marker fields map to YAML). |
| SUGGESTION | L286 (TestRun ERD) | First use of `TestRun` — could link to `docs/concepts/results-storage.md` and `docs/reference/outputs.md`. |
| SUGGESTION | L862-922 (Context section) | References `LimitsView`, `ProductContext`, `StationConfig` properties on `ctx.run / ctx.product / ctx.station` — no links to where these are documented. Also references `litmus_characteristics` marker — should link to `docs/reference/litmus-markers.md#litmus_characteristics`. |
