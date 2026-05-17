# Page audit: docs/reference/catalog-cookbook.md

**Quadrant:** Reference (cookbook of recipes for recurring datasheet shapes — companion to catalog-schema.md)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 1 | 3 | 2 |
| Gaps | 0 | 3 | 3 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **2** | **14** | **14** |

---

## Ordering

The page is a flat list of 14 recipes. For a cookbook the order should be either (a) frequency of need / "start here" → edge cases, or (b) grouped by the schema location they touch (signals → conditions → controls → attributes → cross-cutting). Today it's neither — the order roughly tracks recipes by complexity but mixes layers.

### WARNING — Antipattern-first vs solution-first mixing

Recipes 1, 3, 4, 7, 8 lead with the RIGHT shape (good for a cookbook — the reader is here to copy the right thing). Recipes 2, 5, 6, 9, 10, 11, 12 lead with the WRONG/antipattern shape. The page would read more consistently if every recipe followed the same order (recommend: short inventory → WRONG block → RIGHT block, with RIGHT always last so the eye lands on the thing to copy).

### WARNING — Layer grouping is scrambled

Recipes hop between layers in a way that makes the page hard to scan when you have a specific question:

- 1, 2 — signals.bands
- 3 — capability attributes
- 4 — signals.accuracy.units
- 5 — typed models on signals
- 6 — conditions.range
- 7 — controls (shared across capabilities)
- 8 — capability attributes (input_impedance, repeated)
- 9 — catalog_entry.attributes vs capability attributes
- 10 — attribute bands
- 11, 12 — comments / inventory fidelity (cross-cutting)
- 13 — resolution units (back to signals)
- 14 — redundant bands (back to signals)

Recipes 13 and 14 belong adjacent to recipes 1/2 (both are "signals + bands" topics). Recipes 7/8/9 form a natural "where does this attribute live" cluster but are split by 8 vs 9. Suggest grouping under H2s: "Signals & SpecBands" (1, 2, 4, 5, 13, 14), "Conditions" (6, 12), "Controls" (7), "Attributes" (3, 8, 9, 10), "Cross-cutting" (11).

### SUGGESTION — Recipe 5 is two recipes glued together

The "use typed models" recipe has two code blocks (AccuracySpec, ResolutionSpec) and an implicit "and RangeSpec" mentioned in prose but never demonstrated. Either split into 5a / 5b or add a third code block for RangeSpec for symmetry.

### SUGGESTION — Recipe 14 ("Redundant SpecBands") is an anti-recipe and stands oddly at the end

It's a "what NOT to do" — useful, but the closing position makes the page end on a negative. Consider moving to a "Common mistakes" section grouped with recipe 11 (no spec data in comments) and recipe 12 (range mismatches), all of which are about hygiene rather than schema choice.

---

## Voice

The page is in Litmus's house voice — direct, opinionated, "WRONG / RIGHT" framing, plenty of `MUST` / `NEVER` / `EXACTLY`. Matches the schema reference and the catalog-from-datasheet skill. A few drifts:

### WARNING — Mixing first- and second-person frame

The lede uses second person ("you'll meet", "you'll meet when authoring"). Most recipes are imperative ("Put each control ONLY...", "Create BOTH as attributes"). Recipe 7 slips into a meta-frame: *"the inventory agent read the datasheet, you didn't."* That's witty but breaks register — most reference pages avoid scolding the reader. Soften to *"the inventory was extracted from the datasheet; treat its 'Applies To' column as ground truth."*

### SUGGESTION — `MUST` / `NEVER` are heavy

The page leans hard on RFC-2119-style ALL CAPS:

- "Any table where a value varies by a condition MUST become SpecBands"
- "NEVER flatten structured values"
- "Do NOT guess or infer"
- "NEVER put spec data in comments"

Used sparingly, these land. Used six times on one page, they start sounding shouty. Reserve ALL CAPS for the two or three truly load-bearing rules (signals over flat attributes; never name-encode); use normal-case "must" / "don't" for the rest.

### SUGGESTION — "Inventory:" prefix is jargon

Every recipe opens with `# Inventory: ...`. "Inventory" is the term from the `/catalog-from-datasheet` skill (the extraction stage), but a reader of this reference page may not have the skill loaded. Either:

- Add a one-line lede paragraph: *"Each recipe opens with the inventory line — the raw datasheet fact the skill produces in its 'inventory' stage."*
- Or rename the comment prefix to `# Datasheet:` which needs no glossary.

### SUGGESTION — Recipe 7's tone slips into reprimand

*"the inventory agent read the datasheet, you didn't"* reads as scolding rather than instructing. Replace with *"The 'Applies To' column was extracted from the datasheet — copy it literally, don't infer."*

### WARNING — Lede doesn't say what the page is FOR

