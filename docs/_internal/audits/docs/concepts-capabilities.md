# Page audit: docs/concepts/capabilities.md

**Quadrant:** Concepts/Explanation (capability matching — how DUT characteristics pair with instrument capabilities)
**Audited:** 2026-05-17

> **Coordinator note:** The audit-coordinator workflow normally dispatches six sub-agents (Task tool) in parallel. The Task/Agent tool is not available in this environment, so the six dimensions below were performed inline by the coordinator against the same rubric. Findings format and severity tags match what the sub-agents would have produced.

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 3 | 2 |
| Accuracy | 2 | 3 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **4** | **15** | **13** |

---

## Ordering

**Rubric for Concepts:** lead with the problem and the shared mental model; introduce vocabulary before using it; algorithm/mechanics after model; reference-style tables (enum dumps, condition keys) belong near the end; "Next Steps" / cross-link footer last.

### WARNING — `MeasurementFunction` enum table breaks the conceptual flow (lines 152–212)

The page builds a clean conceptual arc:

1. What is a capability (model)
2. Direction pairing (the "why")
3. Capability matching (the mechanics)

Then at line 152 it pivots into a **60-line reference table** of every measurement function before resuming with "Typed Collections" (a conceptual section). A reader following the explanation has to scroll past a reference dump to continue learning. In Diátaxis terms, this is reference content interleaved into explanation.

**Fix:** move the `MeasurementFunction` table to the bottom (or split it into a sibling reference page and link). Keep a *short* prose paragraph in place describing what `MeasurementFunction` is and why it matters (1 paragraph, 2–3 examples), then "see reference table below" or `[reference/measurement-functions]`.

### WARNING — "Direction Pairing" appears twice and the second time is denser than the first (lines 82–112 and 136–150)

"Direction Pairing" (line 82) explains the idea conceptually with an ASCII diagram. Then "Matching Algorithm" (line 136) repeats the same direction-flip story embedded in a Python comment block. The two passes do not build — they restate. A reader feels they've been told the same thing twice.

**Fix:** drop the comment-block narration inside "Matching Algorithm" and instead show a minimal code path that exercises the tiered match logic (function → direction → range), with one sentence pointing back to the Direction Pairing section.

### SUGGESTION — "3-Tier Instrument Catalog" arrives too late (line 293)

The `catalog_ref` mechanism is introduced quietly in the Power Supply example (line 56, in a YAML comment) and again at line 71 — but the conceptual explanation of the three tiers doesn't appear until line 293. Readers spend the whole page wondering "what's that `catalog_ref` for?" Consider hoisting a 4-line "where capabilities live" subsection right after "What Is a Capability?".

### SUGGESTION — "Custom Instruments" feels like a how-to wedged into an explanation (lines 334–361)

The "Custom Instruments" section is procedural ("When adding…, define…"). It belongs in a how-to (or in `catalog-cookbook`) rather than a concepts page. If it stays, demote it to a one-paragraph pointer with a link.

---

## Voice

**Rubric for Concepts:** explanatory, not imperative; reasons over recipes; "this is how Litmus thinks about X." Avoid second-person commands and feature-marketing tone.

### WARNING — Opening sentence overloads three standards in one breath (line 3)

> "...using an ATML (Automatic Test Markup Language) / IEEE 1641-inspired signal-parameter model — ATML / IEEE 1671 is the industry test-data interchange standard Litmus aligns with."

This sentence references ATML, IEEE 1641, *and* IEEE 1671 before the reader has been told what a capability is. The standards lineage is real (see `enums.py:28` and `models/test_config.py:236`), but leading with it makes the page feel like a brochure. Move the lineage to a small "Where this came from" callout at the bottom (the way `capability-model.md` does at line 259, "Lineage: Where This Came From").

### SUGGESTION — "The key insight is that **directions pair**" (line 84)

"Key insight" is a tutorial/blog tic. Concepts docs can just say "Directions pair." Same applies to "Why This Works" header (line 93) — concepts pages are *all* "why this works."

### SUGGESTION — "MATCH!" with an exclamation mark (line 150)

Stylistically out of register for an explanation. Either drop the exclamation or replace the whole comment block (see Ordering finding above).

### SUGGESTION — Emoji checkmarks in a code comment (line 148)

`# → Function match ✓, Direction pair (OUTPUT↔INPUT) ✓, Range contains 3.3V ✓` — checkmarks read fine in a UI mockup but are noise in a Python comment. Use plain words.

---

## Audience

