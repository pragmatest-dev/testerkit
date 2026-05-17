# Page audit: docs/concepts/products.md

**Quadrant:** Concepts / Explanation (product specifications — pins, characteristics, bands, spec-driven testing)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 2 | 3 |
| Audience | 0 | 3 | 2 |
| Accuracy | 2 | 5 | 2 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **4** | **19** | **13** |

---

## Ordering

**WARNING — Section sequence buries the "why" under the "how"**
The page opens with a 40-line YAML dump (lines 7–45) before any prose unpacks what a "Product Specification" *is* or *why it exists*. For a Concepts page, the reader should first learn the mental model (product → pins + characteristics → traceability) and only then see a fleshed-out example. As written, the page reads like a Reference entry that started without an intro.
Suggested order: §intro → §the mental model (pins + characteristics) → §full example → §pin types deep dive → §characteristics deep dive → §conditions/bands → §signal groups → §variants → §minimal spec → §loading.

**WARNING — "Minimal Spec" lands after the most complex section**
"Specifications with Conditions" (multi-band `when:` clauses, lines 126–162) is the most advanced material on the page, but it precedes "Minimal Spec" (lines 187–209). Concept pages should scaffold up; here the reader has to walk back down to the simple example after wading through banded specs. Either move "Minimal Spec" right after the first example, or rename it "Reference: the smallest valid product" and acknowledge the reversal.

**SUGGESTION — "Part Numbers" sits between schema and inheritance**
"Part Numbers" (line 211) is operational/traceability content, not schema. It interrupts the schema flow (characteristics → conditions → signal groups → minimal → **part numbers** → variants). Group it with the loading/runtime section near the bottom, or merge it into the opening "what a product is" pitch since `part_number` is one of the very first YAML keys shown.

**SUGGESTION — "Loading Products" precedes "Next Steps" but the in-code comment introduces a new claim**
The Python snippet on line 261 — "Nominal lives on each SpecBand, not on the characteristic itself" — is the single most surprising design decision on the page. Burying it as a code comment in the penultimate section is poor ordering. Promote it into the "Specifications with Conditions" section so the reader meets the claim where the band schema is being explained.

---

## Voice

**WARNING — Slips into Reference/How-to voice repeatedly**
Concept pages explain a model; Reference pages enumerate fields; How-to pages give instructions. This page mixes all three. Examples of voice slippage:
- Lines 156–161 are a Reference table of `accuracy` fields ("`pct_reading` — Percentage of the measured value") with no conceptual framing.
- Line 213 ("Overridable via `--dut-part-number` on the CLI. This enables yield analytics filtering by part number.") is How-to.
- Lines 246–250 ("Inheritance rules: ... Max inheritance depth: 5 levels.") is Reference.
Either commit to Concepts and link out to `reference/configuration.md` for the field-level enumerations, or restructure as a hybrid and own it explicitly in the intro.

**WARNING — Imperative "you" without a "you are doing X" frame**
Lines 49 ("Pins represent…"), 78 ("Characteristics are measurable properties"), 87 ("The `direction` field describes the DUT's perspective") read fine, but lines 213 ("Overridable via…") and 254 ("In Python:") drop into bare imperative without setup. Concepts pages should narrate ("When you load a product at runtime, the resolved Pydantic model …") rather than instruct.

**SUGGESTION — Bold-on-first-mention is inconsistent**
"**Pins**" is bolded on line 49, "**Characteristics**" on line 78, "**specs**" (calling out a different term: SpecBand) on line 128 — but "**Product**" gets it on line 3 and not again, "Pin Types", "Direction", "Specifications with Conditions" never get the bold-on-first-mention treatment. Pick one rule (typically: bold the term the *section* defines, on first use only) and apply it consistently.

**SUGGESTION — "DUT" is introduced without expansion**
Line 3 uses "device" but line 49 jumps to "DUT" without ever spelling out *device under test*. A Concepts page is the right place to seed vocabulary. Add a parenthetical on first use, or define it in the intro.

