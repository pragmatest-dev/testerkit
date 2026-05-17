# Page audit: docs/tutorial/05-configuration.md

**Quadrant:** Tutorial (step 5 of 10 — sidecar YAML configuration)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 1 |
| Voice | 0 | 1 | 1 |
| Audience | 0 | 2 | 0 |
| Accuracy | 1 | 2 | 1 |
| Gaps | 0 | 2 | 3 |
| Cross-links | 0 | 1 | 4 |
| **Total** | **1** | **10** | **10** |

---

## Ordering

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

⚠️ WARNING — OD-1: Vector content is split across three non-adjacent sections

The "Vector Expansion" section (line 74) is separated from "Range Expanders" (line 122) by the full "Accessing Vector Parameters via Context" section (line 99). All three sections are about the same concept (parametric sweeps) and a reader building understanding has to jump back and forth. A better sequence: introduce vectors, show how to access them via `context`, then show range expanders as a shorthand, then show change detection — all as a continuous vector topic.

⚠️ WARNING — OD-2: "Retries" section has no logical bridge from preceding content

The Retries section (line 158) appears after "Product with Change Detection" with no transition. There is no sentence explaining why retries are presented at this point in the tutorial, or how they relate to the sidecar YAML topic that is the stated goal of this page. A one-sentence bridge ("Sidecar YAML can also control retry behavior...") would anchor it.

💡 SUGGESTION — OD-3: "Where Test Config Lives" is too shallow for a tutorial step that introduces sidecar

The resolution priority list at the top of the page shows only two layers (markers, sidecar). The full five-layer stack (sidecar file-level → sidecar class/method → inline markers → profile chain → CLI flags) is documented in `docs/reference/configuration.md`. For a tutorial reader who is learning to configure tests, seeing only two layers is accurate-but-incomplete and may set wrong expectations about override behavior. A brief forward reference ("profiles and CLI flags add two more layers; covered in step 9") would close the gap without bloating this step.

---

## Voice

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

⚠️ WARNING — VO-1: "What You Learned" bullet uses incorrect vocabulary for zip semantics

Line 177: "Vector expansion: cross-product across keys, zip via comma-joined argnames"

"Comma-joined argnames" is pytest.mark.parametrize-specific internal vocabulary. In litmus_sweeps and sidecar YAML, zip is expressed as a **multi-key dict** within one list entry — the comma joining is an implementation detail (`sweeps.py` line 43: `",".join(argnames)`) passed to pytest under the hood, not a user-facing concept. A reader who reads this bullet and then looks at the sidecar YAML will be confused. Should read: "Vector expansion: cross-product across stacked list entries, zip via multi-key dicts in one entry."

💡 SUGGESTION — VO-2: Section heading "Product with Change Detection" is ambiguous

Line 138: `## Product with Change Detection` — "Product" here means "Cartesian product" but the page has already linked to "Product Specifications" as the next step. A reader skimming headings may misread this as being about product spec configuration. Rename to "Cross-Product Sweeps with Change Detection" or "Outer-Loop Change Detection" to disambiguate.

---

## Audience

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

⚠️ WARNING — AU-1: `verify` fixture is used without forward reference before it is explained

Line 39: `verify("output_voltage", dmm.measure_dc_voltage())` — the `verify` fixture is used in the first code block without any note that it was introduced in step 3. The page then switches to `logger.measure` on line 61 with no explanation of when to use one vs the other. A tutorial reader who starts from step 5 (plausible if they arrive from search) will not know what `verify` does or whether the code blocks are equivalent. A one-sentence aside ("...`verify` raises on fail; `logger.measure` records but never raises — both are covered in step 3") would orient them.

⚠️ WARNING — AU-2: The term "sweeps" is used in the first code block before it is defined

Line 28-29: the sidecar YAML example uses `sweeps:` as a key before the term is defined (definition first appears in the "Vector Expansion" section at line 74). A tutorial reader reading the sidecar YAML example will encounter an unexplained key. Either add a parenthetical "(vectors — covered below)" or move the sidecar example to appear after the Vector Expansion section introduces the term.

---

## Accuracy

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

❌ CRITICAL — AC-1: `context.get_param("key")` does NOT raise if the parameter is missing

Line 117: "`context.get_param("key")` - Required parameter (raises if missing)"

This is factually wrong. The actual implementation in `src/litmus/execution/harness.py` line 288:

```python
def get_param(self, key: str, default: Any = None) -> Any:
```

The default is `None`. When called with no second argument and the key is absent, `get_param` returns `None` silently — it does not raise. A test relying on this "required parameter" guarantee will fail in confusing ways (e.g., passing `None` to `psu.set_voltage()`). The bullet must be corrected: `context.get_param("key")` returns `None` when the key is absent; there is no built-in "required parameter" mode. If the caller needs to assert the key exists, they must check the return value explicitly.

⚠️ WARNING — AC-2: "What You Learned" zip description uses wrong user-facing vocabulary

Line 177: "zip via comma-joined argnames" — as noted in the Voice section, this is an internal implementation detail of `sweeps.py`, not the user-facing mechanism. The user-facing mechanism for zipping is putting multiple keys in one dict entry. This could mislead a reader into thinking they need to write comma-joined strings somewhere in their YAML or marker call.

⚠️ WARNING — AC-3: Resolution priority list is incomplete

