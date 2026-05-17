# Page audit: docs/tutorial/06-specifications.md

**Quadrant:** Tutorial (step 6 of 10 — product specifications)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 3 | 1 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 3 | 2 |
| Accuracy | 0 | 3 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 0 | 2 | 2 |
| **Total** | **1** | **15** | **11** |

---

## Ordering

### Findings

**[WARNING] Primary workflow taught last, inferior workflow taught first**

The page teaches manual limit derivation (compute `low`/`high` arithmetic from `pct_reading`, hard-code them in the sidecar) before the reader ever sees the platform-native `characteristic: + tolerance_pct:` pattern. The platform-native pattern is never shown at all in this step — it only appears in `how-to/spec-driven-testing.md`. The sequence within the page (spec → manual math → hard-coded limits → guardband calculation) teaches an inferior manual workflow in detail and omits the automated workflow entirely. The actual example in `examples/05-product-spec/tests/test_rail.yaml` uses `characteristic: rail_3v3, tolerance_pct: 2`, not hard-coded `low`/`high` values.

**[WARNING] `bands:` without `when:` used before `when:` is explained**

The product spec at the top of the page (lines 25–64) uses `bands: [{value: 3.3, accuracy: {pct_reading: 5}}]` with no `when:` key. The `when:` key is not introduced until the "Conditions" section near the bottom of the page. A reader encountering `bands:` at line 26 with no context has no idea what a "band" is, why there is a list of them, or whether `when:` is required. The conceptual dependency is inverted — the simpler unconditional case appears before the reader understands the `bands:` mechanism that makes it meaningful.

**[WARNING] "Complete Example" is a near-duplicate of earlier material**

Sections "The Product Spec" (top of page) and "Complete Example" (near bottom) contain nearly identical YAML. The complete example adds the sidecar and Python function but uses different (guardband) limit values without explanation of the change. A first-time reader will re-read essentially the same YAML twice without clear benefit. The complete example should either integrate all earlier concepts (showing the `characteristic:` workflow, the `when:` bands, and the guardband) or be removed.

**[SUGGESTION] "What You Learned" appears before the Traceability Chain diagram**

The "Traceability Chain" ASCII diagram (lines 253–258) appears after the "What You Learned" bullet list but before "Next Step". The traceability diagram is a conceptual synthesis of the whole step — it belongs before the summary bullets so it reinforces what was just taught, not after the reader has mentally closed the chapter.

---

## Voice

### Findings

**[WARNING] Section headers describe rather than instruct**

"What the Spec Defines", "Pins", "Characteristics", "Product Identity" read as reference-section labels, not tutorial instructions. In a tutorial, headers should guide action: "Understand what the spec declares", "Map pins to physical connectors", "Describe measurable characteristics". The current headers make the page feel like a reference section embedded in a tutorial.

**[SUGGESTION] "Sweep these conditions from the sidecar" is ambiguous**

Line 177: "Sweep these conditions from the sidecar:" — "sweep" is used as a verb here but it collides with the Litmus-specific noun `sweeps:` (the YAML key). A reader reading this before fully internalizing the `sweeps:` concept may read "sweep conditions from the sidecar" as "remove them from the sidecar". Prefer: "Drive these conditions via sidecar sweeps:".

**[SUGGESTION] Informal exclamation in code comment**

Line 124: `spec_ref: "output_voltage @ tolerance_pct=5"  # Traceability!` — the exclamation mark is enthusiastic but inconsistent with the rest of the codebase's tone. The how-to docs and reference YAML use factual comments. Remove the `!` or rephrase the comment as a factual annotation, e.g., `# links this limit back to the spec`.

---

## Audience

### Findings

**[WARNING] Two conflicting workflows presented without guidance on which to use**

The page teaches two ways to connect a product spec to test limits:
1. Manual: Compute `low`/`high` from the spec's `pct_reading` value and hard-code them in the sidecar with a hand-typed `spec_ref` string.
2. Platform-native: (implied only) use `characteristic: output_voltage, tolerance_pct: 5` in the sidecar to let the platform resolve limits automatically.

Only workflow 1 is actually shown. Workflow 2 is never demonstrated or even mentioned. A reader who completes this step will implement workflow 1 for all their tests, then discover workflow 2 exists much later (or never). They should be pointed to the right path.

**[WARNING] `bands:` without `when:` is unexplained for a first-time reader**

The first product YAML example uses `bands: [{value: 3.3, accuracy: {pct_reading: 5}}]` with no `when:` key. The model accepts this (empty `when: {}` means unconditional), but the tutorial never states this. A reader who knows that `SpecBand.when` is the matching predicate will wonder whether the omission is intentional (unconditional) or a typo. This should be explained inline: "The `when:` key is optional — omitting it means this band always applies."