**SUGGESTION — Avoid the "what is X?" rhetorical pattern in headings**
The page leans on bare-noun headings ("Pins", "Characteristics", "Specifications with Conditions") which is fine, but the prose under them often starts with the same noun ("Pins represent…", "Characteristics are…"). Vary the opening to avoid the textbook-glossary cadence.

---

## Audience

**WARNING — Assumes the reader already knows the platform's mental model**
Line 3 ("A Product is what you're testing — a PCB, module, or device.") is the only orientation the reader gets. The page never explains *why* products exist as YAML, who reads them (test engineer? configuration owner? operator?), or how they relate to stations / fixtures / tests. A reader landing here from a search engine has no idea this is part of a chain — `concepts/architecture.md` covers it, but no link is offered until "Next Steps" at the bottom.

**WARNING — Hardware-test vocabulary used without grounding**
Terms used without definition: "DUT" (line 49), "net" (line 53 — used as a YAML field with no explanation that this is the schematic net name from EE workflow), "bus", "I2C/SPI/UART" (line 164 — assumes EE-protocol fluency), "guardband" (not used here but referenced in the linked `spec-driven-testing.md`). For a Concepts page that test engineers *and* software-leaning users will read, these need at-least-once glossary support.

**WARNING — The four-dimensional capability model is hidden**
`ProductCharacteristic` extends `Capability`, which has four parameter dicts: `signals`, `conditions`, `controls`, `attributes` (see `capability.py:444–451`). This page treats characteristics as having only `function`, `direction`, `units`, `pins`, and `bands`. Readers who graduate to instrument matching, conditions, or controls will hit this model with no preparation. At minimum, a one-paragraph aside saying "characteristics inherit from the same Capability shape used by instruments — see [Capability model](capability-model.md) for the full structure" is needed.

**SUGGESTION — No persona signposting**
The page never says "if you're a test engineer, start here; if you're integrating an existing test suite, the relevant section is variants". Concept pages benefit from a one-line "who this matters to" framing at the top.

**SUGGESTION — "In Python" snippet (line 254) targets a different audience than the rest of the page**
The whole page is YAML-and-vocabulary content for spec authors; the closing snippet pivots to API consumers. Either flag this with "If you're writing test code or tooling against products programmatically, here's the loader API" or move it to the reference.

---

## Accuracy

**CRITICAL — `pct_reading` units are misrepresented**
Lines 33–34, 43–44, 113, 122, 142, 150, 208, 243: the page shows `pct_reading: 10` and labels it "±10% tolerance", `pct_reading: 5` as "±5%", and `pct_reading: 3` as "tighter tolerance for industrial." Per `capability.py:115–132` (`AccuracySpec.total_uncertainty`), the formula is `(pct_reading / 100) * abs(value)`. So `pct_reading: 10` is literally 10 percent, which is correct. **However**, real-world instrument specs and the catalog examples use *decimal* percent (e.g., `pct_reading: 0.0035` for a 6½-digit DMM — see `capability.py:226`, `models/catalog.py:52`). The page's "5", "7", "10" values are wildly loose by instrument-spec convention and will confuse readers who cross-reference catalog YAMLs. Either drop in a one-line note ("values shown here are loose to make the example readable; production specs are typically 0.001–1.0") or use realistic numbers.

**CRITICAL — `pins: [VIN]` example contradicts the actual model and other docs**
The first big example (line 30: `pins: [VIN]`) uses the list form, but every real example product YAML uses the singular `pin: TP_VIN` (see `examples/05-product-spec/products/buck_3v3.yaml`). Per `product.py:152–176` both are valid, but they have different priority semantics (`pin` wins over `pins`). The page never explains the four-way choice (`pin`, `pins`, `net`, `signal_group`), never says `pin:` exists as the simpler alternative, and the `capabilities.md` sibling page does mention the four-way choice explicitly (line 23). Pick one canonical form for the lead example and explain when to deviate.

