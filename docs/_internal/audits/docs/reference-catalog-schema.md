# Page audit: docs/reference/catalog-schema.md

**Quadrant:** Reference (catalog/<vendor>/<model>.yaml schema — every field, rules, decision tree)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 4 | 3 | 2 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 1 | 3 | 4 |
| **Total** | **7** | **16** | **15** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L11–L26 | The "Capability Structure" example block (L15–L24) uses `signals`, `conditions`, `controls`, `attributes` keys before any of those terms have been defined in the body. The reader must trust the inline comments. A one-line intro sentence describing what a capability *is* (a function+direction with these four typed dicts) before the YAML block would let the reader parse the example. |
| WARNING | L46–L51 | "`SpecBand` `when` keys" rules are introduced before the reader has seen how `bands:` is actually structured in the surrounding capability. The bands example (L38–L44) shows the shape, but the rules block (L46–L51) refers to "sibling name from signals, conditions, or controls" — terms that aren't fully defined until L84–L123. Move the sibling-name rule down after `controls` is introduced, or forward-reference it. |
| SUGGESTION | L177–L189 | "MeasurementFunction — use the MOST SPECIFIC value" appears between Qualifier (L154) and Board-Level Attributes (L191), but logically belongs at the top with the `function:` field introduction (L16). A reader scanning for "what values can `function:` take?" will not find it until they're 60% through the page. |
| SUGGESTION | L228–L240 | The "What goes WHERE — decision tree" table is the densest, highest-value content in a reference page and arrives near the bottom. Move it nearer the top (or duplicate as a summary), since Diátaxis reference says "densest facts first." |

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L9 | hedging | "likely a 1.0+ event" |
| SUGGESTION | L3 | throat-clearing | "For worked examples of each pattern (one recipe per recurring datasheet shape), see..." — a long parenthetical before the actual cross-link. Could be "See the [catalog cookbook](catalog-cookbook.md) for worked recipes." |
| SUGGESTION | L26 | passive voice hiding actor | "is a convenience fallback used by the execution layer when signal-level units aren't set" — could be "the execution layer falls back to the top-level `units` when signal-level units aren't set." |

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L7 | programmer jargon | "Source of truth: `src/litmus/models/capability.py` (Pydantic models)." — the "Pydantic models" parenthetical is jargon a test engineer won't translate. Either drop it (the path is enough) or say "(the schema definitions)." |
| WARNING | L15 | cold drop of enum | "function: dc_voltage          # MeasurementFunction enum" — first appearance uses bare "MeasurementFunction enum" without saying what it is or linking to its enumeration. The reader has to wait until L177 / L189 to find out the values come from `src/litmus/models/` (also a cold drop). |
| SUGGESTION | L30 | jargon adjacent to T&M | "Each signal has range, accuracy, resolution, and condition-dependent overrides (`bands` / [SpecBand](models.md) — a value-plus-condition record)." — "value-plus-condition record" reads like a programmer phrasing. A test engineer would understand "an override that applies only when these operating conditions match." |
| SUGGESTION | L52–L65 | jargon | "RangeSpec", "PointSpec", "ListSpec" — exposed model class names in a column where the test engineer just wants to know "what can I write in YAML?" The "Type" column adds noise the audience doesn't need. Consider dropping the Type column or renaming to "Shape." |

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L162 | doc lists qualifier value `limit_nominal` | actual enum value is `nominal` (`SpecQualifier.NOMINAL = "nominal"`) | `src/litmus/models/capability.py:65` |
| CRITICAL | L191–L209 | doc shows board-level attributes wrapped under `catalog_entry:` (e.g. `catalog_entry:\n  id:\n  attributes:`) | Actual catalog YAML files have these fields at the **root** of the document (e.g. `src/litmus/catalog/generic/generic_eload.yaml` has `id:`, `manufacturer:`, `channels:`, etc. at top level). `InstrumentCatalogEntry` is validated against the root dict, not a `catalog_entry:` sub-key. The `"catalog_entry" in data` check in `_detect_yaml_type` looks for it as an optional discriminator, but no shipped catalog uses that wrapper, and the validator does not require it. | `src/litmus/store.py:198`, `src/litmus/models/catalog.py:20-71`, `src/litmus/catalog/generic/generic_eload.yaml:1-32` |
| CRITICAL | L51 | doc says "Unknown keys cause warnings" | Unknown SpecBand `when` keys raise `ValueError` and reject the entire model — they do NOT warn | `src/litmus/models/capability.py:487-491` |
| CRITICAL | L51 | doc says "duplicate names across categories cause errors" | True, but the doc phrases this loosely. The actual rule: only `signals`/`conditions`, `signals`/`controls`, `conditions`/`controls` pairs are checked for overlap — `attributes` is NOT cross-checked against the others. A name appearing in both `attributes` and `signals` does not raise. | `src/litmus/models/capability.py:464-475` |
| WARNING | L14–L22 | doc lists `units: V` as a top-level capability field labelled "convenience fallback" | Confirmed: `Capability.units: str | None` exists. But the doc's comment "Optional — convenience fallback when all signals share one unit" describes consumer behavior, not validator behavior. The model accepts this freely; the "fallback" is implemented by callers, not by the model. | `src/litmus/models/capability.py:452` |
| WARNING | L23 | doc indents `units: V` deeper than the other capability fields (extra leading space) | Cosmetic, but inconsistent with the rest of the block; readers may think it's a nested key | L23 |
| WARNING | L52–L65 | the row for `"SLOW"` says "Type: string", `50` says "Type: float", `true` says "Type: bool" | All correct, but the row for bare list `[50, 600, "HiZ"]` says "Type: list" with logic "Membership" — actual `SpecBand.when` accepts `list[str | float | bool]` (no nested specs). Doc is correct. | `src/litmus/models/capability.py:197-200` |
| SUGGESTION | L189 | "Full enum list: read `MeasurementFunction` in `src/litmus/models/`" | The enum is at `src/litmus/models/enums.py:24` — give the file, not just the directory. The directory contains many enums and a reader will not find it by inspection. | `src/litmus/models/enums.py:24` |
| SUGGESTION | L211 | "Common board-level attributes: `operating_temperature`, ...`max_working_voltage`." | None of these are formally defined as keys (Attribute is `extra="forbid"` only for *its own structure*; the *name* is freeform). True, but the doc reads like an enum when it's a convention. Consider "Conventional names (not enforced): ..." | `src/litmus/models/capability.py:312-368` |
| VERIFIED | — | 14 claims verified against source (capability/catalog structure, `bands:` field name on Signal/Condition/Control/Attribute/Capability, `AccuracySpec` fields, `ResolutionSpec` fields, SpecBand `when` value types, TerminalRole enum members, ConnectorType enum members, GroundTopology enum members, Direction enum values, `MeasurementFunction` enum existence and member names, `band_matches` AND-semantics, `Attribute._require_value_range_or_options` constraint, `InstrumentCapability.channels` accepting range strings, `expand_range("ai[0:7]")` valid syntax) | — | — |

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L191–L213 | "Board-Level Attributes" section is the only place where `catalog_entry:` appears with a leading colon — but the section never defines what `catalog_entry` is (a wrapping key? the file root?) or shows the **full** top-level YAML structure (`id:`, `manufacturer:`, `model:`, `type:`, `channels:`, `capabilities:`, ...). A reader who reaches this page first has no way to know the overall shape of a catalog YAML file. There is no example of a complete file. |
| CRITICAL | L256–L258 | "Instrument Variants (Option Codes)" mentions "A post-processing step splits variant-gated specs into separate catalog entries using `base:` inheritance" but never explains how `base:` works, where it goes in YAML, or how to invoke the post-processing step. A user reading the schema reference cannot author a variant. The `base:` field is defined in `InstrumentCatalogEntry` but never documented here. |
| WARNING | L4–L9 | The "Status: Frozen at 1.0" paragraph mentions `CATALOG_SCHEMA_VERSION` but the page never tells the reader where to put a version key in their YAML (or whether they should). Does the user write `version: "1.0"` at the top? Is it injected? Is it a comment? Unanswered. |
| WARNING | L154–L175 | Qualifier section says "must always be explicit — there is no implied default" but doesn't say what *happens* if you omit it. Validation error? Silent skip? Inherited from parent? |
| WARNING | L216–L226 | Channel Topology shows the field shape but never states what `connector_pin` is or how to use it (the model has `connector_pin: dict[str, int | str] | None` — a documented field with no doc presence here). |
| WARNING | L228–L240 | The decision-tree table is excellent but doesn't cover: where do `interfaces:` (USB/LAN/GPIB) go? Where does `form_factor:` go? Where does `driver:` go? Where does `description:` go? All present on `InstrumentCatalogEntry`, none mentioned. |
| WARNING | L257 | "different SKU, different hardware. During extraction, model them as normal controls with SpecBand `when` clauses so data stays traceable to the PDF" — the reader doesn't know what "extraction" is; it's an internal concept from the catalog-from-datasheet skill. A user authoring catalogs by hand will be lost. |
| SUGGESTION | (whole page) | Missing "Validation" section. How do I check my YAML loads? The skill files reference `uv run python -c "from litmus.store import load_catalog_entry; load_catalog_entry(Path('...'))"` — this should be in the reference. |
| SUGGESTION | L256–L258 | Variants section provides no example. Show one base file + one variant file with the inheritance resolved. |
| SUGGESTION | (whole page) | Missing a complete, real catalog YAML at the top (or bottom) showing every section in context. A reference page that never shows the whole document forces the reader to assemble fragments. |

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L189 | "Full enum list: read `MeasurementFunction` in `src/litmus/models/`" — no link, no concept page reference. The `docs/concepts/capability-model.md` page has a `## Functions (MeasurementFunction enum)` section (L30) that would be the right target for a doc reader. Also the source path is a directory, not a file. |
| WARNING | L30 | Link `[SpecBand](models.md)` has no anchor — `docs/reference/models.md` does not have a `## SpecBand` section; the closest is the ERD reference. Either add an anchor to models.md, or point to `concepts/capability-model.md#condition-dependent-specs-specband` which is the real definition. |
| WARNING | L240 | "(ChannelTopology)" in the decision-tree table — first use of the model name with no link. Should link to `reference/models.md` or `concepts/capability-model.md`. |
| WARNING | (whole page) | No "See also" / "Next steps" section. A reference page this dense should at minimum link out to: `catalog-cookbook.md` (already linked top), `concepts/capability-model.md` (the WHY), `concepts/capabilities.md`, and `tutorial/08-capabilities.md` (introduces the schema). |
| SUGGESTION | L23 | `MeasurementFunction enum` comment in the YAML example — first inline use, no link. Could link to `concepts/capability-model.md#functions-measurementfunction-enum`. |
| SUGGESTION | L154 | "Qualifier" — first use of `SpecQualifier` concept; could link to `concepts/capability-model.md` if it covers qualifiers, or to `reference/models.md`. |
| SUGGESTION | L216 | "Channel Topology" — first use; could link to `concepts/capability-model.md` or `reference/models.md` for the model definition. |
| SUGGESTION | L256 | "Instrument Variants (Option Codes)" — `base:` inheritance is a real feature, should link to whatever doc explains it (currently nothing — see Gaps). |