**Rubric for Concepts:** assume a test engineer who knows hardware test but not Litmus internals; introduce Litmus-specific jargon (SpecBand, MatchDepth, catalog_ref) the first time they appear.

### WARNING — `MatchDepth` is used without being defined (line 117)

> "The matcher determines whether a station can test a product using tiered matching controlled by `MatchDepth` (an enum naming how deep to take the match check)..."

A reader sees `MatchDepth` for the first time here. The parenthetical helps, but the page never tells the reader *why anyone would change the depth* or *what the default is*. From the code (`service.py:322`) the default is `MatchDepth.RANGE`, and the deeper levels exist so MCP / recommend flows can match at coarser tiers. Add one sentence: "The default depth (`RANGE`) checks function, direction, and parameter-range containment; deeper levels (`ACCURACY`, `RESOLUTION`) are used by tools like `recommend_from_catalog` that want strict matches."

### WARNING — `SpecBand` introduced sideways with a link, never explained inline (line 120)

The first appearance of `SpecBand` is `(condition-aware via [`SpecBand`](../reference/models.md), the value-plus-condition record)`. A reader has to click out of the page to find out what it is. The page does eventually explain it at line 244 ("Condition-Dependent Specs"), but in the meantime they've seen it referenced. Either:
- Hoist a 1-sentence "(a `SpecBand` is a per-condition spec override — see below)" parenthetical, or
- Defer mentioning `SpecBand` until line 244 and just say "accuracy can vary with operating conditions; the matcher accounts for that — see below."

### WARNING — Assumes prior knowledge of ATML/IVI/IEEE 1641 (line 3, line 154, line 230)

Test engineers know IVI broadly; ATML and IEEE 1641 are niche. Currently the page name-drops them without telling the reader why they should care. A test engineer writing their first capability YAML doesn't need to know the standards lineage — that's nice-to-have context. Move it to a footer ("Where the model came from") and replace the opening sentence with something action-oriented: "Litmus describes both what an instrument can do (capabilities) and what a product needs (characteristics) using the same shape. That shared shape is what lets the matcher pair them automatically."

### SUGGESTION — "ATML (Automatic Test Markup Language)" expansion is wrong (line 3)

ATML stands for **Automatic Test Markup Language**, but it's usually written as "Automated Test Markup Language" in IEEE and industry usage. Worth verifying with the IEEE source — and if uncertain, drop the expansion since the standards lineage shouldn't be leading anyway (per above).

### SUGGESTION — "transform" direction explained as an afterthought (line 210)

The four directions (input/output/bidir/transform) are introduced at line 12. "Transform" gets a one-sentence aside at line 210 ("used for signal-path components"). For a concepts page, transform deserves the same treatment as bidir — even one line in the direction-pairing table. Currently a reader might never realize transform is the right choice for an amplifier-under-test.

---

## Accuracy

**Rubric:** every claim verified against source.

### CRITICAL — Field is `when`, not `conditions` (lines 254, 267)

The YAML example at line 254 correctly uses `when:`. But the prose at line 267 says:

> "The `conditions` keys in SpecBand reference sibling condition names. Multiple keys are ANDed — all must match."

`SpecBand` has a `when` field (see `capability.py:197`), not a `conditions` field. The keys *inside* `when` happen to reference sibling condition names, but calling them "the `conditions` keys" reads as if the field itself is `conditions:`. This will mislead anyone trying to author YAML.

**Fix:** "Each `when` key references a sibling parameter (signal, condition, or control); multiple keys are ANDed — all must match. An empty `when:` always matches."

Note also: `when` keys are not restricted to conditions — they can reference signals and controls too (`capability.py:477` builds the known set from `signals | conditions | controls`).

### CRITICAL — `band_matches` description omits non-condition sources for `when` (line 267)

Same root cause as above. The matcher's `band_matches` (`capability.py:569`) walks **all** `when` keys against an operating-point dict that includes signal values, condition values, and control defaults (`service.py:_build_operating_point` lines 406–436). A page saying "conditions keys" implies bands can only derate on conditions; in practice bands can derate on signal values (e.g., range derated at high frequency where frequency might be a signal of a separate capability) and on controls.

### WARNING — "BIDIR satisfies both" is asymmetric in the implementation (line 118)

The page says:

> "Direction match — directions pair correctly (OUTPUT↔INPUT, BIDIR satisfies both)"

In `service.py:_directions_compatible` (lines 199–218):
- Instrument BIDIR → matches **any** product direction.
- Product BIDIR → matches **only** instrument BIDIR (line 214–215).