**WARNING — `bands` field referred to as `specs` in inherited docstring**
Line 128 says "Each characteristic has one or more **specs** (SpecBands)" but the actual YAML key in every example on the page is `bands:`. The docstring in `capability.py:182` uses `specs:` as an example YAML key — that's the *legacy* docstring nomenclature, but the actual model field on `Capability` is `bands` (line 453). This page should call the user-facing key `bands` and explain that each band is a `SpecBand`, not say "specs (SpecBands)" which conflates the YAML key with the type name.

**WARNING — `function: dc_voltage` listed under "Domain" terminology**
Line 81 calls the second bullet "**Domain** — What physical quantity? (voltage, current, etc.)" but the actual YAML key is `function:` and the enum is `MeasurementFunction` (see `enums.py:24`). "Domain" appears nowhere in the code. Use the real name — `function` — and avoid inventing a synonym readers will search for in vain.

**WARNING — Direction table omits `transform`**
Line 89–93 lists `input`, `output`, `bidir` but the `Direction` enum (see `enums.py:15–21`) also includes `transform` (signal-path components like amplifiers, filters, mixers). Either say "the main three" and add a footnote, or include all four. Currently a user with a passive amplifier or filter in their product spec would silently hit the missing case.

**WARNING — `pin: VOUT` vs `pins: [VOUT]` inconsistency within one example**
Lines 99–124 ("Multiple Characteristics Per Pin") declare the pin map with `pins:` (singular block at top level) and then use `pins: [VOUT]` (the list form) for both characteristics. Reader is left to figure out that the top-level `pins:` map is unrelated to the characteristic-level `pins:` list. Worth a one-line "note the two `pins` keys are different namespaces".

**WARNING — `load_product` signature does not match example invocation**
Line 259 shows `load_product("products/power_board.yaml")` (string), but `store.py:618` declares `load_product(path: Path, ...)`. It works at runtime because `open()` accepts strings, but the example is type-incorrect for a project that prides itself on Pydantic/typing discipline. Use `Path("products/power_board.yaml")` and `from pathlib import Path`. Also, `get_product(product_id)` is the more typical entry point (loads by id rather than by path) — worth surfacing.

**SUGGESTION — "Max inheritance depth: 5 levels" is unverified by inspection**
Line 250 cites a max depth of 5. The `_load_with_inheritance` function in `store.py:568` takes a `depth=0` start but the limit isn't visible in the slice I read. If it's enforced (and it appears to be from `depth=depth+1` recursion), surface the constant name; if not, the claim is documentation drift.

**SUGGESTION — Signal-groups example has no characteristic referencing it**
The signal-groups YAML (lines 167–185) defines `i2c_main` but no characteristic in the page ever references it via `signal_group: i2c_main`. A reader trying to wire a characteristic to the bus has no example. Add a `characteristics:` block that uses `signal_group: i2c_main` to close the loop.

---

## Gaps

**CRITICAL — No mention of how a product participates in matching / capability resolution**
The page's whole reason to exist is "specs define what needs to be tested" but it never connects to capability matching — the *purpose* of having a structured spec. A reader can write a perfect product YAML and have no idea what happens when `pytest --product=foo.yaml` runs: which fixture wins, how `direction` pairs with the instrument's opposite direction, how `bands` get evaluated against the current parametrize row. A 4–6 sentence "Where products go from here" section linking to `concepts/capabilities.md`, `how-to/spec-driven-testing.md`, and `concepts/architecture.md` is missing.

**WARNING — `direction` semantic is given a table but no example of the wiring**
Line 87–93 tells the reader "input means the DUT receives", but never says "and therefore a DUT input characteristic gets paired with an instrument *output* capability." That direction-pairing rule is the load-bearing concept; without it, the table is trivia.

**WARNING — No discussion of `function` choices beyond `dc_voltage` / `ac_voltage`**
`MeasurementFunction` has 30+ values (DMM, scope, power, RF, optical, etc.) — see `enums.py:24` onward. The page implies `dc_voltage` is the menu. At least a "see [reference/configuration.md] for the full enum" pointer is needed.