Lines 6-14: The "Where Test Config Lives" section enumerates only two layers: pytest markers and sidecar YAML. The authoritative list in `docs/reference/configuration.md` lines 196-204 defines five layers in order: sidecar file-level, sidecar class/method, inline markers, profile chain, CLI flags. Omitting profile chain and CLI flags is acceptable for a tutorial intro, but the sentence "resolved in priority order" implies a complete list. At minimum, add "(profiles and CLI flags also participate — see step 9 and the configuration reference)" so readers don't build a wrong mental model.

💡 SUGGESTION — AC-4: Range expander claim "same shape works in any list position across all Litmus YAML (sidecars, profiles, stations, products)" overstates scope

Line 135-136: `expand_ranges` is called on all loaded YAML (it is called in `store.py`'s `load_yaml_validated`), so syntactically the claim is true. But range expanders in `stations/` and `products/` YAML are not useful because those YAML schemas have no `sweeps:` field. A range expander in `stations/my_bench.yaml` would expand meaninglessly into a field that Pydantic would then reject. The claim should be narrowed: "Same shape works in any value-list position in sidecar and profile YAML."

---

## Gaps

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

⚠️ WARNING — GA-1: `litmus_retry` / sidecar `retry:` block is omitted; only `@pytest.mark.flaky` is shown

The Retries section (line 158-171) teaches only `@pytest.mark.flaky` from `pytest-rerunfailures`. The page's own stated goal is sidecar YAML configuration, yet the sidecar-native retry mechanism (`retry: {max_retries: 3, delay: 0.5, on: [AssertionError]}`) is not mentioned. A learner who wants retry behavior in sidecar YAML will not find the answer here and may resort to inline markers unnecessarily. The `litmus_retry` marker and its sidecar form are documented in `docs/reference/configuration.md` lines 249-268 and `docs/reference/litmus-markers.md`. The Retries section should show the sidecar form alongside or instead of `@pytest.mark.flaky`.

⚠️ WARNING — GA-2: Class-level nesting in sidecar YAML is not shown

The page shows only flat test-function entries under `tests:` in the sidecar YAML. The sidecar supports class-branch nesting (`tests: TestMyClass: tests: test_method:`) as documented in `SidecarConfig` and the reference. Test engineers who use `class`-based test organization (common in pytest) will not learn from this page that sidecar YAML supports their structure. A brief example or note would close this gap.

💡 SUGGESTION — GA-3: No explanation of what happens when `context.get_param` returns `None` for a "missing" parameter

Related to AC-1: because `get_param` does not raise, a missing parameter silently propagates `None` into the test body. The page does not explain how to detect or guard against this. A sentence noting "if a vector parameter is not set, `get_param` returns `None` (or your supplied default) — validate the value before using it in hardware calls" would prevent a class of confusing runtime failures.

💡 SUGGESTION — GA-4: `prompts:` sidecar block is never mentioned

The page covers limits, sweeps, mocks, and retry (partially). The seventh sidecar field, `prompts:`, is not mentioned. This is acceptable for a tutorial, but a brief note ("The sidecar also supports `prompts:` for operator confirmation steps — see the markers reference") would orient learners who need operator interaction.

💡 SUGGESTION — GA-5: No mention of `context.last()` for cross-vector access

The "Accessing Vector Parameters via Context" section covers `get_param`, `params`, and `changed`. The `context.last(key)` method (also on the `Context` class in `harness.py` line 225) allows reading the previous iteration's *observed* values, not just vector params. For a tutorial on configuration this is a minor omission, but given the adjacent change-detection section it would naturally fit there.

---

## Cross-links

**Page:** `docs/tutorial/05-configuration.md`
**Quadrant:** Tutorial

### Findings

⚠️ WARNING — CL-1: The Retries section links to `pytest-rerunfailures` GitHub but not to `litmus-markers.md#litmus_retry`

Line 160: the link goes to `https://github.com/pytest-dev/pytest-rerunfailures`. The Litmus-native retry alternative (`litmus_retry`) is documented at `docs/reference/litmus-markers.md`. Since the page is introducing sidecar configuration, a link to the Litmus marker reference would help readers discover the sidecar-native form. The external GitHub link is useful to keep; the Litmus reference link is missing.

💡 SUGGESTION — CL-2: "Vector Expansion" and "Range Expanders" sections have no link to `how-to/vector-expansion.md`

The how-to guide at `docs/how-to/vector-expansion.md` exists and covers the full vector semantics including loop ordering, list-length checks, and generators. Neither the Vector Expansion section nor the Range Expanders section links to it. Add: "See [Test vectors and sweeps](../how-to/vector-expansion.md) for the full semantics."

💡 SUGGESTION — CL-3: The `mocks:` sidecar example has no link to `how-to/mock-mode.md`

Line 25-26: the sidecar YAML example shows `mocks:` but does not link to `docs/how-to/mock-mode.md`. A reader wanting to understand mock priority resolution (per-test mocks > file-level > station `mock_config` > zero) has no pointer.

💡 SUGGESTION — CL-4: No back-link to step 4 where `verify` and `logger.measure` were introduced

The page uses `verify` on line 39 and `logger.measure` on lines 61, 96, 113, 155, 168 without reminding readers where these fixtures were introduced. A parenthetical "(introduced in [Step 4: Add Limits](04-limits.md))" on first use of each fixture would help readers who land here out of sequence.

💡 SUGGESTION — CL-5: "Where Test Config Lives" should cross-link to `reference/configuration.md#test-configuration` for the full five-layer resolution order

Line 18 links to `../reference/configuration.md` for "the full schema" — good. The resolution order sub-section in that reference (`#test-configuration`) directly addresses the gap in the two-layer list this page shows. A more specific anchor link (`../reference/configuration.md#test-configuration`) would direct readers to exactly the right section.