The lede says what the page contains ("Worked recipes for the recurring datasheet shapes") but not when to reach for it. Add a one-liner: *"Use this when you're staring at a datasheet table and unsure which schema slot the spec belongs in."* That mirrors the audience framing already in index.md.

---

## Audience

The implied reader is someone authoring `catalog/<vendor>/<model>.yaml` — likely an AI agent following the `/catalog-from-datasheet` skill, or a human reviewing the skill's output. The page mostly hits this audience but has gaps for the human-only path.

### WARNING — Page assumes the reader is the skill, not a human

Recipe 7 ("inventory agent read the datasheet"), recipes' `# Inventory: ...` opener, recipe 5's "If an inventory value fits one of these" all assume the reader has just produced an "inventory" via the skill. A human reading the datasheet directly has no inventory — they have a PDF. The page needs one paragraph clarifying:

- Who runs this workflow (skill-driven vs human-driven)
- What "inventory" means
- Where the human-only path differs

### WARNING — Schema vocabulary is undefined on this page

Terms used without on-page definition or hover-link:

- SpecBand, AccuracySpec, ResolutionSpec, RangeSpec
- "capability-level" vs "board-level" vs "catalog_entry"
- "signal", "condition", "control", "attribute" as schema slots
- "function", "direction"
- "when keys", "siblings"
- "discrete options" vs "continuous range"
- "guaranteed / typical / nominal" qualifiers (never mentioned but appear in every recipe pattern)

The lede points to `catalog-schema.md` for definitions, which is the right call — but a one-line glossary box at the top ("Schema vocabulary used: ...") would let the page stand alone for a quick lookup.

### SUGGESTION — Each recipe needs a "when you see this" header

The H2s describe the SOLUTION ("Accuracy by frequency band → SpecBands") which is good for a cookbook reader who knows the schema. A reader who only knows the datasheet ("I have a table where accuracy varies by frequency, where does this go?") needs the reverse lookup. Recipe 1's solution-named header already does this well. Recipes 3, 4, 9, 11 could be rephrased to lead with the datasheet shape rather than the schema location, e.g.:

- 3: "Dual-unit values → two attributes" — good, datasheet-shape first.
- 9: "Board-level vs capability-level attributes" — schema-vocabulary first; consider "Where does this attribute live: board or capability?"

### SUGGESTION — Numeric jargon in recipe 2 will trip non-skill readers

Recipe 2's RIGHT block uses `acquisition_mode: {min: 0, max: 0}` to match a string-valued control whose `options: ["single", "automatic"]`. A human reader will (rightly) ask "why am I matching 0..0 against an option list?" That's a real bug (see Accuracy) but also a reader-confusion bug.

---

## Accuracy

I cross-checked claims against:

- `src/litmus/models/capability.py` (CATALOG_SCHEMA_VERSION = "1.0", SpecBand, Signal, Condition, Control, Attribute, AccuracySpec, ResolutionSpec, ConditionKey)
- `src/litmus/models/catalog.py` (InstrumentCatalogEntry)
- `src/litmus/store.py` (catalog loader + deep merge)
- Real catalog YAMLs under `examples/06-station-catalog/catalog/`, `examples/07-profiles/catalog/`, `src/litmus/catalog/generic/`

### CRITICAL — Recipe 2 demonstrates an internally inconsistent SpecBand `when` shape

Recipe 2's RIGHT block has:

```yaml
controls:
  acquisition_mode:
    options: ["single", "automatic"]   # string options
```

but the bands `when` clauses match `acquisition_mode` with numeric ranges:

```yaml
when:
  acquisition_mode: {min: 0, max: 0}
  fundamental_frequency: {min: 20, max: 100, units: Hz}
```

`band_matches()` in `capability.py:569-597` will compare the user-set string value (e.g. `"single"`) against a `RangeSpec`. The relevant branch is `if isinstance(spec, RangeSpec): if isinstance(val, (int, float)): ...` — when `val` is the string `"single"`, BOTH min/max checks are skipped and the RangeSpec matches anything. So the recipe as written would have every band match every acquisition mode, silently producing wrong specs.

The correct shape for the recipe's intent is either:
- `acquisition_mode: "single"` (string equality), or
- `acquisition_mode: {values: ["single"]}` (ListSpec)

This is the cookbook's flagship example for multi-row tables — the bug needs fixing before any reader copies it.

### WARNING — `# No specs[] needed` comment in recipe 14 contradicts the field name

Recipe 14's closing comment:

```yaml
# RIGHT — just use top-level, no SpecBand needed:
signals:
  distortion:
    accuracy: {absolute: 0.8}
    # No specs[] needed — accuracy doesn't vary
```

