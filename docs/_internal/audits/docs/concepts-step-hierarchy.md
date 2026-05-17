# Page audit: docs/concepts/step-hierarchy.md

**Quadrant:** Concepts / Explanation (test step hierarchy — class → method → vector)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 1 | 3 | 2 |
| Accuracy | 1 | 4 | 2 |
| Gaps | 0 | 3 | 3 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **3** | **16** | **14** |

---

## Ordering

**SUGGESTION — Section sequence is broadly right for an Explanation page.** Opens with the tree, defines each level, then identity fields, worked example, special case (`vectors` fixture), rollup chain, and storage. That order matches a reader's natural "what is it → how is it identified → how does it look → how does it persist" path.

**WARNING — The `vectors` fixture section sits between two example/rollup blocks, breaking the example→rollup→storage flow.** The "Worked example" (line 65) demonstrates the normal pytest-parametrize path; the rollup section (line 129) closes the conceptual model; the storage section (line 145) lands the model in the materialized table. The `vectors` fixture section (lines 104–127) is a **variant** of the worked example and belongs immediately after it, or in a clearly-labelled "Special cases" sibling subsection so the reader sees the two examples side-by-side before moving on to rollup/storage.

**WARNING — `Identity fields` table appears before the worked example, forcing the reader to hold the field list in memory.** A reader new to the model parses 7 rows of `step_path` / `parent_path` / `step_index` / `vector_index` abstractly, then sees them used in the example. The example would land harder if it came first (or if the field table came right after, captioned "what you just saw in the example, named"). Concepts pages usually want concrete-then-abstract.

**SUGGESTION — Within the per-level subsections, "TestRun" → "Step" → "TestVector" → "Measurement" is correctly top-down,** but the "Step" section's two paragraphs (line 27 vs line 33: "container vs method is structural — not flagged") interleave the *definition* of a step with the *discriminator* between its two kinds. A `### Container step` / `### Method step` pair (or "Step kinds: container vs method" subhead) would let the reader navigate it.

**SUGGESTION — The opening sentence on line 3 commits to "single reference for Litmus's run-data hierarchy" but then immediately defers two adjacent topics (outcomes, step-manifest) to other pages.** Either rename the page promise ("This page covers the structural hierarchy; verdict cascade is on Outcomes; planned-vs-executed is on Step Manifest") or accept that the three pages together are the reference. Today's phrasing oversells the page in isolation.

---

## Voice

**WARNING — Three competing voices within a single page.** The page opens in clinical reference voice ("single reference for Litmus's run-data hierarchy"), shifts to narrator/explanation voice in "What each level is" ("One run = one pytest session. Wraps a session_id..."), and finally drops into source-comment voice in identity fields ("Logger derives from `_step_stack`"). Concepts pages should pick one register and hold it — the explanation/narrator voice is the right one for this quadrant.

**SUGGESTION — Source-internal names leak into prose.** `_step_stack` (line 55), `_stamp_container_outcome` (line 138), `assign_indices` (line 56), `callspec.params` (line 60) all surface in user-facing concept tables as if the reader will find them. These are implementation locators, not concepts. Either drop them entirely, or split them into a small "Implementation map" trailer (like `outcomes.md` does with its `file:line` references) so the conceptual surface stays clean.

**SUGGESTION — "iff" on line 33** is mathematician shorthand for "if and only if". Test engineers — the audience — will read it as a typo. Spell it out.

**SUGGESTION — Excessive em-dashes and parentheticals in the "Step" definition** (lines 25–33) chain three clauses with em-dashes plus a parenthetical OpenTAP reference. The result reads as a stream of qualifications rather than a definition. Split into two sentences: definition first, then the structural-not-flagged distinction.

---

## Audience

