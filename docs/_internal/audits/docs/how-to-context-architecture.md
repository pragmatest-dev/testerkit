# Page audit: docs/how-to/context-architecture.md

**Quadrant:** How-to (using the context fixture — stamping stimulus inputs, observations, hierarchical run/step/vector scoping)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 1 | 3 | 2 |
| Audience | 1 | 4 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 1 | 3 | 3 |
| **Total** | **8** | **20** | **13** |

---

## Ordering findings

**WARNING — Title and lead don't match the page's actual job.**
Lines 1-5. The title "Context Architecture" and the lead read like an explanation/reference page (Diátaxis: Explanation), not a how-to. The user requested quadrant is "using the context fixture — stamping stimulus inputs, observations, hierarchical run/step/vector scoping." A how-to opens with a task the reader wants to accomplish ("Read run / station / product state from inside a test", "Skip slow setup when a sweep param hasn't changed"), not with a noun-phrase architecture description. Reorder so the first sentence states what the reader will do, not what the abstraction is.

**WARNING — Payoff section is buried.**
The most actionable content (`context.changed()` — skipping 20-min thermal soak) lives in section 6 of 9 (line 70+). For a how-to, that should be section 1 or 2. Readers who came for the practical win have to wade past metaphysics (read/write split, two shapes, glance table, source-of-truth table, stash internals) to reach it. Promote `context.changed()` to immediately follow a one-paragraph orientation; sink the source-of-truth and stash-internals tables to a "How it works" appendix.

**SUGGESTION — Section sequence mixes "how-to" with "concepts."**
Current order: read/write split → two shapes → at-a-glance → where each value comes from → prior-context memory → payoff → scratch-state → data flow → see also. Three of these (read/write split, where each value comes from, prior-context memory) are conceptual. A how-to flow would be: see also-style intro → "Read run/station/product" → "React to sweep changes (`changed()`)" → "Look back at the previous value (`last()`)" → "Record observations (`observe()`)" → "Mutable scratchpad" → background reading.

**SUGGESTION — "Two shapes, one result" precedes any reason to care.**
Section 3 demonstrates aggregate vs destructured signatures before the reader has been told what's *in* context. Flip: introduce attributes first, then show that destructuring fixtures works as a stylistic alternative. As written, the destructured example (lines 28-29) doesn't even take `context` — making the section's claim ("both forms resolve to the same cached fixture instances") confusing because no one would write it that way.

---

## Voice findings

**CRITICAL — Page contradicts itself on whether `context` is read-only.**
Line 3: "All values are sourced from ContextVars seeded by session fixtures; **tests cannot mutate the shared view.**"
Line 12 (table row): "`context` | **Read-only**"
Line 47: "`context.observe("dut_temp", 42.3)  # record an environmental observation`"

`context.observe()` and `context.configure()` both *do* mutate the context (see `Context.observe` at `src/litmus/execution/harness.py:190` and `Context.configure` at `:179`). The page promises immutability and then demonstrates mutation in the same code block. Either narrow the claim ("the run/station/product roll-up is read-only; per-iteration scratch is writable via `observe()` / `configure()`") or drop "tests cannot mutate" entirely.

**WARNING — "Read-only ambient roll-up" is jargon that adds no value.**
Line 3. Two abstract modifiers stacked: "ambient" and "roll-up." A how-to reader wants "the run / station / product / sweep state the test can read." Rewrite without the adjectives.

**WARNING — Parenthetical asides bury the actual rule.**
Line 5: "DUT identity intentionally lives at `context.run.dut` — there is no `context.dut` attribute because the bare `dut` fixture is the live DUT driver (a different concept). For the same reason `context.instruments` is not exposed: take the `instruments` fixture as a test argument when you need it." Two design rationales in one paragraph, neither asked-for. A how-to says: "To read DUT identity: `context.run.dut.serial`. The `dut` fixture is a different thing — the live driver." Save the "intentionally" / "for the same reason" justifications for a concepts page.

**WARNING — Inline grep snippet is a non-sequitur.**
Line 15: "`grep -E 'verify\(|logger\.measure'` finds every write." This is a true statement, but it sits at the end of a paragraph explaining `verify` vs `logger`. The reader isn't searching a codebase right now; they're trying to write a test. Either move it to a "see also" or drop it.

**SUGGESTION — Imperative voice would suit how-to better.**
The page narrates ("Both forms resolve to the same…", "DUT identity intentionally lives at…"). How-to prose is imperative: "Use `context.get_param('vin')` to read a sweep value", "Call `context.observe(...)` to stamp an environmental reading on the row."