The schema field is `bands:`, not `specs:` (capability.py:247: `bands: list[SpecBand] | None`). The comment is leftover from an older schema name. Replace `specs[]` with `bands` (or `bands:`).

Note that `store.py` line 948-954 still has stale code that deep-merges on a `"specs"` key — that's a separate codebase bug but it indicates this naming drift happened recently. Worth noting in any followup.

### WARNING — Recipe 9 documents `catalog_entry.attributes` as a YAML wrapper that doesn't exist on disk

All recipes that say `catalog_entry.attributes:` (recipe 9, the prose in recipe 10 implicitly, and the schema reference) describe a wrapper that does NOT appear in any real catalog YAML in the repo. Real catalogs (e.g. `examples/06-station-catalog/catalog/generic_dmm.yaml`, `src/litmus/catalog/generic/*.yaml`) are flat:

```yaml
id: generic_dmm
manufacturer: Generic
...
channels: {...}
capabilities: [...]
# (attributes: would go here at root, not under catalog_entry:)
```

The store does sniff for a `catalog_entry:` wrapper key (`store.py:198`) but no shipped or example catalog uses it. The Pydantic model `InstrumentCatalogEntry` (`catalog.py`) defines `attributes:` as a top-level field, which serializes flat.

This is shared with `catalog-schema.md` so the inconsistency is documentation-wide. Either:
- The docs are wrong and should write `attributes:` at YAML root.
- The codebase is wrong and should require the wrapper.

Either way the cookbook should match reality. Recommend: remove the `catalog_entry:` wrapper from recipes 9 (and verify the prose in recipe 3 / 10 / the introductory schema doc).

### WARNING — Recipe 5 attribute name encodes the accuracy type, contradicting its own rule

Recipe 5 says:

> **RIGHT** — if no frequency signal exists (subsystem spec), keep as attribute but the name must NOT encode the accuracy type:
> ```yaml
> attributes:
>   frequency_accuracy: {value: 0.01, units: pct_reading}
> ```

