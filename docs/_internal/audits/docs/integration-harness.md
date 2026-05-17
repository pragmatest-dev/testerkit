# Page audit: docs/integration/harness.md

**Quadrant:** Integration (TestHarness imperative API for non-pytest runners)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 3 | 2 | 1 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 0 | 2 | 4 |
| **Total** | **5** | **14** | **14** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L56–71 | "Running vectors" appears before "Steps" (L96–111), but the example at L60–68 iterates vectors *outside* any step. Steps are the parent of vectors in the recorded hierarchy; the page should establish steps first (or at least mention that "without a step context, all vectors attach to the top-level `step_name` from the constructor") before showing a bare `for vector in harness.vectors` loop. As written, a reader following top-to-bottom builds an example that produces unsteped vectors and only discovers step boundaries 40 lines later. |
| ⚠️ WARNING | L96–111 | The Steps section reintroduces `harness.vectors` / `harness.run_vector` inside an example (L106–108) after already covering them at L56–71, but never explicitly says "use a step to wrap a vector loop." The relationship between the two sections is left for the reader to infer from a code snippet. |
| 💡 SUGGESTION | L142–156 | "Spec-driven limits" comes after "Hierarchical context" but `product_context=` was first introduced in the constructor table at L51 and referenced again in the limit-resolution order at L90. A short forward-reference at L90 ("see [Spec-driven limits](#spec-driven-limits) for an end-to-end example") would help, or move "Spec-driven limits" to immediately follow "Recording measurements". |
| 💡 SUGGESTION | L158–168 | "Comparison with pytest-native" sits before "See also" but functionally belongs in the lead — the page opens with "for new projects use the plugin" but doesn't deliver the comparison until the end. Consider folding the comparison table near the top so readers evaluating whether to use `TestHarness` get the decision criteria first. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L26 | Passive voice hiding actor | "no events are recorded" → "the harness records no events" |
| 💡 SUGGESTION | L70 | Passive voice | "every measurement inside [is] stamped" — "stamps every measurement inside" reads cleaner |
| 💡 SUGGESTION | L139 | Passive voice | "Run-scope fields appear as columns" / "Step- and vector-scope fields appear only on…" — both passive constructions hide the writer (the logger). Could be sharper, but borderline acceptable as descriptions of an outcome rather than an action. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L26 | Programmer jargon / niche use case | "useful only for tests-of-tests" — "tests-of-tests" is meta-testing jargon. Test engineers reading this page (LabVIEW / TestStand / Robot Framework refugees) won't recognize the term. Say "useful only when you're testing the harness itself" or omit. |
| ⚠️ WARNING | L168 | Audience confusion | "Reach for `TestHarness` when the embedding environment leaves you no choice." — slightly condescending framing; the page's whole point is to support non-pytest runners. Rephrase as "Use `TestHarness` when your test runner is not pytest." |
| 💡 SUGGESTION | L24 | Pydantic model name in prose without one-liner | "The `RunContext` Pydantic model is created internally from `TestRun`; you don't construct it." — first mention of both `RunContext` and `TestRun`. Neither is linked, and a reader who comes here from Robot Framework / unittest has no anchor. Either drop the sentence (it's defensive against a question that wasn't asked) or briefly say what each is for. |
| 💡 SUGGESTION | L51 | Cold reference to `ProductContext` | "Active product spec — enables `verify(name, value)` style limit + traceability resolution" — `verify` is a pytest-native fixture; this page is for non-pytest users. Naming the pytest fixture here as the way to think about `product_context` is misleading for the target audience. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L123 | doc says `prompt_type` values are `confirm`, `choice`, `text` | `Literal["confirm", "choice", "input"]` — `text` is not a valid value; the third value is `input` | `src/litmus/models/test_config.py:531` |
| ❌ CRITICAL | L130 | doc says `harness.run_context.set("operator", "jane")` | `harness.run_context` returns the harness `Context` class (defined in `harness.py:105`), which has `configure()` / `observe()` / `set_params()` — but **no `set()` method**. The `.set(key, value)` method exists on a *different* class, `RunContext` in `logger.py:307`, which is reached via the pytest `run_context` fixture — not via `harness.run_context`. | `src/litmus/execution/harness.py:105–476`, `src/litmus/execution/logger.py:307–373` |
| ❌ CRITICAL | L132 | doc says `harness.context.set("fixture.id", "FIX-01")` | Same as above — `harness.context` is also a `Context` instance with no `.set()` method. The example will raise `AttributeError`. The intended method is `harness.context.configure("fixture.id", "FIX-01")` (for in_* params) or `harness.context.observe(...)` (for out_* observations). | `src/litmus/execution/harness.py:179, 190` |
| ⚠️ WARNING | L24 | doc says "The `RunContext` Pydantic model is created internally from `TestRun`" | `RunContext` (`src/litmus/execution/logger.py:307`) is a plain Python class, **not a Pydantic model**. `TestRun` *is* a Pydantic model. `RunContext.__init__` accepts a `TestRun` reference and stores it; it is not "created from" `TestRun`. | `src/litmus/execution/logger.py:307–338` |
| ⚠️ WARNING | L24 | doc says `TestRunLogger.__init__` takes "`station_name`, `operator_id`, `test_phase`, `product_id`, `data_dir`" | All present, but the doc omits significant keywords that a non-pytest integrator will need: `station_type`, `station_location`, `station_hostname`, `operator_name`, `profile`, `profile_facets`, `session_inputs`, `session_id`, `run_id`, `product_name`, `product_revision`, `fixture_id`, `dut_part_number`, `dut_revision`, `dut_lot_number`, `git_commit`, `git_branch`, `git_remote`, `project_name`, `project_dir`, `instruments`, `environment`. The "see ... for the full keyword list" softens it, but the docstring it points readers to is the source code, not docs. | `src/litmus/execution/logger.py:387–426` |
| 💡 SUGGESTION | L146 | doc passes `guardband_pct=10` to `ProductContext.from_file` | The parameter is a float fraction in `guardband_pct: float = 0.0`. Calling with `10` is interpreted as 10 (i.e. 1000% guardband), almost certainly not the intent. Real usage is fractional (e.g. `0.10` for 10%). Verify by reading `ProductContext.get_limit` semantics or change the example value to `guardband_pct=0.10` with a comment "10%". | `src/litmus/products/context.py:82` |
| ✅ VERIFIED | — | 19 claims verified against source (import paths for `TestHarness`, `TestRunLogger`, `Limit`, `MeasurementLimitConfig`, `RetryConfig`, `PromptConfig`, `ProductContext`; constructor signature parameter names and defaults; `harness.vectors` / `current_vector` / `retry_config` / `context` / `run_context` / `prompt` / `measure` / `step` / `record` methods; `Vector.changed()` / `vector["key"]`; `Context.observe()`; `Context.connections`; `Context.run` / `station` / `product`; limit resolution chain order matches `_resolve_limit` at harness.py:647; `LITMUS_MARKER_NAMES` includes `litmus_limits` and `litmus_sweeps`) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L9–26 (Required collaborators) | The page never explains how to **close** the run. A reader wires up `TestRunLogger`, calls `harness.measure(...)`, then... what? Is there a `logger.close()` / `logger.finalize()` / `logger.end_run()`? Does the event log flush on garbage-collection? Without this, an integrator's run ends with partial events on disk and no run outcome ever recorded. This is *the* critical gap for a non-pytest integration page — the pytest plugin handles run lifecycle invisibly, but explicit users need to know what they own. |
| ❌ CRITICAL | Whole page | No complete end-to-end example. Every snippet is a fragment. A non-pytest integrator needs one full `if __name__ == "__main__":` runnable example showing: create logger → create harness → open step → iterate vectors → record measurements → close cleanly → query results. Without it, assembling the pieces from fragments is a research project. |
| ⚠️ WARNING | L26 | "A harness without a logger still runs, but no events are recorded — useful only for tests-of-tests." — what about a **logger without a harness**? The page never says you can use `TestRunLogger.measure()` directly without ever constructing a `TestHarness`. For Robot Framework / unittest users with no vector/retry needs, that may be the right entry point — but it's invisible here. |
| ⚠️ WARNING | L51 | `product_context` parameter is described as "Active product spec — enables `verify(name, value)` style limit + traceability resolution" — but `verify` is the pytest fixture, never used in `TestHarness`. What does this enable for a `TestHarness` user? (Answer per source: auto-resolution of `dut_pin` / `instrument_channel` / `fixture_connection` in `harness.measure`, and limit lookup by characteristic name — neither stated.) |
| ⚠️ WARNING | L60–68 | The vector example references `psu`, `dmm`, `vector["temperature"]`, `vector["vin"]`, but nowhere does the page say where `psu`/`dmm` come from in a non-pytest world. Pytest users get them from autouse fixtures; `TestHarness` users have to construct them. The `instruments` constructor argument is mentioned but its actual usage for *getting at* instruments inside the loop is not shown. |
| ⚠️ WARNING | L74–84 | What happens to a measurement when `value` is outside the limit? Does `measure()` raise, return a failing `Measurement`, set the vector outcome, or just record? (Source: `check_limit()` sets `measurement.outcome`, the logger escalates the vector outcome — but the page never says.) What does the function return? (A `Measurement` per source.) |
| ⚠️ WARNING | L86–93 | Limit resolution shows 4 entries but the source `_resolve_limit` (`harness.py:647`) shows 5 — per-vector `_limits` overrides come first. The page omits this. For a power user passing a `config` with per-vector limit overrides, this matters. |
| 💡 SUGGESTION | L114–122 | "Operator prompts" section: what does the prompt block on? Where does the answer surface — stdout, a UI, an event? What if no operator is connected? What does `timeout_seconds` do on timeout — return None, raise? |
| 💡 SUGGESTION | L142–156 | "Spec-driven limits" example shows the call but never shows the YAML structure of `products/power_board.yaml` it's loading. The reader has to leave the page to find out what a product spec looks like. A 6-line snippet inline would close the loop. |
| 💡 SUGGESTION | L96–111 | "Step boundaries are required when you want measurements grouped under named work" — but the page never describes how nested steps work, whether `with harness.step()` can be nested, or what happens to a measurement emitted outside any `step()` (the no-step path that "attaches to the top-level `step_name` from the constructor" is asserted but never demonstrated). |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L24 | First use of `TestRun` and `RunContext` (the logger class) in prose — neither links to a definition. `TestRun` should link to `../reference/models.md` (or wherever the data model is documented). `RunContext` is internal and arguably needs no link, but should at least be clarified (see Audience finding). |
| ⚠️ WARNING | L139 | "Parquet" mentioned in prose with no link. First use on the page. Should link to `../concepts/results-storage.md` or `../reference/parquet-schema.md`. |
| 💡 SUGGESTION | L3 | `context`, `verify`, `logger`, `pins` named as exemplars in the lead callout, but neither is individually linked to its anchor (`../reference/litmus-fixtures.md#context`, etc.). The blanket link to `litmus-fixtures.md` is fine — anchors per fixture would just be polish. |
| 💡 SUGGESTION | L51 | `verify(name, value)` referenced — could link to `../reference/litmus-fixtures.md#verify`. |
| 💡 SUGGESTION | L93 | `Limit` model first appears as a Python type in prose — could link to `../reference/models.md#limit` or whichever anchor exists. Same for `MeasurementLimitConfig`, `PromptConfig`, `RetryConfig` (already collected in the "See also" Models link, so low priority). |
| 💡 SUGGESTION | "See also" L170–176 | Missing entry for `../concepts/results-storage.md` or `../concepts/event-log.md` — this page repeatedly references "the event log" (L9) and "events recorded" (L26) without any link to the concept page that explains them. A reader integrating a non-pytest runner needs to understand what they're writing into. |
