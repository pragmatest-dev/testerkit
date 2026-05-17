# Page audit: docs/how-to/spec-driven-testing.md

**Quadrant:** How-to (spec-driven testing with litmus_characteristics + product spec for automatic limit derivation)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 1 | 2 | 2 |
| Accuracy | 2 | 4 | 2 |
| Gaps | 1 | 3 | 3 |
| Cross-links | 0 | 3 | 3 |
| **Total** | **4** | **15** | **15** |

---

## Ordering findings

The page reads top-to-bottom in roughly the right shape for a how-to (workflow → minimal example → variations), but several jumps break the flow once a reader is past the minimal example.

- WARNING — The `characteristic:` delegation section ("Delegate a limit by name") appears *before* "Condition matching", but the minimal example already uses condition matching implicitly (the parametrize axes match the band `when:` clauses). A reader who lands on the page and runs the example will hit a condition-matching question first; the delegation section is a sidebar concern by comparison. Order should be: Minimal example → Condition matching → Guardband → Delegate by name → Parquet row → When-to-use table.
- WARNING — "Guardband" sits between the minimal example and `characteristic:` delegation, which interrupts the natural progression from "how spec-driven verify works" to "how to point a different name at a spec". Guardband is a tightening *modifier*, not a core mechanic of spec-driven testing — it belongs after the core flow is fully explained, near the end alongside the parquet-row table.
- SUGGESTION — The "What ends up in the parquet row" table is a reference-style payoff that belongs near the end as a closer, but it currently precedes the "When to reach for verify vs logger.measure" decision table. Decision tables should be the last thing the reader sees before "See also" — they're the takeaway.
- SUGGESTION — The "workflow" numbered list at the top reads as 1-2-3 setup, but step 3 ("Call `verify(name, value)` from the test body — everything else flows through") is doing all the conceptual work. Consider splitting it: "Define product YAML" / "Wire the product into the run" / "Call verify in the test" — three concrete actions instead of two-and-a-promise.

---

## Voice findings

Voice is mostly consistent with the surrounding how-to corpus (imperative, second-person, terse), but a few sentences drift toward explanatory/reference register.

- WARNING — The opening paragraph (line 3) is a single sentence that runs 80+ words with two parenthetical asides ("a `ProductContext`…" and "you just call `verify(name, value)`"). How-to openers in this corpus typically lead with the verb the user will perform; this one front-loads the abstraction. Rewrite as: "Derive test limits from a product spec — call `verify(name, value)` and the limit, DUT pin, and spec_ref resolve from the active product context."
- SUGGESTION — Line 50 ("`verify` picks the condition row that matches…and raises `AssertionError` on fail.") is descriptive prose explaining what just happened. How-tos usually leave that explanation to a brief one-liner after the code block, not a full paragraph. Tighten to: "`verify` resolves the limit, records `dut_pin` and `spec_ref`, and raises `AssertionError` on fail."
- SUGGESTION — The "Guardband" section uses two adjacent code blocks with a one-line preamble each ("Apply a manufacturing-margin tightening…" and "Or inline on the spec load:"). The CLI form should be the default and the Python form a footnote; collapse "Or inline on the spec load:" into a single sentence below the block: "For programmatic loads, pass `guardband_pct=` to `ProductContext.from_file(...)`."
- SUGGESTION — "When to reach for `verify` vs `logger.measure`" is a phrase the rest of the corpus says more directly: "verify vs logger.measure — pick one" (writing-tests.md line 5). Mirror that phrasing for consistency.

---

## Audience findings

This page is meant for test engineers who have already met the platform and want to wire a product spec into their tests. Several spots assume more or different background than that audience has.

- CRITICAL — Line 44 example uses two fixtures, `chamber` and `eload`, that are not Litmus built-ins (verified against `/home/ryanf/repos/litmus/src/litmus/pytest_plugin/__init__.py`; the built-in fixture list contains `verify`, `logger`, `limits`, `dmm`, `psu`, `pins`, `connections`, etc., but not `chamber` or `eload`). The reader will copy the example, run it, and get `fixture 'chamber' not found`. Either (a) use station-config-derived names that are demonstrably auto-registered (`psu`, `dmm`), or (b) add a one-liner above the example: "`chamber` and `eload` are user-defined fixtures in your `conftest.py` or station config." The example must not look turnkey when it isn't.
- WARNING — The `characteristic:` section (line 72) jumps straight into `@pytest.mark.litmus_limits(rail_3v3={"characteristic": "output_voltage"})` without explaining what `litmus_limits` is or where its docs live. A test engineer who is here because they want spec-driven testing may not yet know about the markers system. Add a half-sentence: "Use `litmus_limits` (see [Limits guide](limits.md)) to delegate by name…"
- WARNING — Line 92 ("…the row that `context.get_param(...)` would return…") references the `context` fixture and `get_param` without forward link. A reader following the workflow will see `verify` in the example and `context.get_param` in the prose with no bridge. Either replace with a plain-English description ("the values your `@pytest.mark.parametrize` row currently has") or link to the context fixture entry in `reference/litmus-fixtures.md`.
- SUGGESTION — The page implicitly assumes the reader knows what `dut_pin` and `spec_ref` mean — they're presented in the parquet-row table without a one-line definition. A reader looking only at this page might wonder what `fixture_connection` is, too. A one-liner under the table ("See [Traceability](traceability.md) for what each field means") fixes it.
- SUGGESTION — The minimal example mixes a YAML block and a Python block with no signposting that the file paths matter (`products/power_board.yaml`, `tests/test_power.py`). New test engineers benefit from a single sentence: "Drop the YAML in your repo's `products/` directory; the test file goes anywhere pytest collects from."