So "BIDIR satisfies both" is only true when BIDIR is on the *instrument* side. A product declared as BIDIR (e.g., a port that both sources and sinks) will NOT match an instrument that's only `input` or only `output` — it requires a BIDIR instrument like an SMU or VNA. Worth stating explicitly.

### WARNING — Auto-matching exclusion of `readback: true` is asserted but not explained (line 75)

The Power Supply YAML comment says `readback: true  # Excluded from auto-matching`. Grepping the matching service, **nothing in `capability_satisfies` or `match_capabilities` filters on `readback`**. The `readback` field is exposed as a property on `StationCapability` (`service.py:118`) but never read by the matching logic in this file. The exclusion may live elsewhere (or may not exist), but the comment as written is not verified by `matching/service.py`. Either:
- Verify where readback filtering happens and cite it, or
- Soften the claim ("readback meters are typically considered secondary measurements").

### WARNING — `find_compatible_stations` return type wording is loose (line 322)

Page: "`find_compatible_stations(product)` takes a loaded `Product` object and returns a `list[StationMatch]`."

That's accurate. But the surrounding paragraph also says: "`check_station_compatibility(product_id, station_id)` takes id strings and returns a `dict | None`; its `missing` value is a list of dicts shaped `{characteristic, function, direction}`."

Verified against `service.py:649–700` — accurate. Good. (Keeping as a passing check, but worth noting the `matches` key is also a list of dicts and not mentioned; if the page wants to document the return shape, it should be complete or explicitly say "(other keys omitted)").

### SUGGESTION — `channels` accepts more than just a list (line 290)

Page: `channels: ["1", "2"]  # Which channels support this capability`. The model accepts `str | list[str]` and supports range syntax like `"1:4"` and `"CH[1:4]"` (`capability.py:550–566`). Concepts page doesn't need to enumerate every form, but a one-line "(also supports range strings like `'1:4'` — see reference)" prevents the misconception that only lists work.

### SUGGESTION — `MeasurementFunction` table is incomplete (lines 156–209)

The table lists ~36 functions. The enum has ~63 (see `enums.py:24–152`). Notable omissions: `rf_am`, `rf_fm`, `rf_pm`, `rf_sweep`, `rf_iq`, `rf_pulse`, `dc_ratio`, `thd`, `snr`, `gain`, `return_loss`, `insertion_loss`, `vswr`, `group_delay`, `humidity`, `charge`, `lock_in_detection`, `heater_power`, `excitation_current`, `pulse_generation`, `trigger`, `reference_clock`, `conductance`, `reactance`, `susceptance`, `dynamic_load`. Either:
- Label the table "Selected functions (see reference for the complete list)," or
- Move the full enum to a reference page and shrink the concepts page to a representative subset.

---

## Gaps

**Rubric for Concepts:** does the page answer the "why," "what," and "how does this fit together" questions a test engineer would have? Does it surface design tradeoffs?

### CRITICAL — Two near-identical concepts pages exist: `capabilities.md` and `capability-model.md`

`docs/concepts/capability-model.md` (the sibling page) covers the same ground — Capability shape, Function/Direction, typed collections, SpecBand, lineage — and is the page the **models reference** (`reference/models.md:3`) and the **ontology graph** (`graph.json`, `spec_band` and `product_characteristic` nodes) point to. Neither page links to the other. The two pages drift independently:

- `capabilities.md` (this page) puts the standards lineage at the top.
- `capability-model.md` puts it at the bottom in a "Lineage" section (which is the right place).
- `capabilities.md` introduces `MatchDepth` and the `recommend_from_catalog` flow; `capability-model.md` does not.
- `capability-model.md` has an explicit "Why Separate Typed Collections?" rationale; `capabilities.md` doesn't.
- Both define the four typed collections, but with slightly different tables.

This is an audit-level structural gap. Either consolidate into one page (recommended, since they are presenting the same concept) or define an explicit split — e.g., `capability-model.md` for the shape, `capabilities.md` for matching — and have each link to the other prominently from the top.

### WARNING — No mention of "what happens when no station matches"

The matcher returns `compatible: False` with a list of missing requirements, plus `find_partial_stations` and `find_all_station_matches` for procurement planning (`service.py:728, 930`). The page never says "and if nothing matches, here's what you can do" — which is the most common real-world scenario for a new product/station setup. Add a short subsection: "Partial matches and procurement planning."

### WARNING — "channels" is shown twice with two different shapes — the difference isn't explained