**WARNING — `conditions` / `controls` / `attributes` dicts on characteristics are not mentioned**
`ProductCharacteristic` inherits all four parameter dicts from `Capability`. The page only ever shows `bands`. A test author who needs to declare an operating condition (e.g., "this characteristic is only valid at 25°C ±5") via the `conditions:` dict has no idea it exists. Either say "this page only covers the common case; full structure is in [capability model]" or include a real `conditions:` example.

**WARNING — `datasheet_ref` / `datasheet` / `schematic` / `driver` are silent**
Top-level `datasheet`, `schematic`, `driver` (see `product.py:258–260`) and per-characteristic `datasheet_ref` (line 159) are absent from the page. They're how a product spec ties back to source documents and to a Python driver class — fundamental for traceability and the "driver-class-per-product" pattern.

**SUGGESTION — No coverage of "where does the spec live in the run record"**
A Concepts page should at least gesture at how the product spec leaves a footprint in the parquet/event log. Line 213 mentions `dut_part_number` and yield analytics, but nothing about `spec_ref`, `dut_pin`, or the traceability columns covered in `how-to/spec-driven-testing.md`.

**SUGGESTION — No mention of `--product=<id>` vs `--product=<path>`**
Per `docs/how-to/spec-driven-testing.md:8`, the CLI accepts both an id and a path. That's the test author's first contact with the product spec at runtime and worth one sentence in this concepts page (or at minimum a link).

---

## Cross-links

**CRITICAL — Page does not link to its closest Concepts siblings inline**
The only links are at the very bottom in "Next Steps." Within the body of the page, claims like:
- "Characteristics are measurable properties" (line 78) — no link to [capability-model.md]
- "Direction" (line 86) — no link to anything explaining input/output pairing
- "Specifications with Conditions" (line 126) — no link to [capability-model.md] or [how-to/spec-driven-testing.md]
- "Loading Products" (line 252) — no link to the loader's role in `pytest_plugin` or to [reference/api.md]
For a Concepts page in a tightly-cross-linked docs tree, inline anchors at the point of mention are table stakes.

**WARNING — `ProductContext` is mentioned nowhere on the page but linked-to as the product concepts page from other docs**
`docs/how-to/limits.md:42` and `docs/how-to/spec-driven-testing.md:3` describe `ProductContext` as "the loaded-product container … see [concepts/products.md]". This page never defines or even mentions `ProductContext`. Either add a "The ProductContext at runtime" section that mirrors the inbound expectation, or fix the linking docs to point at a different anchor (e.g., `reference/api.md`).

**WARNING — "Configuration Reference" link target may not deliver**
Line 273 promises `[Configuration Reference](../reference/configuration.md) — YAML schema details`. Confirm this file (a) exists, (b) actually contains the product YAML schema field-by-field. A search of the repo shows `reference/configuration.md` exists, but the page should also link to `reference/models.md` (which exposes the Pydantic models authoritatively).

**WARNING — Inbound links from tutorials reference sections that are not anchored**
`docs/tutorial/00-quickstart.md:21` links to `concepts/products` as "Product spec — Describes the device under test." `docs/tutorial/09-production.md:61` links here to disambiguate the fixture `pins` from the product `pins:`. Neither link uses an anchor (`#pins`, `#part-numbers`, etc.) — partly because the page's anchors are not stable. Add explicit anchors to the major headings and update inbound links.

**SUGGESTION — Missing cross-link to `how-to/spec-driven-testing.md`**
The page describes spec-driven test data but never points at the how-to that walks through it end-to-end. Add a "See also" line under "Specifications with Conditions" or in the intro.

**SUGGESTION — Missing cross-link to `concepts/architecture.md`**
Per `concepts/index.md:9`, `architecture.md` is "system-level view of products, stations, fixtures, and runs" — exactly the orientation this page is missing. Link from the intro paragraph.