---

## Accuracy findings

Several claims are verifiable from the source; two are wrong as written, four are imprecise or context-dependent, and two are nits.

- CRITICAL — Line 50 claims `verify` records `spec_ref="output_voltage @ temperature=25, load=0.5"`. Reading `_build_spec_ref` in `/home/ryanf/repos/litmus/src/litmus/execution/limits.py` lines 148–154: the spec_ref base is `char.datasheet_ref or "spec"`, NOT `char_id`. With `datasheet_ref: "Section 7.2"` (as in the minimal example YAML), the recorded spec_ref will be `"Section 7.2 @ load=0.5, temperature=25"` — note also the conditions are sorted alphabetically by key (`sorted(conditions.items())` at line 152), not in declaration order. Two factual bugs in one line: wrong base, wrong condition order.
- CRITICAL — Line 50 says `verify` "records `dut_pin="VOUT"`". Reading `ProductContext.get_pin_info` in `/home/ryanf/repos/litmus/src/litmus/products/context.py` lines 156–162: `result["dut_pin"] = pin.name` (the physical designator), not the pin key. Given the minimal example's `VOUT: {name: "J1.3", net: "VOUT_3V3"}`, `dut_pin` will be `"J1.3"`, NOT `"VOUT"`. The pin key is the dict key; the recorded `dut_pin` is the `.name` attribute. The traceability page (line 26) correctly shows `"J1.3"` as an example value.
- WARNING — Line 92 says condition matching "runs against the row that `context.get_param(...)` would return". Reading `resolve_limit` in `/home/ryanf/repos/litmus/src/litmus/execution/sidecar.py` lines 252–272 (and `ProductContext.get_limit` → `derive_limit` → `char.get_spec_at`), the match runs against `get_active_vector_params()` for the `verify` path. That ContextVar is populated by both `@pytest.mark.parametrize` and `litmus_sweeps` rows. The claim is correct in spirit, but `context.get_param(...)` is not the function that drives the match — it's a downstream reader of the same dict. Phrase as: "matched against the same vector-params dict that `context.get_param(...)` reads from".
- WARNING — Line 68 guardband arithmetic: "spec: 3.3 V ± 5 % → 3.135 .. 3.465" is correct. "with 10 % guardband (tighten by 10 %): → 3.152 .. 3.449". Reading `_apply_guardband` in `/home/ryanf/repos/litmus/src/litmus/execution/limits.py` lines 130–135: for a range comparator, `guardband_amount = range_size * guardband_pct / 100.0 / 2.0`. Range size = 3.465 − 3.135 = 0.33; guardband_amount = 0.33 × 10/100 / 2 = 0.0165. New bounds: 3.135 + 0.0165 = 3.1515 and 3.465 − 0.0165 = 3.4485. The displayed values are correct to 3 decimal places, but the wording "tighten by 10 %" obscures that only half the percentage is applied to each side. State the actual semantics: "tightens the range by 10 % total — 5 % from each side".
- WARNING — Line 64 example: `ProductContext.from_file("products/power_board.yaml", guardband_pct=10.0)`. This is the documented constructor signature (verified at `context.py` line 78–88), but the field is named `guardband_pct` consistently, while the pytest CLI option is `--guardband` and the field on `ProductContext.__init__` is `default_guardband_pct` (line 53). The page uses `guardband_pct=10.0` (correct for `from_file`) but `--guardband=10` (correct for CLI) — consistent, but worth a one-liner that `guardband_pct` is "percent (0–100)" since the CLI passes a string-int and `from_file` takes a float.
- WARNING — The parquet-row table (line 96) lists `measurement_outcome` "passed / failed (lowercase enum value)". Reading `_compute_outcome` in `verify.py` lines 97–110: outcome can be `Outcome.ERRORED` when `value is None` — verify also produces an errored row, not only passed/failed. Three states minimum: `passed` / `failed` / `errored`. The table understates the surface.
- SUGGESTION — Line 109 says traceability fields "are injected by the plugin". They're injected by the runner-neutral `logger.measure` path (`/home/ryanf/repos/litmus/src/litmus/execution/logger.py` line 967, `_resolve_trace_fields`), with the pytest plugin being one runner. Consistent with the CLAUDE.md guidance "Litmus is a PLATFORM, not a pytest plugin", say "injected by the platform" or "injected by the runner-neutral measurement pipeline".
- SUGGESTION — Line 17–35 YAML uses `temperature: {min: 0, max: 50}` and `load: {min: 0.1, max: 0.5}` as `when:` keys. Verified that `band_matches` (`/home/ryanf/repos/litmus/src/litmus/models/capability.py`) treats `min`/`max` as a range probe against the active scalar value. The example is fine, but worth a forward link to the concepts page that documents the `when:` schema — readers will copy-paste and want to know what other key shapes are accepted (`{value: 25}`, `{set: [...]}`, etc., if those exist).