**[WARNING] Conditions section does not connect to the limit resolution mechanism**

The "Conditions" section (lines 155–186) shows `bands:` with `when: {temperature: 25, load: 0.5}` and then shows a sidecar with `sweeps: [{temperature: [25, 85]}]` and `limits: # Different limits per condition resolve from the spec at runtime`. The reader is not told:
- How the runtime knows which band to pick (it matches the active vector params from `sweeps:` / `@pytest.mark.parametrize`).
- That a `characteristic:` reference in the sidecar's `limits:` block is required to trigger this automatic resolution.
- What `# Different limits per condition resolve from the spec at runtime` actually means mechanically.

A test engineer reading this would not be able to implement condition-dependent limits from this description alone.

**[SUGGESTION] `id:` field purpose not explained**

Line 27: `id: power_board` appears in the YAML but the tutorial never says this `id` is what you pass to `--product=power_board` on the CLI. Without this connection the reader cannot use the product spec they just defined.

**[SUGGESTION] `pins: [VOUT]` vs `pin: VOUT` distinction not explained**

The tutorial uses `pins: [VOUT]` (list form) on characteristics. The real working example (`examples/05-product-spec/products/buck_3v3.yaml`) uses `pin: TP_VOUT` (singular string). Both are valid (`ProductCharacteristic` has both `pin: str | None` and `pins: str | list[str]`), but the tutorial uses the plural form without explaining the difference. A reader who copies the singular form from another source may be confused.

---

## Accuracy

### Findings

**[WARNING] The "Conditions" sidecar example implies automatic limit resolution without `characteristic:`**

Lines 179–186 show:
```yaml
limits:
  # Different limits per condition resolve from the spec at runtime
```
This is misleading. Automatic condition-based limit resolution from the product spec requires a `characteristic:` reference in the sidecar limit entry. An empty `limits:` block (or one with only a comment) does not trigger any automatic resolution — it means no limit is applied and the measurement records unchecked. The claim "resolve from the spec at runtime" without a `characteristic:` key is inaccurate.

Source: `src/litmus/execution/sidecar.py` `_resolve_single()` — only dispatches to `spec_ctx.get_limit()` when `char_id is not None` (i.e. `characteristic:` is set).

**[WARNING] Manual `spec_ref` string taught as the traceability mechanism**

Line 124: `spec_ref: "output_voltage @ tolerance_pct=5"  # Traceability!`

When using the `characteristic:` workflow, `spec_ref` is auto-generated by the platform as `char_id` (see `sidecar.py` line 226: `spec_ref=char_id`). The tutorial teaches the reader to hand-type a `spec_ref` string as if it is the primary traceability mechanism. This is technically valid (the field accepts a free-form string) but it teaches the manual fallback rather than the platform behaviour. A reader using the `characteristic:` workflow will find that `spec_ref` is already set correctly without their intervention.

**[WARNING] Guardband presented only as a manual arithmetic exercise**

Lines 130–151 show the reader computing `low: 3.152` and `high: 3.449` by hand ("Calculate guardbanded limits in the sidecar"). The platform supports:
- `--guardband=10` CLI flag (applies to all spec-derived limits in the session)
- `guardband_pct: 10.0` in profile config
- `guardband_pct:` field in a `MeasurementLimitConfig` entry

None of these are mentioned. The tutorial teaches the hardest, least maintainable approach (hard-coded arithmetic) as if it is the intended method.

Source: `src/litmus/execution/sidecar.py` `resolve_limit()` line 257: `guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0`; `src/litmus/execution/limits.py` (referenced in `_resolve_single`).

**[SUGGESTION] `pct_reading` arithmetic is correct but the formula is implicit**

The tutorial states "Low: 3.3 × (1 - 0.05) = 3.135V" without explaining the `pct_reading` semantics explicitly. `AccuracySpec.total_uncertainty()` computes `(pct_reading / 100) * abs(value)`, so uncertainty = 0.165V, and limits are 3.3 ± 0.165. The arithmetic is correct but the reader may not understand why `pct_reading: 5` translates to ±5% of the nominal reading. A one-line note like "`pct_reading: 5` means ±5% of the measured value (not ±5% of the full range)" would prevent confusion with `pct_range`.

**[SUGGESTION] `pins: [VOUT]` refers to a pin key but VOUT is undefined in the characteristics section**

In the exploded "Characteristics" section (lines 94–104), the snippet shows:
```yaml
characteristics:
  output_voltage:
    ...
    pins: [VOUT]           # Measured at this pin
```
But the `VOUT` key is only defined in the `pins:` section of the product YAML, shown separately. Within the snippet the reference to `VOUT` floats without context. The full product YAML at the top of the page defines `VOUT` — but a reader reading only the "Characteristics" sub-section (e.g., following a direct link) will see an undefined reference.

