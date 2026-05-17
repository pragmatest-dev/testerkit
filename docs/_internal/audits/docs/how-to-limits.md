# Page audit: docs/how-to/limits.md

**Quadrant:** How-to (all limit forms and resolution order — inline, sidecar, marker, product spec)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 1 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 3 | 2 | 1 |
| Gaps | 1 | 3 | 1 |
| Cross-links | 1 | 2 | 2 |
| **Total** | **5** | **12** | **9** |

---

## Ordering

**WARNING — Resolution chain numbering disagrees with task-first ordering.**
Line 30 ("Where limits come from") presents the cascade as steps 1–8 before the reader has met any of the limit forms it references. Items 5 ("Inline `@pytest.mark.litmus_limits(...)`"), 6 ("Profile chain"), and 7 ("Product spec — `characteristic: \"<name>\"`") are introduced as cascade rungs before they are demonstrated as forms (Marker form starts at line 49; Product-spec delegation at line 140). A how-to should let the reader see each form before being asked to memorize where it sits in the cascade. Either move "Where limits come from" after all four forms have been demonstrated, or trim it here to a 2-line summary and put the full cascade at the end as a reference.

**WARNING — `Explicit limit= kwarg` section is buried after the long bands section.**
Lines 132–138 introduce `limit=Limit(...)` as a one-paragraph stub after 40+ lines on bands. The cascade lists "Explicit kwargs" as rung 1 (the entry point a beginner uses); the demonstration is the smallest of all forms and appears 5th. Promote it to immediately after "Where limits come from" (or before "Marker form") so the reader sees forms in cascade order: inline → sidecar → marker → product-spec.

**SUGGESTION — Comparators table sits between Characterization mode and Best practices.**
Comparators (line 152) is reference material more than a how-to step. Either move it up next to "Limit structure" where `comparator: GELE` is first mentioned (line 14), or push it to the very end as an appendix. Splitting it from its first mention forces readers to scroll.

---

## Voice

**WARNING — "Litmus checks every `verify(...)` and `logger.measure(...)` call against a configured `Limit`" overstates `logger.measure`.**
Line 3 says Litmus "checks every … `logger.measure(...)` call against a configured Limit and records the outcome." Per `src/litmus/execution/logger.py:946–988`, `logger.measure` defaults to `outcome=Outcome.DONE` and does NOT compute pass/fail against the limit — it copies limit fields onto the row. Only `verify` calls `_compute_outcome` and converts to PASSED/FAILED. Rewrite as: "`verify(...)` checks every measurement against a resolved `Limit`. `logger.measure(...)` records the limit on the row but does not judge (outcome = DONE)."

**SUGGESTION — Mixed pronoun use ("you" vs. impersonal).**
Most of the page is impersonal ("Litmus checks", "resolution walks"). The Best practices section pivots to imperatives at the reader ("Prefer …", "Use …", "Keep …", "Never hardcode"). Pivoting to imperatives in a closing-checklist is fine; just make sure no earlier paragraph slips into second person except in the checklist.

**SUGGESTION — "Operator-edited" vs "non-developer" — pick one term.**
Line 90 ("operator-edited limits — non-developers can tune"). Line 181 ("operator-tuned values … non-developers can edit them"). Both lines pair the same two phrases. Pick "non-developer" or "operator" and stay consistent — the doc has a personnel-vocabulary convention (`station_hostname` etc. per project memory) that suggests "operator" is the canonical term.

---

## Audience

**WARNING — `verify(name, value)` vs. `logger.measure(name, value)` distinction never explicitly stated.**
Line 3 lumps them together. Line 47 distinguishes them (verify "bypasses" — incorrect, see Accuracy). Best practice #1 says "Prefer `verify(name, v)` when a product spec exists". A test engineer reading top-to-bottom has no clear when-to-use-which until line 179, and the distinction is wrong when it arrives. Add a 2-line block at the top of "Where limits come from": "`verify` judges (raises `AssertionError` on FAIL); `logger.measure` records (no judgment). Both walk the same resolution chain."