---

## Gaps findings

The page covers the happy path well but leaves several questions unanswered that a test engineer will hit within their first session.

- CRITICAL — No mention of what happens when `verify(name, value)` is called and no product spec is loaded (no `--product`, no `products/` dir). Reading the source: `MissingLimitError` (`verify.py` lines 87–94 and 196–201) is raised with a multi-line hint. The page must show that error and tell the reader how to recover (load a product, use `logger.measure`, pass `limit=` explicitly). Without this, the page is silent on the most common error mode.
- WARNING — No mention of what happens when `verify` is called with a name that has no matching characteristic. Reading `ProductContext.get_limit` (line 116–119): raises `KeyError(f"ProductCharacteristic '{char_id}' not found in product '{self.product.id}'")`. Reader will hit this when they typo a characteristic name; the page should preview the error.
- WARNING — No mention of what happens when condition matching finds no band (`derive_limit` line 50–54 raises `ValueError` with the available `when:` clauses listed). This is the third most likely failure mode after the two above; the page should at least mention it exists.
- WARNING — The page demonstrates a parametrize-driven flow but never shows the alternative: using `litmus_sweeps` for range expansion (the marker is referenced once on line 92 but never illustrated). A reader who wants `temperature=linspace(-40, 85, 10)` will not know it's supported from this page.
- SUGGESTION — No mention of how `verify` interacts with class-level `@pytest.mark.litmus_characteristics([...])`. Reading `autouse.py` lines 55–66, that marker binds the test to one or more characteristics and is the idiomatic way to write a thin spec-driven test. The page demonstrates `verify("output_voltage", ...)` with the name passed explicitly, but never shows the binding-via-marker alternative — a notable omission given the page subtitle calls out `litmus_characteristics`.
- SUGGESTION — No mention of `limits[name]` (the read-only mapping fixture). For a test engineer who wants to assert inline (`assert v in limits["output_voltage"]`) against the same spec-derived limit, the page is silent. That's the pythonic non-raising counterpart to `verify` and lives in the same fixture group.
- SUGGESTION — No mention of how guardband interacts with the marker-cascade `litmus_limits` path vs the `verify`-direct path. Reading the limits.md cross-link, `verify` "bypasses this chain and reads directly from the active product spec" — but does `--guardband=10` apply to both paths uniformly, or only to the verify-direct path? Worth a one-liner.

---

## Cross-links findings

- WARNING — Line 3 links `[traceability](traceability.md)` and `[product specification](../concepts/products.md)` — both targets exist. But the page never links back to `traceability.md` from the "What ends up in the parquet row" table, even though every field in that table is documented in detail at `/home/ryanf/repos/litmus/docs/how-to/traceability.md`. Add a closing line under the table: "See [Traceability](traceability.md) for full field semantics."
- WARNING — Line 117 in the decision table mentions "Callable limit via marker / sidecar" with no link. Verified that `litmus-markers.md` documents `litmus_limits` and `limits.md` documents callable limit forms. Link the cell.
- WARNING — The page does not link to `/home/ryanf/repos/litmus/docs/reference/litmus-markers.md` despite using `@pytest.mark.litmus_limits` (line 77) and referring to `@pytest.mark.litmus_sweeps` (line 92). Both are documented in the reference; a single link at first use suffices.
- SUGGESTION — Add a link to `/home/ryanf/repos/litmus/docs/how-to/vector-expansion.md` from the "Condition matching" section. That page is the canonical doc for "what drives the vector-params dict that condition matching reads from" and is the natural next stop for a reader who has read this page and wants to drive more axes.
- SUGGESTION — The "See also" block at the bottom (lines 120–124) does not include `traceability.md`, despite traceability being the closest sibling how-to and being linked once in the opener. Add it.
- SUGGESTION — Add a link from "When to reach for `verify` vs `logger.measure`" to `/home/ryanf/repos/litmus/docs/how-to/writing-tests.md#verify-vs-loggermeasure—pick-one` (or equivalent anchor). The two pages cover the same decision; cross-linking prevents drift.