- Line 60: top-level `channels: {"1": {terminals: [...], ...}}` — a dict of `ChannelTopology` objects.
- Line 71: per-capability `channels: ["1", "2"]` — a list of references.

The page implies they're the same field. They're not — they're two different things sharing a key name. Top-level channels describe physical topology (`ChannelTopology`), per-capability channels are references into that map. The "Channel Specification" section (line 270) repeats both without flagging the duality. Concept readers will conflate them.

### WARNING — No explanation of `direct_direction` mode

`capability_satisfies` has a `direct_direction` parameter (`service.py:323, 347–350`). It's used by `recommend_from_catalog` (`service.py:812`) to bypass the OUTPUT↔INPUT flip when an MCP agent says "I need an *input* instrument" (specifying the instrument direction directly). This is non-obvious behavior, and a concept page on matching should at least mention that there's a "direct" mode used by the catalog recommender — otherwise a reader looking at `find_compatible_stations` vs `recommend_from_catalog` results will see what looks like inconsistent matching.

### SUGGESTION — `SpecQualifier` (guaranteed / typical / nominal / supplemental) goes unmentioned

`SpecQualifier` exists on `Signal`, `SpecBand`, and `Attribute` (`capability.py:45–66, 248, 350`) and tags whether a spec is warranted or typical. The model file calls it "industry-standard datasheet semantic." Even one line in the "Condition-Dependent Specs" section noting "specs can also carry a qualifier (`guaranteed` / `typical` / …) so the matcher can prefer warranted specs — see reference" would close this gap.

### SUGGESTION — `ConditionKey` vocabulary not surfaced

The shared `ConditionKey` enum (`capability.py:376–423`) names ~25 canonical condition keys (frequency, temperature, NPLC, coupling, impedance, …). This is the vocabulary `band.when` keys are *expected* to use. A concepts page on condition-dependent specs should at least say "here's the shared vocabulary" or link to it — otherwise authors invent ad-hoc names that work locally but defeat cross-instrument matching.

---

## Cross-links

**Rubric:** every concept that has its own page should link there on first mention; every page that links *in* should also have a link *out*; reference tables and YAML schema details should link to the reference quadrant.

### CRITICAL — Page does not link to the sibling `capability-model.md`

See Gaps finding above. Inbound links from `reference/models.md:3` and `graph.json` route to `capability-model.md`, not this page. A reader entering through `architecture.md` or `index.md` lands here; a reader entering through `reference/models.md` lands at `capability-model.md`. Neither page links to the other. Add a prominent cross-link, or merge the pages.

### WARNING — "Next Steps" omits the most directly related pages

Current "Next Steps" (lines 363–367) links to fixtures, architecture, and custom-drivers. Missing:
- `tutorial/08-capabilities.md` — the hands-on counterpart (heavily referenced by tutorial index).
- `concepts/products.md` — where characteristics come from on the DUT side.
- `concepts/stations.md` — where capabilities come from on the bench side.
- `reference/models.md` and `concepts/capability-model.md` — for the model details.
- `reference/catalog-schema.md` — referenced once in tutorial 08, but never from here (this page talks about the 3-tier catalog at length).

### WARNING — `[SpecBand](../reference/models.md)` link points at a 500-line ERD instead of a SpecBand-specific anchor (line 120)

The link drops the reader on the top of a long Mermaid diagram. `models.md` does have a SpecBand entity at line 53, but the link has no anchor. Either add a header anchor in `models.md` and link to it (e.g. `../reference/models.md#specband`), or link to the more digestible explanation in `concepts/capability-model.md#condition-dependent-specs-specband`.

### WARNING — "3-Tier Instrument Catalog" section doesn't link to the catalog reference

Lines 293–303 introduce the catalog tier hierarchy. No link to `reference/catalog-schema.md`, `reference/catalog-cookbook.md`, or `concepts/stations.md` (where the catalog/instrument/station relationship is also discussed). A reader who wants to actually write a `catalog/keysight_34461a.yaml` is left to guess.

### SUGGESTION — "MeasurementFunction" should link to enum reference (line 152)

The table reads like reference content; the prose says "The `MeasurementFunction` enum provides…" — link to `concepts/capability-model.md#functions-measurementfunction-enum` (which groups them by domain) or to the source enum.

### SUGGESTION — "Custom Instruments" should link to the catalog cookbook (line 334)

This section walks through defining a temperature logger. The catalog cookbook (`reference/catalog-cookbook.md`) likely has the canonical pattern; link there and shrink the inline example.