**SUGGESTION — "The payoff" headline is presumptuous.**
Line 70. "Payoff" frames a benefit the page hasn't asked the reader to invest in yet. Use a task-shaped heading: "Skip expensive setup with `context.changed()`."

**CRITICAL note already counted above (the read-only contradiction is the single Critical).**

---

## Audience findings

**CRITICAL — Page targets framework authors, not test engineers.**
The audience for a how-to is the test engineer writing `tests/test_*.py`. This page repeatedly addresses someone *implementing* the platform: line 3 ("sourced from ContextVars seeded by session fixtures"), lines 52-59 (table "Source ContextVar / fixture" with entries like `get_current_logger().test_run`, `_litmus_push_limits autouse`), lines 62-68 (stash key implementation, `pytest.StashKey`, "no cross-talk"). A test engineer doesn't need to know that `run` comes from `get_current_logger().test_run` to call `context.run.dut.serial`. Most of this belongs on a `concepts/context-architecture.md` page, with the how-to here keeping only the user-visible API and one diagram.

**WARNING — Programmer jargon for hardware test engineers.**
Per [feedback_no_jargon_for_test_engineers.md], this page is dense with platform-internals vocabulary that the test engineer doesn't speak: "ContextVar(s)" (lines 3, 52, 56-59), "parent stash node" (line 63), "pytest.StashKey" (line 63), "iteration-state attributes" (line 3), "cached fixture instances" (line 19), "ambient roll-up" (line 3). A test engineer reading "merged with parent chain" (line 57) has no model for what the parent chain is.

**WARNING — Audience assumed already proficient in pytest internals.**
Line 32: "`context.params['vin']` and native `request.node.callspec.params['vin']` point at the same dict." Most test engineers have never opened `request.node`. Either drop the equivalence, or frame it as "if you're already comfortable with pytest internals…".

**WARNING — Bizarre fixture in the scratchpad example.**
Lines 94-103. The example fixture is named `xstate` (mystery prefix `x`) and stores `first_calibration_ts`. A test engineer reading this has no model for why a calibration timestamp would persist across iterations, or what they'd ever do with one. Pick a recognizable use case (e.g., "remember the warmest temperature seen so far in this class") and use a plain name like `class_state` or `seen`.

**WARNING — "20 plugin fixtures" name-checks scale rather than helping.**
Line 124. "Litmus fixtures — all 20 plugin fixtures with signatures." For a how-to, the count is meaningless — what matters is which one solves the reader's problem. Drop "all 20."

**SUGGESTION — Tutorials already taught some of this; lean on them.**
Tutorial 05-configuration.md already covers `context.params`, `context.get_param()`, and `context.changed()` (see lines 110, 119, 140 of that file). This page re-teaches them. Trim to "Tutorial 05 introduced X; this page goes deeper on Y, Z."

**SUGGESTION — "DUT" not defined on first use.**
Line 5: "DUT identity intentionally lives at `context.run.dut`." A how-to in the production-test workflow can assume DUT, but the page is the first the reader may hit in this group; one parenthetical on first use ("Device Under Test") helps onboarding without insulting experts.

---

## Accuracy findings

**CRITICAL — "Tests cannot mutate the shared view" is false.**
Line 3 (also flagged under Voice). `Context.observe` at `src/litmus/execution/harness.py:190-207`, `Context.configure` at `:179-188`, plus `configure_all` / `observe_all` / `set_params` / `set_observations` (lines 250-282) all mutate `self._params` or `self._observations`. The class is mutable; this page's own line 47 example (`context.observe("dut_temp", 42.3)`) writes to it.

**CRITICAL — `context.limits["output_v"]` is not the resolved limit.**
Line 42 comment: `# function (resolved from markers + sidecar + product)`. `LimitsView.__getitem__` (`src/litmus/execution/harness.py:61-62`) returns a `MeasurementLimitConfig`, not a resolved `Limit`. Resolution happens via `Context.get_limit` (line 406) or inside `verify` / `logger.measure`. The comment misleads readers into thinking `ctx.limits[name]` already has `.low` / `.high` / `.nominal` evaluated.

**CRITICAL — `context.params` is NOT the same dict as `callspec.params`.**
Line 32: "`context.params['vin']` and native `request.node.callspec.params['vin']` point at the same dict."
`Context.params` is a `@property` (`harness.py:320-327`) that rebuilds a fresh dict on every call by merging the parent-chain. `callspec.params` is pytest's own dict. They contain the same *values* (for parametrize cases), but they are not "the same dict" — mutating one does not affect the other, and they are not even the same object. Either rewrite to "carry the same values" or drop the equivalence claim.