---

## Gaps

### Findings

**[CRITICAL] The `--product=` CLI flag is never mentioned**

The page teaches the reader how to define a product spec YAML but never tells them how to activate it at test time. The workflow requires passing `--product=power_board` (or `--product=products/power_board.yaml`) to pytest. Without this, the `verify` fixture cannot resolve spec-driven limits — it simply has no product context. A reader who completes step 6 cannot use what they just built.

This is covered in `docs/how-to/spec-driven-testing.md` under "Run with `--product=<id>`" and in `docs/tutorial/08-capabilities.md` (probably). Neither is linked from step 6. The gap leaves the reader with a product spec they cannot connect to their tests.

**[WARNING] The `characteristic:` + `tolerance_pct:` sidecar pattern is absent**

The central platform-native workflow for linking product specs to test limits is `characteristic: <id>` + `tolerance_pct: <N>` in the sidecar's `limits:` block. This pattern:
- Automatically reads the nominal value from the product spec.
- Computes `low`/`high` limits at measurement time.
- Stamps `characteristic_id` and `spec_ref` on the measurement row.
- Applies the session's `guardband_pct` automatically.

The tutorial teaches only the manual alternative (hard-coded `low`/`high` + hand-typed `spec_ref`). The reader finishes step 6 having learned the pattern that bypasses most of what the spec layer provides.

**[WARNING] No explanation of how condition matching works at runtime**

The "Conditions" section shows condition-dependent `bands:` in the product YAML and a `sweeps:` block in the sidecar, but the mechanism linking them is not explained. Specifically: the platform picks the matching `SpecBand` by comparing the active vector parameters (from `sweeps:` expansion or `@pytest.mark.parametrize`) against each band's `when:` clause using `band_matches()`. Without this explanation the reader cannot debug cases where the wrong band is selected or no band matches.

**[WARNING] `datasheet_ref` field on characteristics is not mentioned**

`ProductCharacteristic` has a `datasheet_ref: str | None = None` field for direct citation of the datasheet section that sourced the spec. This is a primary traceability field — it closes the chain from test result back to the physical datasheet. It is shown in `docs/how-to/spec-driven-testing.md` (`datasheet_ref: "Section 7.2"`) and `docs/concepts/products.md`. Its absence from the tutorial means readers miss a key data-entry point when setting up their first product spec.

**[SUGGESTION] Product families / `base:` field not mentioned or forward-referenced**

The `Product` model supports a `base: <product_id>` field that lets variant products inherit and override specs. This is relevant context because test engineers commonly have product families. A brief mention ("you can extend a base product spec using `base:` — see concepts/products.md") would orient the reader without bloating the tutorial step.

**[SUGGESTION] `part_number` field not mentioned**

The `Product` model has `part_number: str | None`. `concepts/products.md` notes that `part_number` auto-populates `dut_part_number` in test results, enabling yield analytics by part number. The tutorial's product YAML omits `part_number`. A brief note or inclusion in the example would alert readers to this operator-facing field.

---

## Cross-links

### Findings

**[WARNING] No link to `how-to/spec-driven-testing.md`**

Step 6 introduces exactly the concepts that `docs/how-to/spec-driven-testing.md` covers in depth: `characteristic:` + `tolerance_pct:`, `--product=` CLI flag, `--guardband=` flag, condition matching via parametrize. This is the most directly relevant how-to guide for the content of step 6 and is not linked anywhere on the page. A reader who wants to go deeper after step 6 has no obvious next destination.

**[WARNING] The "Characteristics" inline link points to `concepts/capabilities.md` rather than `concepts/products.md`**

Line 89: `[Characteristics](../concepts/capabilities.md)` links to the capability matching and `InstrumentCapability` concepts page. The `ProductCharacteristic` model and product YAML schema are documented in `concepts/products.md`, which is the more appropriate target for this term in the context of defining a product spec.

**[SUGGESTION] No link to `how-to/limits.md` for condition-indexed bands**

The "Conditions" section introduces condition-dependent specs but the how-to treatment of this topic (including examples, edge cases, and the `characteristic:` + `when:` pattern) lives in `docs/how-to/limits.md`. Step 4 of the tutorial links to this page; step 6 does not, even though step 6 introduces the product-spec side of the same concept.

**[SUGGESTION] No link to `reference/configuration.md` for full sidecar schema**

Step 5 links to `reference/configuration.md` for the full sidecar schema. Step 6 introduces additional sidecar fields (`spec_ref`, guardband values, the `characteristic:` pattern — though this last one is absent from the page itself) but does not repeat the schema reference link. A reader who jumps to step 6 without reading step 5 has no path to the schema reference.