But `frequency_accuracy` literally encodes "accuracy" in the name (and `units: pct_reading` smuggles the type into the units field, where `pct_reading` is not a real unit — it's a model field name from `AccuracySpec`). The recipe contradicts itself: it labels the shape RIGHT, while a strict reading of "name must NOT encode the accuracy type" rules out `frequency_accuracy` too.

Either:
- Clarify the rule (e.g. "name may say `accuracy` but must not encode `_pct_reading_` or `_per_range_`")
- Or pick a different field name (e.g. `frequency_accuracy_pct` with `units: pct`)

Also: `units: pct_reading` is a fabrication — units in Litmus are conventional unit strings (V, Hz, ohm, pct, dB) per the other recipes; `pct_reading` is the AccuracySpec field name, not a unit.

### SUGGESTION — Qualifier (guaranteed / typical / nominal) is never shown in any recipe

The schema has a `qualifier` field on Signals, Attributes, and SpecBands (`capability.py:206, 248, 350`), and the schema doc dedicates a section to it. The cookbook has 14 recipes and never uses `qualifier:`. Real datasheets always distinguish guaranteed from typical specs — that's the primary use of the field. Recipe 1 (multi-band accuracy on AC voltage) is the natural place to demonstrate a typical-vs-guaranteed split.

### SUGGESTION — Recipe 10 uses bare scalar `when: {range: 100}` without showing the units-inheritance rule

The recipe writes:

```yaml
bands:
  - when: {range: 100}
    value: 0.001
```

That's valid (units inherit from the parent control/condition per `capability.py:505-515`), but the recipe never mentions this — a reader copying the pattern won't know whether the `100` is ohms / volts / amps, or that the inheritance even exists. Add a one-line comment: `# 'range' here is the sibling control whose units (ohm) are inherited.` Also: the example's outer attribute is `test_current` but there's no `controls.range:` declared in the recipe, so the `when: {range: 100}` reference is dangling. The `_validate_band_when_keys` validator (`capability.py:455-528`) will raise on this. Either show the sibling `controls.range:` declaration or restructure.

---

## Gaps

Things a reader will look for and not find:

### WARNING — No recipe for "datasheet says values X, Y, Z for option A, B, C" with discrete (non-numeric) when-keys

Recipe 2's bug aside, the cookbook never demonstrates the supported `when` shapes for non-numeric matches:

- `when: {coupling: "AC"}` — string equality
- `when: {impedance: {values: [50, 600], units: ohm}}` — ListSpec
- `when: {frequency: {value: 100000000, units: Hz}}` — PointSpec
- `when: {trigger_enabled: true}` — bool equality

The schema reference shows these in a table; the cookbook is the natural place to show each one in a worked recipe. Without them, the page only covers the RangeSpec case.

### WARNING — No recipe for option codes / variant instruments

Schema reference §"Instrument Variants (Option Codes)" calls out that variant-gated specs are modeled as controls with SpecBand `when` clauses, then post-processed into separate catalog entries via `base:` inheritance. That's a substantial workflow and a very common datasheet shape (every datasheet has option codes). No cookbook recipe demonstrates it. Add a recipe — perhaps "15. Option-code-gated specs → SpecBand `when` (pre-split)".

### WARNING — No recipe for typed channels / ChannelTopology

The page is silent on `channels:` topology entirely (terminals, connector, ground, connector_pin). Channels are the other half of a catalog entry — every catalog has them, and the rules ("every channel referenced in capabilities MUST exist in `catalog_entry.channels`") are non-obvious. Add a recipe showing:

- Single-input DMM (`hi`, `lo`, binding_post, shared)
- 4-wire (`hi`, `lo`, `sense_hi`, `sense_lo`, binding_post, floating)
- Range syntax for multi-channel ADC (`ai[0:7]`)

### SUGGESTION — No recipe for `units:` mismatch between signal `range` and `value`

A common datasheet shape: range is 0..100% but the typical value is given as 0.1 (a fraction). The cookbook doesn't address unit-system mismatches between `range.units` and `value` / `accuracy.units` outside recipe 4. A worked example for "datasheet gives range in dBm but typical in W" would help.

### SUGGESTION — No recipe for direction = bidir / transform

Every recipe uses `direction: input`. Real catalogs include `output` (PSU, FGen), `bidir` (SMU), and `transform`. A short recipe naming when each direction applies would close the gap.

### SUGGESTION — No recipe for the "same instrument measures multiple things" decision

The schema doc's "Same quantity, different roles" table is the right call-out. The cookbook could mirror it with a worked recipe: "An RF source's output frequency is a signal; the same RF source's reference clock is also a signal but on a different capability". The decision tree is rule-of-thumb in the schema; a worked recipe makes it concrete.

---

## Cross-links

### CRITICAL — Forward link to `models.md` for "SpecBand" is mis-targeted

The schema reference says `[SpecBand](models.md)` (catalog-schema.md:30) and the cookbook references SpecBand without linking it at all. `models.md` does define SpecBand (line 53, 428, 434, 490) but there's no in-page anchor — the link sends the reader to the top of a long ERD page. The cookbook should:

- Link `SpecBand` on first mention (currently zero links to the model)
- Use an anchored link if one exists (`models.md#specband`)

The CRITICAL is for the cookbook's zero links to the model definitions for SpecBand / AccuracySpec / ResolutionSpec / RangeSpec — all four are named in recipe 5 without a single link.

### WARNING — No back-link from the page footer to related pages

Most reference pages in this repo end with "See also" links. This page ends cold on recipe 14. Add a footer:

- `[Catalog schema](catalog-schema.md)` — field-by-field reference
- `[concepts/capability-model](../concepts/capability-model.md)` — why the model is shaped this way
- `[reference/models](models.md)` — Pydantic ERD including SpecBand
- The `/catalog-from-datasheet` skill (if user-discoverable)

### WARNING — Missing link from `concepts/capability-model.md`

`docs/concepts/capability-model.md` discusses the capability schema at length and never links to the cookbook. The cookbook is exactly the operational follow-on to that concept page. Add a "Practical recipes: see [catalog-cookbook](../reference/catalog-cookbook.md)" line at the bottom of the concept page (this is a cross-link gap from the OTHER direction, but it's the cookbook's discoverability that suffers).

### WARNING — Recipe 7 references "inventory's USER-SELECTABLE SETTINGS" without linking to the skill

That phrase is from the `/catalog-from-datasheet` skill workflow. The page assumes the reader has the skill loaded. Either:

- Link to the skill doc (if there's an Anthropic-style internal path that's user-readable), or
- Inline a short definition: *"The skill's extraction step produces a USER-SELECTABLE SETTINGS table with an Applies To column — that column lists which capabilities each control belongs to."*

### SUGGESTION — No links to canonical `ConditionKey` vocabulary

Recipes use `frequency`, `acquisition_mode`, `fundamental_frequency`, `num_frequencies`, `nplc`, `coupling`, etc. The canonical `ConditionKey` enum (`capability.py:376`) is the source of truth and is documented in `concepts/capability-model.md:210`. The cookbook should link to that list so an author knows which condition names are canonical (`acquisition_mode` yes; `fundamental_frequency` and `num_frequencies` no — those are author-coined names).

### SUGGESTION — Recipe 12 ("Condition ranges must match the inventory") has no link to the inventory step

The whole recipe hinges on a concept ("the inventory") that's defined elsewhere. Same fix as the recipe-7 cross-link gap — one link to the skill or a one-line in-page definition.