**WARNING — Glance code block mixes attribute and dict-subscript syntax confusingly.**
Lines 41, 42: `context.params["vin"]`, `context.limits["output_v"]`. Both are valid (params is a property returning a dict, limits is a Mapping view), but the *idiomatic* access shown elsewhere in the codebase and in `reference/litmus-fixtures.md` is `context.get_param("vin")`. Pick one form per concept and stick with it; mixing them in adjacent lines invites readers to think they're different things.

**WARNING — "Cached fixture instances" claim is questionable.**
Line 19: "Both forms resolve to the same cached fixture instances." The `context` fixture is *function-scoped* (`pytest_plugin/__init__.py:974-977`), so it's freshly built per test, not cached across tests. Within a single test, pytest does cache per-test, so any reference to `context` returns the same instance — but "cached fixture instances" reads as if pytest is reusing across tests, which is wrong for function scope. Either rephrase ("pytest dedupes fixtures within a test, so both forms get the same `Context` object") or drop the line.

**WARNING — The two code examples don't show the contrast the prose promises.**
Lines 22-29. "Aggregate" calls `context.get_param("vin")` then assigns to `vin` (unused). "Destructured" does not take `context` at all but is otherwise identical. There's no "destructured" form on display — neither example takes `vin` as a parameter, which is how `litmus_sweeps` actually destructures. The intended contrast (`def test(self, vin, ...)` vs `def test(self, context, ...)`) is not what's printed. Fix the code or rewrite the prose.

**WARNING — Data-flow table mixes Measurement, TestStep, and TestRun fields.**
Lines 109-117. The table claims one measurement row contains "operator, phase, pytest node id, git commit, param values." These are not fields on `Measurement` (see `data/models.py:170-191` — Measurement has name/value/units/limit_*/outcome/characteristic_id/spec_ref/dut_pin/instrument_*). Operator/phase/git_commit are on `TestRun` (`:385-433`); pytest node id is on `TestStep` (`:284-296`). The page is correct that all of these end up in *the parquet row* (per `reference/parquet-schema.md`, where rows are denormalized step + run + DUT + station + measurement context), but the framing "Each `verify` / `logger.measure` call produces one measurement row containing…" suggests they're on the Measurement model. Rephrase as "the parquet row produced by each call" and link `reference/parquet-schema.md`.

**SUGGESTION — `logger` row in the read/write table omits `record`.**
Line 13: lists `logger` as "Pure recorder (no raise); used for characterization rows." But `logger` also exposes `record(key, value)` (see `logger.py:1116`) for non-measurement key-value writes. Either name it ("`measure` + `record`") or scope the row to measurements only.

**SUGGESTION — "Always available" claim about `context` deserves a footnote.**
The page (and writing-tests.md line 18) describes `context` as always present. The fixture is function-scoped and resolves on every test, but `context.run` returns `None` outside a run (e.g., pytester subprocesses without the harness), and `context.station` / `.product` return `None` for bringup tier. Worth noting that the *fixture* is always present but its *attributes* can legitimately be `None`.

---

## Gaps findings

**CRITICAL — `context.configure()` is completely absent.**
The page documents `observe()` (line 47) but never mentions `configure()`, which is the in_* sibling. `Context.configure` exists (`harness.py:179`), `tutorial/03-fixtures.md` uses it (line 94), `reference/models.md` uses it (line 896), and `writing-tests.md` lists it under context verbs (line 18 — "get_param, changed, last, observe, configure"). For a how-to on "stamping stimulus inputs," omitting `configure` is the most consequential gap on this page.

**CRITICAL — No example shows `context.observe` or `context.configure` actually used in a test that records to parquet.**
The page mentions `context.observe("dut_temp", 42.3)` in a code-glance block (line 47) and then never returns to it. The reader has no end-to-end example showing: take a reading from a temperature probe, stash it on context, see it show up as an `out_dut_temp` column. Without that, "observations land in parquet" is an unsupported claim. The tutorial 03-fixtures.md has this example (lines 92-100) — port it or link to it.

**WARNING — `context.characteristics` is missing.**
The reference (`reference/litmus-fixtures.md:179`) lists `context.characteristics`. It is part of the same per-test read API this page is documenting. The "context at a glance" block omits it.

**WARNING — `context.get_limit(name)` is missing.**
`Context.get_limit` (`harness.py:406-431`) is the resolved-limit accessor — distinct from `context.limits[name]`, which returns the config. The page shows the config form (line 42) and never mentions the resolution method. Test engineers writing adaptive logic ("if the limit is tight, take more samples") need this.