**WARNING — Cascade listing assumes the reader knows what "Profile chain" and "Sidecar class branch field" mean.**
Items 3, 4, 6 in the cascade reference concepts (class branch nesting, profile chain, parent/child profiles) that are documented elsewhere. A how-to should either link to those concepts on first mention (the page has no link for "Profile chain" → `docs/how-to/profiles.md`, no link for the sidecar nesting scheme → `docs/how-to/writing-tests.md` or `docs/reference/configuration.md`) or restate them in one line.

**SUGGESTION — `ProductContext` link in cascade item 7 helps experts but stalls beginners.**
The parenthetical "(the loaded-product container)" on line 42 is glossary-style. Either teach this concept earlier (with one sentence: "the product YAML loaded via `--product=<id>`") or drop the parenthetical and rely on the link.

**SUGGESTION — Characterization mode appears twice (rung 8 and §"Characterization mode" at line 167) without acknowledging the duplication.**
Both occurrences say the same thing differently. Cross-link them or fold one into the other.

---

## Accuracy

**CRITICAL — Line 47 is FALSE: "`verify(name, value)` bypasses this chain and reads directly from the active product spec."**
Per `src/litmus/execution/verify.py:148–218`, `verify` calls `_resolve_measurement_limit(name, inline_any=False, low=None, high=None, nominal=None, comparator=None, limit=limit, units=None)` — the same function `logger.measure` calls (`src/litmus/execution/logger.py:979–988`). That function walks: explicit `limit=` kwarg → `get_active_limits()` (sidecar + marker + profile merged config) → `get_active_product_context()` (product spec by name) → `None`. `verify` differs from `logger.measure` only in that it (a) errors out (`MissingLimitError`) if nothing resolves, (b) computes outcome and raises `LimitFailure` on FAIL. It does NOT skip the marker/sidecar/profile cascade. This claim also contradicts `docs/reference/litmus-fixtures.md:31` ("resolves a limit from the active chain (sidecar / inline marker / product spec)") and `docs/reference/litmus-markers.md:26` ("both `verify(name, value)` and `logger.measure(name, value)` … resolve the limit against this marker (or the sidecar's `limits:` block, or the active product spec, in resolution order)"). Delete the sentence or rewrite as: "`verify(name, value)` and `logger.measure(name, value)` walk the same chain; `verify` additionally raises `LimitFailure` on FAIL and `MissingLimitError` if no limit resolves."

**CRITICAL — Line 36 and §"Explicit `limit=` kwarg" overstate `logger.measure`'s signature.**
Line 36 says: "**Explicit kwargs** — `logger.measure(\"v\", val, low=..., high=..., units=...)`". Per `src/litmus/execution/logger.py:941–948`, the actual signature is `measure(name, value, *, limit: Limit | None = None, outcome: Outcome = Outcome.DONE, allow_repeat: bool = False)`. There is no `low=`, `high=`, `nominal=`, `comparator=`, or `units=` keyword on `TestRunLogger.measure`. The `_resolve_measurement_limit` helper has an `inline_any` branch that takes these scalars (line 235–244), but `measure` calls it with `inline_any=False` and `low=None, high=None, ...` hard-coded. Either (a) wire those kwargs through `measure` (separate fix in the source), or (b) correct the docs to say only `limit=Limit(...)` is accepted. The `verify` callable's signature is also `verify(name, value, limit=None, characteristic=None)` (`src/litmus/execution/verify.py:38–44`) — no scalar kwargs. NOTE: `docs/tutorial/04-limits.md:62` and `docs/how-to/writing-tests.md:147` repeat this same error.

**CRITICAL — Cascade order is incomplete and partially wrong.**
The cascade in lines 31–43 lists 8 items but doesn't match the actual resolution paths. Two distinct paths exist:

1. *Inside* `_resolve_measurement_limit` (per-measurement, at call time): `inline_any` (the scalar branch — not exposed on `measure`) → explicit `limit=` kwarg → `get_active_limits()` (already-merged config) → `get_active_product_context().get_limit(name)` → None.
2. *Merging into* `get_active_limits()` (collection time, in `pytest_plugin/autouse.py:217–232`): sidecar file → class → per-test → inline `@pytest.mark.litmus_limits` → profile chain, all walked via `node.listchain()` with `dict.update`. Profile chain is the LAST update (so wins). Most-specific wins.

The page collapses these into one numbered list with conflicting precedence: it lists "Explicit kwargs" as rung 1 (least-specific) but the explicit `limit=` kwarg actually short-circuits the cascade and wins outright (most-specific). It lists "Inline marker (5)" before "Profile chain (6)" before "Product spec (7)" — but in `autouse.py`, profile markers merge AFTER inline markers (line 222 walks `request.node.listchain()` which includes profile injection), so profile-chain entries override inline. Decide on a precedence model, fact-check it against `pytest_plugin/autouse.py:_litmus_push_limits` and `execution/logger.py:_resolve_measurement_limit`, and rewrite. The current list will mislead anyone trying to predict which limit wins.

**WARNING — Line 18 says "At least one of `low`, `high`, `nominal`, or `characteristic` is required" — true for `MeasurementLimitConfig` only at the validator level.**
`MeasurementLimitConfig._require_some_policy` (`test_config.py:694–708`) requires "at least one of: direct limit (low/high/nominal), characteristic + tolerance, expr, lookup, steps, callable, or a non-empty bands list." So `bands:` alone (no flat low/high/nominal/characteristic at the parent) is legal, as is `expr` / `lookup` / `steps` / `callable`. Also, the `Limit` class itself (line 233–270) does NOT enforce this — `units: str` is the only required field on `Limit`. Rephrase as: "Every entry under `limits:` must declare some policy — `low`/`high`/`nominal`, `characteristic`, `tolerance_pct`/`tolerance_abs` on a characteristic, a `bands:` list, or one of the roadmap fields (`expr`/`lookup`/`steps`/`callable`)."

