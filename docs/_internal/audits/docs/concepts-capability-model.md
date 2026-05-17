# Page audit: docs/concepts/capability-model.md

**Quadrant:** Concepts / Explanation (the four-typed-collection capability model: signals, conditions, controls, attributes)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 2 |
| Voice | 0 | 1 | 4 |
| Audience | 0 | 1 | 2 |
| Accuracy | 2 | 4 | 3 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 3 |
| **Total** | **5** | **15** | **16** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L13-L16 | The page calls this a "five-part structure" then immediately writes a six-part formula (`Function + Direction + Signals + Conditions + Controls + Attributes`). A reader counting the parts is blocked on paragraph two — they cannot tell which list to trust. |
| WARNING | L30-L51 | "Functions (MeasurementFunction enum)" precedes "Direction" (L53), but in the formula at L16, Function comes before Direction so the order is fine. However, the formula's "Signals / Conditions / Controls / Attributes" block (the page's main subject) does not appear until L68 ("Typed Parameter Collections") — three sections later. A reader following the formula left-to-right is detoured through the function taxonomy and the direction table before reaching what the page is actually about. |
| WARNING | L98-L124 | The "Typed Collection Fields" tables describe each collection's allowed fields, but the YAML at L72-L96 *already* showed `bands`, `options`, `value`, `units` etc. used in those collections. Readers see the YAML keys in use before the field tables explain them. Swap: short prose / table first, then the full YAML showcase. |
| SUGGESTION | L171-L176 | "Why Separate Typed Collections?" is the design rationale and belongs *before* the field tables and the example block (L98-L168), not after. The current flow shows the answer (what the collections are), demos them, then explains *why* — which is the inverse of the "motivated explanation" ordering a concept doc should follow. |
| SUGGESTION | L208-L219 | "Condition Keys (ConditionKey enum)" lives inside the SpecBand section but conceptually belongs with the `conditions` collection definition (L108-L112). A reader hunting for "what keys go in `conditions`?" reads L108 and gives up; the vocabulary lives 100 lines later. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L172 | Hedging / superlative | "The four-collection approach is **clearer** than role tags" — comparison without a definable yardstick; reads as marketing. |
| SUGGESTION | L51 | Throat-clearing | "Functions describe **what**, not **how**." — useful contrast but framed as a punchline; consider naming the implication ("a DMM and a scope share `dc_voltage`; their differences live in the parameters"). |
| SUGGESTION | L70 | Hedging / temporal drift | "capabilities **now** have four typed collections" — the word "now" implies a previous design the reader doesn't know about. A concept doc should present the model as it is, not as it became. |
| SUGGESTION | L172 | Promotional adjective | "well-defined purpose" — drop; the next four bullets define the purposes, the adjective adds nothing. |
| SUGGESTION | L265 | Hedging | "(too complex for our needs today)" — "today" injects time-relative uncertainty into a lineage table. Either commit ("not needed") or remove the parenthetical. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L70 | Programmer jargon | "Instead of a single `parameters` dict with role tags" — "role tags" is a software-design term the test-engineer audience has no anchor for; it references a previous internal design they never saw. Cut the comparison or describe what the user actually types. |
| SUGGESTION | L11 | Vocabulary | Header is "The Shared Language: Capability" — "shared language" is fine but "shared vocabulary" (the term the codebase uses, see `ConditionKey` docstring at `src/litmus/models/capability.py:381`) is the established term. |
| SUGGESTION | L263-L273 | Anti-audience / academic | The "Lineage: Where This Came From" table and "Key design decisions" bullets are aimed at someone evaluating Litmus vs. ATML/IEEE 1641/IVI — not a test engineer trying to author a catalog entry. Useful for credibility, but a one-line "if you know IEEE 1641, we use a flat function enum" link to an appendix would serve the engineer better than 16 lines of standards talk. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L13, L16 | "Both instruments and products describe their electrical behavior using the same **five-part structure**" followed by `Capability = Function + Direction + Signals + Conditions + Controls + Attributes` (six parts) | The `Capability` model has six fields used here: `function`, `direction`, `signals`, `conditions`, `controls`, `attributes` (plus optional `units` and `bands`). Either the prose count or the formula is wrong. | `src/litmus/models/capability.py:446-453` |
| CRITICAL | L280-L284 | Diagram shows catalog YAML wrapped under a top-level key `catalog_entry:` (`catalog_entry: \n  id: keysight_34461a \n  type: dmm \n  channels: ...`) | Actual catalog YAML has `id:`, `type:`, `channels:`, `capabilities:` at the **top level** — there is no `catalog_entry:` wrapper. See e.g. `src/litmus/catalog/generic/generic_dmm.yaml`. A reader copying this YAML and trying to load it will get a validation error. | `src/litmus/catalog/generic/generic_dmm.yaml:1-15` |
| WARNING | L108-L112 | "**Conditions** — operating parameters that affect other parameters' accuracy" lists only one field: `range`. | `Condition` model has five fields: `range`, `options`, `units`, `default`, `bands`. The page omits `options`, `units`, `default`, `bands`. The `options` field matters — the model docstring at `capability.py:264-266` explicitly shows `conditions.calibration_interval.options: [...]` as a discrete-condition example. | `src/litmus/models/capability.py:251-275` |
| WARNING | L113-L118 | "**Controls** — user-settable knobs" lists `range` and `options: list[str]`. | `Control` model has six fields: `range`, `options`, `units`, `default`, `resolution`, `bands`. The page omits `units`, `default`, `resolution`, `bands`. Also: `options` is typed `list[float \| str \| bool]`, not `list[str]` — boolean and numeric options (e.g., `[true, false]`, `[1, 2, 5, 10]`) are valid and shown in the source example at line 297-298. | `src/litmus/models/capability.py:278-309` |
| WARNING | L119-L123 | "**Attributes** — fixed hardware specifications" lists `value` and `units` only. | `Attribute` model has six fields: `value`, `range`, `options`, `units`, `bands`, `qualifier`. The model has a validator (`_require_value_range_or_options`) that *requires* one of `value` / `range` / `options` / `bands` — the page never states this constraint and omits three of the four valid choices. | `src/litmus/models/capability.py:312-368` |
| WARNING | L106 | `resolution` field listed as `{digits, value, units}`. | `ResolutionSpec` has four fields: `bits`, `digits`, `value`, `units`. The page omits `bits` — relevant for digitizer/ADC attributes and used in the page's own oscilloscope example at L142-L144 (`resolution: value: 10, units: bits`). | `src/litmus/models/capability.py:135-143` |
| SUGGESTION | L62 | "transform: Modifies signal in-path" | Source says "Signal-path component (amplifier, filter, mixer)". The doc's phrasing is fine; consider adding "mixer" alongside "amplifier, filter" to match the model's own description and avoid implying only linear elements. | `src/litmus/models/enums.py:21` |
| SUGGESTION | L32 | "~70 named signal types" | Actual `MeasurementFunction` enum has **67** members today. "~70" is acceptably approximate, but stating "60+" or "around 70" matches the source more precisely. | `src/litmus/models/enums.py:24-152` |
| SUGGESTION | L246-L251 | Comment "`+/-3% = 3.201V to 3.399V`" | Math checks out (3.3 × 0.03 = 0.099). The second band (L249-L251) shows `pct_reading: 5.0` with comment "`+/-5% over full range`" — for parity with the first band, show the resulting tolerance window (3.135V to 3.465V) so the reader can compare. | — |
| VERIFIED | — | 19 additional claims verified against source: `MeasurementFunction` member names (`dc_voltage`, `ac_voltage`, `waveform`, `s_parameters`, `phase_noise`, `harmonics`, etc.); `Direction` member set (input/output/bidir/transform); `ConditionKey` count of 27 and category groupings; `150+ instrument datasheets` provenance line; `Capability` having `signals`/`conditions`/`controls`/`attributes` typed dicts; `_validate_band_when_keys` flat-union and disjointness rules; `SpecBand.when` accepting `RangeSpec`/`PointSpec`/`ListSpec`/scalars; `band_matches` ANDing all keys; `Capability` as base of `InstrumentCapability` and `ProductCharacteristic`; `ChannelTopology` fields (`terminals`, `connector`, `ground`); `AccuracySpec` fields (`pct_reading`, `pct_range`, `absolute`, `units`); `Comparator` lineage from ATML/IEEE 1671; `ProductCharacteristic` `pin`/`pins`/`net`/`signal_group` four-way physical interface; `Pin.role` taking `PinRole.SIGNAL` etc.; `WaveformShape` as a separate enum, not part of `MeasurementFunction`. | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L98-L124 (field tables) | The field tables for `Conditions`, `Controls`, and `Attributes` are *incomplete* (see Accuracy findings). A reader using this page as their mental model will not know that `conditions` can have discrete `options`, that `controls` carry `default` and `resolution`, or that `attributes` *must* provide one of `value`/`range`/`options`/`bands` (the validator will reject anything else). The page silently teaches the wrong constraint surface. |
| WARNING | L235-L252 (product example) | The product example uses **top-level `bands:` on the characteristic** with `value:` and `accuracy:` directly inside each band — but every prior example (L73-L96, L127-L168, L182-L202) put `bands:` *inside* a `signals.<name>` block. The page never explains that bands can live at either level (`Capability.bands` vs `Signal.bands`), or when you'd choose one over the other. A reader trying to author a product YAML by analogy is left guessing. |
| WARNING | L230-L252 | The product example uses `pins: [VOUT]` (list form). The diagram at L304-L308 for the same concept uses `pin: VOUT` (singular). The page never tells the reader that both `pin` (single) and `pins` (list / range string) are valid `ProductCharacteristic` fields, nor when to use which — even though the source supports four physical-interface options (`pin`, `pins`, `net`, `signal_group`) per `src/litmus/models/product.py:152-156`. |
| WARNING | L178-L207 (SpecBand) | The page explains *what* a SpecBand is but never says what happens when **no band matches** the operating point. Does the engine fall back to the top-level default? Raise? Return None? (Source: `ProductCharacteristic.get_spec_at` returns `None`; the lookup contract belongs in this doc.) |
| WARNING | L208-L219 (ConditionKey) | "`ConditionKey` is a shared vocabulary ... not enforced at the model level" — useful, but the page never tells the reader what happens if they use a non-canonical key (`my_custom_condition`). Will the band validator reject? Will the matcher silently miss? (Source `_validate_band_when_keys` *does* reject when the `when` key isn't a sibling parameter name — that constraint is the one that bites, not the canonical-vocabulary one. Worth saying.) |
| SUGGESTION | L253-L258 (matching example) | "The matching engine can now compare..." gives three bullet conclusions but no link to where matching is implemented or explained. A concept doc is the right place to say "see `concepts/capabilities.md#capability-matching` for the algorithm." |
| SUGGESTION | L319 | The diagram ends with "MATCH!" but never names what the matcher *returns* (a score? a binding? a `MatchResult`?). For a concept doc on the model, naming the output type would close the loop. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | Whole page | The page has **zero outbound links**. It is a concept doc that introduces `Capability`, `InstrumentCapability`, `ProductCharacteristic`, `SpecBand`, `Signal`, `Condition`, `Control`, `Attribute`, `ChannelTopology`, `MeasurementFunction`, `ConditionKey`, `Comparator`, `Direction`, `PinRole`, `band_matches`, `get_spec_at` — and links none of them. Every one of these has a defining file (`src/litmus/models/capability.py`, `src/litmus/models/enums.py`, `src/litmus/models/product.py`) and most are documented in `docs/reference/models.md` and `docs/reference/catalog-schema.md`. A reader who wants to *use* the model has no exit ramp. |
| WARNING | Whole page | No "See also" / "Next steps" section. At minimum this page should link to: `docs/reference/catalog-schema.md` (the field-by-field reference for the same models), `docs/reference/models.md` (the Pydantic model reference), `docs/reference/catalog-cookbook.md` (worked YAML recipes), and `docs/concepts/capabilities.md` (the sibling concept doc — see next finding). |
| WARNING | Whole page | **Sibling-page overlap not acknowledged.** `docs/concepts/capabilities.md` covers substantially the same territory (capability hierarchy, direction pairing, typed collections, SpecBand, condition-dependent specs, lineage). Neither page links to the other and neither states what makes it distinct. A reader landing on `capability-model.md` does not know `capabilities.md` exists; vice versa. Either merge or cross-link with a clear "this page covers X; for Y see Z." |
| WARNING | L7 | First mention of "Keysight 34461A" — could link to a real catalog entry or example. Currently the model name is used as motivation but the reader has no way to see the resulting YAML. The diagram at L280 names `keysight_34461a` again with no link. |
| SUGGESTION | L42-L49 | The function-category bullet list could link each major group to its IVI class reference page or a representative catalog cookbook recipe (e.g., "Waveform" → `reference/catalog-cookbook.md` waveform section). Concept doc readers often follow such trails. |
| SUGGESTION | L208-L219 | The ConditionKey table could link each category header to the matching `catalog-cookbook.md` recipe that uses those keys (e.g., "Measurement config" → NPLC/auto-zero recipe). |
| SUGGESTION | L263-L267 | The Lineage table cites IEEE 1641, ATML/IEEE 1671, and IVI Foundation — external standards links would help the reader who wants to follow the trail. (External URL linkage is fine for lineage even though most page links should be internal.) |