**CRITICAL — Page assumes deep pytest + Litmus internals knowledge without naming the audience.** A test engineer arriving at "Step Hierarchy" wants to know: *what shows up in my report, why, and how do I read it?* They get instead: pytest items, `callspec.params`, `_step_stack`, "outer iteration index", "self-loop" mode, "(class_name, outer_iteration_index)" tuple identity. The page is written for a **plugin implementor**, not a test engineer reading Concepts. Either re-cast the page for the test-engineer audience (the natural Concepts reader) or move the internals-heavy material to a dedicated `concepts/` companion (e.g. `step-hierarchy-internals.md`) and link out.

**WARNING — "TestVector" branding will confuse readers who arrive from `tutorial/05-configuration.md` or `how-to/vector-expansion.md`.** Those pages use lowercase "vector" / "vectors" to mean the user-facing concept (a row of sweep parameter values). This page introduces capital-T `TestVector` as a code class name and uses it interchangeably with "vector" — including in the level header (line 39: "### TestVector — one inner iteration"). For a Concepts page, "Vector" or "vector" is the user-visible noun; `TestVector` is the underlying model. Pick the user-visible one for headers and prose.

**WARNING — The OpenTAP / Keysight aside on line 33 trades on niche knowledge.** A test engineer migrating from LabVIEW or building from scratch has never heard of OpenTAP's recursive `TestStep` model. The aside reads as "we did our research" signaling, not as anchor for the reader. If the page wants the parallel, it should land it as an actual migration breadcrumb ("LabWindows / TestStand users: see migration page") rather than an unannotated reference.

**WARNING — Audience-mismatch in the "Worked example" code (line 67–79).** The example commits to class-level + method-level `litmus_sweeps` simultaneously, then narrates a 15-event stream. That's a Step-7 example dropped at the start of the concept. A reader internalising the hierarchy for the first time needs the simplest possible illustration (one class, one method, two iterations) BEFORE the dense nested case. Two examples — minimum-viable first, then the nested matrix — would land both audiences.

**SUGGESTION — Notation conventions are not introduced.** The event-stream block (lines 84–98) uses `vi=` for vector_index without ever defining it. Indentation = nesting is plausible but not stated. A two-line caption ("`vi` = `vector_index`; indentation = parent → child") would carry the reader through.

**SUGGESTION — "Test engineer" vs "platform developer" reader is not signalled on the page.** Adding a one-line header — "For: test engineers reading their results; platform developers writing subscribers" — would tell the reader they're in the right place and let the writer ration internals appropriately.

---

## Accuracy

**CRITICAL — Line 16 — "Each level rolls its outcome up to the next level via the severity-max ladder" is overstated.** The actual cascade chain (verified in `src/litmus/execution/logger.py:850–854` and `src/litmus/pytest_plugin/hooks.py:1197–1226`) is: `measurement → vector → step → run` for the per-test path, plus a separate `_stamp_container_outcome` walk that cascades **method-step outcomes into the class container's outcome at container-close time**. The container's outcome is *not* set by the test step's normal rollup — it's stamped by `_stamp_container_outcome` walking `test_run.steps[first_step_index+1:]`. The page later (line 129–141) gets this right in the rollup chain diagram, but line 16's summary glosses over the container-specific watermark path. Worth foreshadowing that "container outcome is stamped at container close, not by direct child rollup" in the opening summary.

**WARNING — Line 37: "`(step_path, vector_index)` is unique per executed step instance within a run."** Mostly true, but `vector_index` defaults to 0 for non-swept items, and a step that opened with no `step_index` provided falls back to the auto-increment counter (`src/litmus/execution/logger.py:702–706`). For a **class container** opened by `_ensure_class_container` (`src/litmus/pytest_plugin/hooks.py:1166`), `step_index` is *not* passed — the container's `step_index` is whatever the running counter happens to be when it opens, NOT a stable sequence position. The doc later claims (line 102) "`step_index` for the container is 0 (root-level)" — that's only true incidentally for the first container in a run; it's not guaranteed by the code.