**WARNING — `last()` is in the glance block but never explained.**
Line 46: `context.last("output_voltage")  # last recorded value of this measurement name`. The comment is misleading — `last()` actually looks up the prior context's `_params` first, then `_observations` (`harness.py:225-244`); it does NOT consult the measurement log. A reader expecting "last recorded measurement" will be surprised when `last("output_voltage")` returns `None` because they used `verify("output_voltage", ...)`, not `context.observe("output_voltage", ...)`. Fix the comment or fix the example, and dedicate a short section to `last()` semantics.

**WARNING — No mention of the bringup tier (no station, no product) → many context attributes are None.**
Important practical concern for a how-to: what does the test do when `context.station is None` (no station YAML)? The page lists "StationConfig | None" in passing (line 39) but never tells the reader how to write defensive code, or that this is normal in the tutorial / dev workflow. Add a "what's `None` and when" subsection.

**WARNING — Read/write split table omits the writable methods on `context` itself.**
Line 9-13. The table positions `context` as purely Read-only and `verify` / `logger` as Write. Yet `context.observe` / `context.configure` also write (to the parquet `in_*` / `out_*` columns). The table should either acknowledge `context` as "Read + sweep-context writes" or split out a fourth row.

**SUGGESTION — No troubleshooting / "common mistakes" subsection.**
Useful entries for a how-to: `context.changed()` returns True on first iteration (people forget); `context.last()` returns `None` if you never `observe`d / `configure`d that key (people expect it to read measurements); `context.dut` is a `AttributeError` (people expect it).

**SUGGESTION — No "when NOT to use context" subsection.**
Closes a common ambiguity: don't reach for `context.run.station_id` inside a fixture-level helper if `station_config` is in scope — take the fixture. Don't use `context.observe` for a measurement you'd verify; use `verify`. Don't use `context.params` to read pytest-vanilla parametrize values for a single test (just take the param as an argument).

---

## Cross-links findings

**CRITICAL — "See also" misses three highly relevant pages.**
Lines 121-124 link only writing-tests, vector-expansion, litmus-fixtures. Missing:
- `reference/parquet-schema.md` — directly relevant to the "Data flow to parquet" section (the whole table on lines 109-117 is a summary of that page).
- `how-to/traceability.md` — the in_* / out_* column naming and the auto-traceability fields originate there.
- `concepts/fixtures.md` or a future `concepts/context-architecture.md` — for the "Why" of the design (currently leaks into this how-to via lines 3-5, 19, 32, 52-68).

**WARNING — Inbound links are nearly nonexistent.**
A search across `docs/` shows only `docs/how-to/index.md:22` linking to this page. No other how-to or tutorial points here. If the page is supposed to be a hub for "everything about context," then `writing-tests.md` (which introduces context), `vector-expansion.md` (which uses `context.changed`), and `tutorial/05-configuration.md` (which teaches `context.params` / `.changed`) should all link here at the point where they mention `context`. Without those inbound links, the page is an orphan.

**WARNING — "Test Vectors guide" cross-link duplicates content.**
Line 123 sends readers to `vector-expansion.md` for "sweep shapes, generators, loop ordering" — but `vector-expansion.md` lines 191-214 *also* teach `context.changed("temp")` with the exact same chamber/PSU/load example structure. This page should either (a) link to vector-expansion's `context.changed` section specifically and remove its own "payoff" example, or (b) own the `context.changed` story and have vector-expansion link here. As-is, the two pages duplicate.

**WARNING — Reference to `context.connections` has no link.**
Line 3 mentions `context.connections`; line 44 says it iterates `FixtureConnection`. Neither links to `reference/litmus-fixtures.md#connections` (line 187) or to `how-to/spec-driven-testing.md` (which is the workflow this drives). Either inline a one-line explanation or link out.

**SUGGESTION — Link `ContextVars` term on first use.**
Line 3. `writing-tests.md:22` already does this with a deep-link to the Python stdlib docs. Mirror that link here.

**SUGGESTION — Link `StashKey` term.**
Line 63 names `pytest.StashKey` without context. If the section stays (and per Audience findings, it shouldn't), link to `https://docs.pytest.org/en/stable/reference/reference.html#pytest.Stash`.

**SUGGESTION — Add a back-link from `writing-tests.md`'s core-fixtures table.**
The table at `writing-tests.md:14-22` mentions `context` and its verbs (`get_param`, `changed`, `last`, `observe`, `configure`). Add `[Context architecture](context-architecture.md)` to its "See also" — currently it links Mock mode, traceability, etc., but not this page.