**WARNING — `spec_ref` is documented as a field on the limit dict; on `MeasurementLimitConfig` it's actually accepted, but the page says nothing about `characteristic_id`.**
Line 14 shows `spec_ref:` as a top-level optional field on a limit. Per `MeasurementLimitConfig` (lines 628–631 of `test_config.py`), both `spec_ref` AND `characteristic_id` are accepted. `Limit` (lines 249–256) also has `characteristic_id`. Either drop `spec_ref` from the table (it's a documentation breadcrumb, rarely operator-set) or add `characteristic_id` for parity. Right now the field table is incomplete.

**SUGGESTION — Bands example using `tolerance_pct` (line 121–128) is correct, but the prose around it ("Bands can use any policy field a flat limit supports, including `tolerance_pct` against a product characteristic") implies tolerance_pct is one example among many policy fields supported in bands, yet `tolerance_pct`, `tolerance_abs`, `expr`, `lookup`, `steps`, `callable`, `guardband_pct`, `comparator` are all valid `MeasurementLimitConfig` fields and `expr`/`lookup`/`steps`/`callable` are flagged as ROADMAP (not yet wired) in `test_config.py:604–610`. The page should not silently advertise unwired fields; it currently doesn't mention them, which is fine, but `guardband_pct` and `comparator` are wired and unmentioned. Consider a "Policy fields" mini-table.

---

## Gaps

**CRITICAL — `MissingLimitError` (verify with no resolvable limit) is never mentioned.**
Per `src/litmus/execution/verify.py:87–94, 195–201`, calling `verify("x", v)` with no limit configured raises `MissingLimitError` — a `ValueError` subclass. This is a load-bearing UX rule (the entire reason `verify` exists vs. `logger.measure` is judgment, and the platform forces it). Best practice #1 ("Prefer `verify(name, v)` when a product spec exists") and the "Characterization mode" section silently imply you can `verify` without a limit and it'll just record. You can't. Add a sentence to "Where limits come from" rung 8: "If you call `verify` and reach rung 8, `verify` raises `MissingLimitError` — use `logger.measure` for record-only." Or, better, add a dedicated subsection.

**WARNING — Profile chain is listed as a rung but not demonstrated.**
The cascade mentions profile chain (line 41) and best practice #3 says "Keep operator-tuned values in a sidecar `limits:` field". Profiles can also carry `limits:` (`profiles/*.yaml`) — the same shape as sidecar. The page never shows a profile-level limit example or explains when to use profile vs. sidecar. Add one example or link to `profiles.md`.

**WARNING — `guardband_pct` (session-level and per-limit) is unmentioned.**
`MeasurementLimitConfig.guardband_pct` is wired (`sidecar.py:218`) and there's a session-level `--guardband <pct>` flag (`docs/reference/pytest-native.md:90`, `docs/how-to/spec-driven-testing.md:53–69`). A page titled "Test Limits" that covers product-spec delegation but says nothing about guardband is a gap. Add a paragraph or a link.

**WARNING — How `bands:` interacts with `verify`/product-spec `bands:` on the characteristic is unclear.**
A product characteristic can have its own `bands:` list (with `when:` per band — see `concepts/products.md` and `spec-driven-testing.md`). The page introduces `limits.<name>.bands:` (test-level) and `characteristic:` delegation but never explains what happens when both have bands. (Per `sidecar.py:_resolve_single`, the test-level band wins if its `when:` matches the active params; if you fall through to the characteristic, the characteristic's own band selection runs against the same params.) Worth one paragraph because it's the most likely confusion point.

**SUGGESTION — No worked end-to-end example.**
The page has many forms but no single example that shows: a product YAML, a sidecar YAML, a test function, and the resulting Measurement row (with `limit_low` / `limit_high` / `characteristic_id` / `spec_ref`). Spec-driven-testing.md has a partial version; cross-link or include a compact one here.

---

## Cross-links

**CRITICAL — Link target `../concepts/products.md` lacks the anchor for ProductContext.**
Line 42 and 142 link to `../concepts/products.md` with description "(the loaded-product container)". `docs/concepts/products.md` describes Products (the YAML), not `ProductContext` (the Python container, in `src/litmus/products/context.py`). The link is to the wrong target — concept page describes the data model, not the runtime container. The right target is either (a) `docs/reference/models.md` (where `ProductContext` would be enumerated) or (b) the source file. Verify and fix; if `models.md` doesn't have a `ProductContext` anchor, add one. (The same broken framing appears in `docs/how-to/spec-driven-testing.md:3`.)

**WARNING — No link to `docs/reference/litmus-markers.md` for `litmus_limits` marker semantics.**
The Marker form section demonstrates `@pytest.mark.litmus_limits(...)` but doesn't link to `docs/reference/litmus-markers.md#litmus_limits`, which has the canonical signature, the no-stacking rule, and a class/method override example. Add a "See also: marker reference" line at the end of the Marker form section.

**WARNING — Cross-page contradiction not surfaced.**
`docs/how-to/writing-tests.md:141–151` and `docs/reference/litmus-markers.md:221` both state slightly different resolution orders than this page. If this page is the canonical home for resolution order (per the how-to/index.md description: "limit shapes, condition-indexed bands, comparator semantics"), the other pages should defer to it; right now all three have overlapping prose. Either consolidate (single source) or add "Canonical resolution-order spec lives in [Test limits](../how-to/limits.md#where-limits-come-from)" to the other two pages.

**SUGGESTION — Add forward link to `docs/how-to/spec-driven-testing.md` from the "Product-spec delegation" section.**
The section at line 140 is a great gateway to spec-driven testing but has no link. Add one — "See also: [Spec-driven testing](spec-driven-testing.md) for the full workflow."

**SUGGESTION — Link to `docs/reference/models.md#measurementlimitconfig` from the field table.**
The field table at lines 20–28 describes the limit dict shape but doesn't link to the Pydantic schema (`MeasurementLimitConfig` in `src/litmus/models/test_config.py`). The marker reference does this (`litmus-markers.md:48`); the how-to should too.