**WARNING — Line 102: "`step_index` for the container is 0 (root-level), for each method is 0/1/2 within the `TestPower` class bucket — so `(step_index=1, vector_index=0)` uniquely points to `test_load[voltage=1, current=4]`."** Two issues:
 1. The "container step_index = 0" claim is fragile — see prior finding. In `assign_indices` (`src/litmus/data/_collection_indices.py:52–62`), `step_index` is **sequence-relative per parent**, and root-level only includes items whose class is empty. The class *container* itself is opened from `_ensure_class_container` and its `step_index` is set by the auto-incrementer, not by `assign_indices`. So "container is 0" is a coincidence of the first-container-in-a-fresh-run case.
 2. The "`(step_index=1, vector_index=0)` uniquely points to `test_load[voltage=1, current=4]`" claim treats `(step_index, vector_index)` as a primary key. The actual primary key is `(run_id, step_path, vector_index)` (verified at `src/litmus/data/_runs_duckdb_daemon.py:186`). `step_index` is sequence-relative and resets per parent — it is NOT a primary key. The text mixes two identity systems.

**WARNING — Line 49: "Carries the full effective `inputs` dict — outer step params **merged with** the current vector's inner params."** Verified at `src/litmus/execution/logger.py:876–879`: the merge uses `step.vectors[0].params` as "outer" and `build_input_columns(vector)` as "inner", with inner winning on key conflict. The doc accurately describes the merge, but glosses over that "outer" here is the FIRST vector of the method step, NOT the parent **container's** inputs. For a class-swept test, the class-level outer params reach `step.vectors[0]` via `callspec.params` (which combines class + method param injection in pytest), so the result happens to include both — but the doc's framing ("outer step params" + "inner vector's params") implies a parent → child walk that isn't what the code does. Recommend rewriting to: "Outer = the step's primary vector params (which include class-level sweep values via pytest's parametrize injection); inner = the active iteration vector's params."

**WARNING — Line 149: "`parent_path = '<class>/<method>'` → would be a nested step (uncommon today; only via `harness.step()` self-loops)."** Spot-check of `src/litmus/execution/logger.py:648–651` confirms `parent_path` is `"/".join(self._step_stack[:-1])`, so nesting two deep would indeed yield `Class/method`. But the parenthetical "only via `harness.step()` self-loops" is a hand-wave; the actual user-facing path is `with harness.step("inner"):` or successive `start_step` calls without an intervening `end_step`. Either drop the parenthetical or name the actual API.

**SUGGESTION — Line 60: "`step_index` | Pre-assigned per logical step at collection time (`assign_indices`)"** is true for **pytest-collected method steps** only. Class containers (opened in `_ensure_class_container`) are NOT pre-assigned — see prior findings. Worth distinguishing in the table.

**SUGGESTION — Severity ladder on line 143 is correct** (verified at `src/litmus/data/models.py:123–131`: `ABORTED(7) > TERMINATED(6) > ERRORED(5) > FAILED(4) > PASSED(3) > DONE(2) > SKIPPED(1)`). Note: the page should probably also surface that `None` ranks below everything (severity `-1`), since the rollup explicitly treats unjudged rows. The `outcomes.md` page covers this; a one-line reminder here would prevent confusion when a reader sees `step.outcome = None` in their results.

---

## Gaps

**WARNING — Missing: what does a non-class, module-level test look like in this hierarchy?** Every example assumes `class TestPower:`. The plain `def test_voltage(...)` at module scope is mentioned only implicitly (in line 149's `parent_path = ''` row of the bottom table). Test engineers writing their first Litmus test usually start with a module-level function, not a class. The page needs an explicit "Module-level test (no class container)" subsection or example: "The class container is synthesised only when `item.cls is not None`; a top-level `def test_foo` produces one method step with `parent_path = ''` and no container."

**WARNING — Missing: what is a "Step" event's `step_index` actually for, from a reader's perspective?** The identity-fields table says "Sequence-relative ordering within a parent bucket" — but the reader is left wondering when they'd ever use it. The actual answer (visible in `assign_indices`) is "so that all sweep variants of one logical step share an index, distinguishing 'which logical step' from 'which variant of it'". That use-case sentence is worth pulling into the prose.

**WARNING — Missing: where does retry fit?** `MeasurementRecorded.retry` exists (`src/litmus/data/events.py:387`) and `TestVector.retry` / `TestVector.max_retries` are first-class model fields (`src/litmus/data/models.py:260–261`). The doc claims this is "the single reference for Litmus's run-data hierarchy" but never mentions retry as a hierarchical dimension. Either add a "Retries" subsection ("a step that retries N times produces N+1 events at the same `(step_path, vector_index)`, distinguished by `retry`") or scope the page's promise more narrowly.

**SUGGESTION — Missing: the multi-DUT/slot dimension.** `RunStarted.slot_id` exists, `SlotRunner` is real, and the page makes a brief reference to "session_id (could span multiple runs in a multi-slot harness)". But the hierarchy diagram and per-level prose treat run as the root. A test engineer working with multi-DUT will wonder where slot sits. A one-line "Multi-DUT: each slot emits its own TestRun under one shared Session" would close the gap.

**SUGGESTION — Missing: empty / never-run step.** `outcome IS NULL` "never ran" rows are extensively covered in `step-manifest.md`, but a reader coming to *step hierarchy* with the question "what's in my report for a step that didn't run" gets nothing here. A one-line callout linking to `step-manifest.md` would close the loop.

**SUGGESTION — Missing: how `step_path` is built / sanitized.** The doc shows `"TestPower/test_voltage"` as if `/` is the canonical separator. It is (`logger.py:650`: `"/".join(self._step_stack)`), but what if a step name contains `/`? What about Unicode? What about long names? The page presents `step_path` as a primary identity field without addressing the obvious "what's the canonical form" question.

---

## Cross-links

**CRITICAL — `concepts/outcomes.md` link on line 15 should be inline, not parenthetical.** The page already mentions outcomes 4× in the body but the only link is in the opening paragraph (line 3) and the rollup section (line 143). The "Each level rolls its outcome up" sentence on line 15 should link to outcomes.md inline. Same for "see [Outcomes](outcomes.md)" — that's the citation; the reader needs the *concept link* on first mention.

**WARNING — Missing cross-link: `vectors` fixture section (lines 104–127) doesn't link to the actual fixture reference.** The `vectors` fixture has a dedicated entry in `docs/reference/litmus-fixtures.md` (line 250: "### `vectors` — function" with full signature and usage). A reader landing on "vectors fixture — one step, many inner vectors" has no path to the API reference.

**WARNING — Missing cross-link: `litmus_sweeps` marker on lines 29, 68, 73, 109, 111 is never linked.** The marker is documented at `docs/reference/litmus-markers.md` (verified to contain `litmus_sweeps`). Every mention should be linked at least once, especially on the first occurrence (line 29 in "class-level `@pytest.mark.litmus_sweeps`").

**WARNING — Missing cross-link: parquet schema link on line 153 is correct, but the page also references `vector_index`, `step_path`, `parent_path`, `step_index` as primary-key concepts throughout, without linking to the schema until the very last line.** A reader exploring identity fields (table on line 53) would benefit from a forward link to `reference/parquet-schema.md` for the persisted column shape — especially since the table conflates event fields with persisted columns.

**SUGGESTION — Inbound cross-links are sparse.** Only three pages link in: `how-to/writing-tests.md`, `how-to/vector-expansion.md`, `tutorial/03-fixtures.md`, plus `concepts/index.md`. The `outcomes.md` page references this hierarchy implicitly (talks about measurement→vector→step→run rollup) but doesn't link to `step-hierarchy.md`. Cross-page citations should be bidirectional for siblings — outcomes.md should link back to step-hierarchy.md as the structural counterpart.

**SUGGESTION — The "See also" / closing-section pattern is missing.** Both `step-manifest.md` and `outcomes.md` end with explicit "See also" lists. This page just trails off after the last paragraph. Adding a `## See also` block (links to outcomes, step-manifest, parquet-schema, vector-expansion how-to, litmus-fixtures `vectors` entry, litmus-markers `litmus_sweeps` entry) would close the page and improve discoverability.
