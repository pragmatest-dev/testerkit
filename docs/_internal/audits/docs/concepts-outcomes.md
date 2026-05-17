# Page audit: docs/concepts/outcomes.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 9 | 6 | 2 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **11** | **18** | **13** |

---

## Ordering findings

The page declares two purposes in its preamble: (1) "plain meaning" and (2) "implementation tables." It then delivers them in that order. The macro structure is sound. The issues are inside each section.

### WARNING — Severity ladder appears AFTER "Plain meaning" but is referenced by it

The "Plain meaning" section keeps using the language of cascade and rollup ("cascaded up from a step", "cascade rollup from any failing step", "run-level rollup from any contained ERRORED"). The reader doesn't see the severity ladder defining what "worst" or "worse" means until later, on line 82. Cascade behavior is a *prerequisite* for understanding the PASSED / FAILED / DONE / etc. run-level meanings, not a downstream detail. Either:
- Move the severity ladder above "Plain meaning", OR
- Make "Plain meaning" describe each outcome in isolation and drop the cascade language from this section entirely, deferring all rollup talk to the ladder section that follows.

### WARNING — Within "Plain meaning", the ordering is severity-descending for ABORTED/TERMINATED/ERRORED but not for the rest

Order today: PASSED, FAILED, DONE, SKIPPED, ERRORED, TERMINATED, ABORTED, None.

Order in the severity ladder: ABORTED, TERMINATED, ERRORED, FAILED, PASSED, DONE, SKIPPED, None.

Order in the per-level implementation tables: ABORTED → SKIPPED → None (severity-descending, matches the ladder).

Three different orders in the same page is jarring. The implementation tables explicitly call out "ordered worst → least severe (the cascade direction)" — apply the same rule to "Plain meaning" so the page has one consistent narrative order. Reading PASSED first feels intuitive ("happy path first") but it forces the reader to context-switch when the page moves into ladder territory. Pick one and stick with it.

### SUGGESTION — The "None" row appears as a peer in "Plain meaning" but as a footnote-style row in every implementation table

"Plain meaning" treats `None` as a normal outcome value with its own subsection. The implementation tables put it last in a separate visual register ("Default; …"). That's fine, but the page should signal explicitly up front that `None` is structurally different (it's the *absence* of an outcome, not a value) so the reader doesn't read "Plain meaning's None section" as if it were just another verdict. The page does say "or `None` if never judged" in the preamble, but then gives None equal weight in the body without re-flagging.

### SUGGESTION — "Severity ladder" section title is generic; "Cascade rule" sub-paragraph buried at the bottom

The severity table is followed by a one-line "Cascade rule:" sentence that defines the operative behavior. That sentence carries the actual rule the reader needs. Promote it to its own labelled paragraph or callout so it doesn't get skimmed past — right now it reads like a footnote to the table when it's the most operational sentence on the page.

---

## Voice findings

The page is in good Concepts/Explanation register — it explains intent, ties names to producer sites, and grounds reasoning in code. The voice issues are local.

### WARNING — Mixing imperative procedural voice with explanatory voice ("An operator seeing `ABORTED` should physically check the bench")

Lines 71, 80: the page slips into instructional voice ("should physically check", "translates this to 'Never Ran'"). Concepts pages explain *why* things are; the *what to do* belongs in a How-To. Either:
- Reframe as descriptive: "ABORTED carries the semantic that the rig state is unknown — downstream tooling and operator runbooks treat it as 'physically inspect required'."
- OR add a small "Operator implications" callout that owns the imperative voice and links to a how-to.

The current "An operator seeing X should Y" is doing how-to work inside concepts.

### SUGGESTION — Inconsistent capitalization of outcome values in prose

Sometimes the page uses `PASSED` / `FAILED` (caps, code-styled), sometimes "passed" / "failed" (lowercase, plain prose). E.g., line 18 "was in range", line 20 "landed `PASSED`", line 47 "judged bad" header but body uses `FAILED`. Pick one convention for outcome names in prose. Code-styled all-caps matches the enum and reads less ambiguous; if you go lowercase for prose, code-style them only when referring to the enum literal.

### SUGGESTION — "TestStand (National Instruments' commercial test executive) convention" is a parenthetical aside that wants a footnote or a cross-link

Line 62: the TestStand parenthetical explains a term but doesn't give the reader anywhere to go. Either drop the parenthetical and assume the audience (test engineers) knows TestStand, or link to a short note on prior-art conventions Litmus inherits.

### SUGGESTION — "this is the 'I logged data' outcome, not a 'good' outcome" is a strong, opinionated phrasing that doesn't get reused

Line 36 lands a memorable framing for DONE. The same kind of one-liner would help anchor ERRORED ("the test code didn't get to say pass or fail"), TERMINATED ("clean stop"), ABORTED ("dirty stop"), and None ("never judged at all"). The DONE one is excellent; the others read as paragraphs without a takeaway. Consider giving each outcome a one-liner anchor in the same place in its section.

---

## Audience findings

The intended reader is "someone who needs to interpret an outcome value in a report, or build tooling on top of outcomes." Test engineers (operators / station builders) and platform consumers (people writing dashboards, MCP tools, integration code). The page swings between these audiences.

### WARNING — Implementation tables assume reader can read Python source and is comfortable with `file:line` references

The per-level tables list `data/models.py:212`, `pytest_plugin/hooks.py:885`, etc. That's appropriate for platform consumers and contributors but excludes the operator audience the "Plain meaning" section was written for. If both audiences are intended, split: a "for operators" callout summarizing meaning, and a "for contributors / tool builders" section with the implementation tables.

Equivalently: state up front *who* the tables are for. The page does say "Implementation tables — every site in the source tree that sets each outcome" in the preamble, but doesn't say "if you're an operator, you can stop after Plain meaning."

### WARNING — "Verdict intent" is jargon introduced without definition

Line 19 talks about "verdict intent" and "_STEP_JUDGMENT_INTENT" (line 33) and "judgment-only sibling" (line 105) without ever defining what verdict intent *is* conceptually. The mechanics show up on line 152-154 as a list of registration sites — but that's mechanics, not concept. Add a one-paragraph explanation: "Verdict intent is Litmus's runtime signal that a test *intended* to judge — fired by any passing rewritten assert and by any measurement that carries a limit. A clean-exit step with verdict intent → PASSED; a clean-exit step without it → DONE." That's the conceptual hinge between PASSED and DONE; right now you have to reverse-engineer it from the rows.

### SUGGESTION — "TestStand convention" assumes the audience knows TestStand

Line 62 ("TestStand convention") and the comparison with WATS in the source code's Outcome docstring assume audience familiarity with NI's commercial test executive. If the audience is broader than ex-TestStand engineers, soften: "This mirrors TestStand's Terminated/Aborted distinction, which is the dominant convention in production test."

### SUGGESTION — Cross-process slot section drops in at the end with no audience signal

The "Slot orchestrator (cross-process)" section appears under "Run" but introduces `SlotResult.outcome` as strings (not the enum), aggregated from `subprocess.Popen.returncode`. This is a completely different audience — someone running multi-DUT sessions, debugging slot orchestration. Either:
- Move it to a sibling sub-page (orchestrator outcomes deserve their own page), OR
- Add a one-sentence "If you're not running multi-DUT, skip this section" lead-in.

---

## Accuracy findings

I cross-checked every `file:line` reference and every behavioral claim against the source tree. There is significant line-number drift — at least nine references point to wrong or nonexistent lines, and one cited file path (`verify.py`) is incorrect. Behavioral claims are mostly accurate but a few overstate / mis-attribute.

### CRITICAL — `verify.py` is not the right path; should be `execution/verify.py`

The page repeatedly cites `verify.py:97`, `verify.py:108`, `verify.py:110` (e.g. lines 105, 111-114 of the doc). There is no `src/litmus/verify.py`. The actual file is `src/litmus/execution/verify.py`. The page is otherwise consistent in using prefixes like `data/models.py`, `pytest_plugin/hooks.py`, `execution/logger.py` — `verify.py` looks unprefixed by oversight.

### CRITICAL — `data/backends/parquet.py:1284` and `:1287` do not exist

Lines 146-147 of the doc cite:
- `data/backends/parquet.py:1284` — "Parquet readback fallback when any contained vector has `outcome == FAILED`"
- `data/backends/parquet.py:1287` — "Parquet readback fallback when no failed vectors"

The file is only 1067 lines long. The actual step-outcome fallback logic is at lines 1014-1020:
```python
step_outcome_str = step_sample_row.get("step_outcome")
if step_outcome_str:
    step_outcome = Outcome(step_outcome_str)
elif any(v.outcome == Outcome.FAILED for v in vectors):
    step_outcome = Outcome.FAILED
else:
    step_outcome = Outcome.PASSED
```

Update the references and reflect that the fallback only emits FAILED or PASSED (no other branches).

### CRITICAL — `data/backends/parquet.py:764` for "aborted" fallback is wrong

Doc line 162 cites `parquet.py:764` as where the parquet subscriber stamps `"aborted"` directly when `close()` runs without `RunEnded`. Line 764 is in `read_step_results()` (reading step results from a sibling parquet), not the aborted-stamping site.

The actual fallback is in `materialize_run_to_parquet` at lines 661-662:
```python
ended_at = run_ended_at if run_ended_at is not None else _utcnow()
final_outcome = outcome if outcome is not None else "aborted"
```

And the comment block at lines 651-654 documents it. The page should also note this is the *materializer*, not the *subscriber* — the architecture has changed since this page was written (per file comments, "No subscriber class needed — projection lives on the accumulator, writing lives here").

### CRITICAL — `execution/logger.py:773` for vector / step / run cascade is wrong

Doc lines 128-131 cite `execution/logger.py:773` as where `log_measurement` cascades into vector, step, and run. The actual cascade is at lines 852-854:
```python
vector.outcome = escalate_outcome(vector.outcome, measurement.outcome)
step.outcome = escalate_outcome(step.outcome, measurement.outcome)
self.test_run.outcome = escalate_outcome(self.test_run.outcome, measurement.outcome)
```

Line 773 is inside the `_emit_step_event` helper, not the cascade. Doc lines 145, 147 cite `:774` and `:775` for the same site; same correction.

### CRITICAL — `execution/logger.py:782-788` for verdict intent registration is wrong

Doc line 154 cites lines 782-788 as where `log_measurement` calls `mark_step_judgment_intent` when the measurement has a limit. The actual call is at lines 861-867:
```python
if measurement.limit_low is not None or measurement.limit_high is not None:
    try:
        from litmus.pytest_plugin.hooks import mark_step_judgment_intent
        mark_step_judgment_intent(str(step.id))
    except ImportError:
        pass
```

Note the actual code only checks `limit_low` / `limit_high` — the doc's plain-meaning section also mentions `nominal` and `comparator` as triggering verdict intent (line 19), but the source does not include `nominal` in the verdict-intent check. Either the source is missing it or the doc is overclaiming.

### CRITICAL — `execution/logger.py:1080` for `RunEnded.outcome=None` is wrong

Doc line 169 cites `execution/logger.py:1080` as where `RunEnded.outcome=None` is emitted. Line 1080 is inside the docstring of the `_check_duplicate_measurement_key` method, not the RunEnded site.

The actual emission is in `finalize()` at lines 1151-1156:
```python
self._event_log.emit(
    RunEnded(
        session_id=self._session_id,
        run_id=self.test_run.id,
        outcome=self.test_run.outcome.value if self.test_run.outcome else None,
    )
)
```

### CRITICAL — All `pytest_plugin/hooks.py` line numbers are off by ~470 lines

Doc references vs actual:
- `:874` (PASSED) → actual `:1338`
- `:876` (DONE) → actual `:1338` (same conditional; PASSED and DONE share one line)
- `:881` (SKIPPED) → actual `:1342`
- `:883` (FAILED) → actual `:1344`
- `:885` (ERRORED) → actual `:1346`
- `:920` (setup-phase skip) → actual `:1381`
- `:922` (setup ERRORED) → actual `:1383`
- `:963` (TERMINATED) → actual `:1424`
- `:165-171` (`pytest_assertion_pass`) → actual `:167-172`
- `:836` (cascade rollup citation, lines 163-168) → actual lines 1338-1346 (the same `_stamp_step_from_call_outcome` site). Line 836 is in a docstring about `enable_assertion_pass_hook`.

The file has grown substantially since this page was authored. Every hooks.py reference needs updating.

### CRITICAL — `data/models.py` severity table line refs are off

Doc lines 88-95 cite `data/models.py:126` through `:132` for the severity ranks. Actual ranks are at lines 124-130:
```python
_OUTCOME_SEVERITY: dict[Outcome, int] = {
    Outcome.ABORTED: 7,      # :124
    Outcome.TERMINATED: 6,   # :125
    Outcome.ERRORED: 5,      # :126
    Outcome.FAILED: 4,       # :127
    Outcome.PASSED: 3,       # :128
    Outcome.DONE: 2,         # :129
    Outcome.SKIPPED: 1,      # :130
}
```

Doc line 95 cites `:167` for "no-judgment placeholder". The return statement is line 167, but the `-1` rank assignment is at lines 165-166. Either citation works but list the right one.

### CRITICAL — `execution/harness.py` line refs are off

- Doc cites `harness.py:900` for "no-logger branch performs the same measurement cascade" (line 135). Actual no-logger cascade is at lines 902-903.
- Doc cites `harness.py:1128` for vector FAILED on AssertionError (line 129). Actual is line 1131.
- Doc cites `harness.py:1137` for vector ERRORED on non-Assertion exception (line 128). Actual is line 1140.
- Doc cites `harness.py:1244` for step cascade from vectors (lines 145, 146). Actual cascade is at line 1249 (inside the `step()` context manager finally block).

### WARNING — `data/models.py` Measurement / TestVector / TestStep default-outcome line refs

- Doc line 116 cites `data/models.py:182` for Measurement default outcome `None`. Actual is line 180.
- Doc line 133 cites `data/models.py:262` for TestVector default. Actual is line 262 (matches).
- Doc line 150 cites `data/models.py:301` for TestStep default. Actual is line 299.

### WARNING — `slot_runner.py:198` is wrong

Doc line 181 cites `slot_runner.py:198` for `SlotResult(... outcome="errored")` initialization. Line 198 is in `_build_slot_env`/coordinator setup. The actual `SlotResult(slot_id=slot_id, outcome="errored")` is at line 230.

The other slot_runner refs (`:338`, `:614-620`) are correct.

### WARNING — `data/models.py:195` for `Measurement.check_limit()`

Doc line 105 cites `data/models.py:195` as the `check_limit` recorder. Actual is `data/models.py:193` (function definition).

### WARNING — `data/models.py:212`, `:224`, `:226` for ERRORED / DONE / PASSED-FAILED

These references are accurate — verified at lines 212 (`Outcome.ERRORED`), 224 (`Outcome.DONE`), 226 (PASSED/FAILED ternary).

### WARNING — `client.py` line refs are accurate

All client.py references (`:123`, `:129`, `:130`, `:137`, `:143`, `:185`, `:187`, `:237`, `:243`, `:253`) match the source. Good.

### WARNING — "VectorBuilder.skip" claim at vector level (line 132)

Doc says SKIPPED is produced by `VectorBuilder.skip(...)` at the vector level. The source confirms (`client.py:143`). But the doc's "Plain meaning" section for SKIPPED on line 41-43 says skip is *step-level* and *run-level* only, then on line 43 mentions "vector / client builder" exposes `.skip(message)`. The asymmetry needs reconciling — either SKIPPED is also a measurement / vector outcome path worth documenting in the "Plain meaning" tier, or you should explicitly note "the catch-all client API also exposes skip at the vector level, but it's not produced by the runtime cascade."

### WARNING — "An exception in a called function does not produce an ERRORED measurement" (line 118)

This is correct, but the doc's measurement-level ERRORED row above (line 111) does say "verify('name', None) — same cause via the judgment-only path." Verify the truth: `verify()` raises `MissingLimitError` when there's no limit; passing `value=None` with a resolvable limit does indeed lead to `_compute_outcome` returning `ERRORED` (`execution/verify.py:108`). So the claim holds. Just note that the measurement-row gets ERRORED *only* via `logger.measure(outcome=ERRORED)` from verify — not via `check_limit()` on a verify path — which the page doesn't quite spell out.

### SUGGESTION — `escalate_outcome` cited at `:167` for the `-1` placeholder

Doc line 95 says `escalate_outcome at data/models.py:167 (no-judgment placeholder)`. Line 167 is `return current if cur_sev >= inc_sev else incoming`. The `-1` placeholder is in lines 165-166. The function itself starts at line 140. Cite the line that does the `-1` assignment, not the return line, if you want to point at the placeholder semantics.

### SUGGESTION — "harness.py:900" comment claim is mostly right

The doc says "the harness's no-logger branch performs the same measurement cascade." Source confirms — but the cascade in the no-logger branch *only* updates the vector outcome (line 903), not step or run. The doc says "same measurement cascade" which slightly overstates: it's the same *single-level* cascade up to vector, not the full ladder. Worth a one-clause hedge.

---

## Gaps findings

This page is dense and well-scoped. The main gaps are around the "why" the page deliberately defers, but a couple of operational details are missing.

### CRITICAL — No mention of the materializer / runs-daemon split

The page repeatedly cites "the parquet subscriber" as the site of the aborted-fallback (line 71, 162). The actual code path is `materialize_run_to_parquet` in the runs-daemon's event-dispatch loop. The comment at `parquet.py:556-562` says explicitly: "No subscriber class needed — projection lives on the accumulator, writing lives here." Calling it "the parquet subscriber" is at best historical, at worst misleading for anyone trying to find the code.

This matters because ABORTED is the *only* run-level outcome that's not produced by the cascade — it's a side-effect of *who writes the row*. The page should briefly explain that the runs daemon materializes parquet from event accumulators, and the fallback fires when the materializer is asked to write a run that never saw `RunEnded`. Without that, the ABORTED row reads as magic.

### WARNING — `escalate_outcome` semantics for "equal" outcomes is unstated

The cascade rule says "worse wins". The implementation (`data/models.py:165-167`) uses `cur_sev >= inc_sev` — i.e. ties go to the *current* outcome. That's a deliberate behavioral choice (e.g. if two steps both FAIL, the second FAIL doesn't "re-stamp" the first; if the run is already FAILED and a measurement comes in FAILED, no change). Worth a sentence — without it, "worse wins" is ambiguous on ties.

### WARNING — No mention of how outcomes flow into the operator UI / reports

The page explains how outcomes are stamped and cascaded, but never says where the operator *reads* them. Operator-facing surfaces (run list, run detail, metrics page) all consume `run_outcome` / `step_outcome` columns from parquet. A small "where outcomes surface" section linking to the relevant UI / report pages would close the loop for the operator audience the page partially serves.

### WARNING — "Never Ran" display logic is mentioned but not explained

Line 80 says "the display layer translates this to 'Never Ran'". The reader has no idea what the display layer is, where it lives, or what `outcome IS NULL` + finalized-state interaction means. Either link to `concepts/step-manifest.md` (which covers it briefly) or add a one-paragraph explanation. The step-manifest page already says: "the display layer derives 'Never Ran' from `outcome IS NULL` plus the run's finalized state." Cross-link it.

### WARNING — Retry interaction with outcomes is undocumented

`TestVector.retry` exists; `run_with_retry` re-runs vectors until `Outcome.PASSED`. What happens to the *retried* vector's outcome record? Does a vector that FAILED then PASSED stamp the final outcome as PASSED with retry=N, or does the run carry both rows? This affects how reports / metrics interpret retried tests. The page doesn't cover it.

### SUGGESTION — No example timeline / trace showing outcomes at each level

The page is mostly definitions and tables. A concrete worked example would help — e.g., a test with one PASSED measurement and one FAILED measurement and one ERRORED measurement, walking through the resulting `Measurement.outcome` / `TestVector.outcome` / `TestStep.outcome` / `TestRun.outcome` values via the cascade. The step-hierarchy.md page does this for the hierarchy; mirror it here for outcomes.

### SUGGESTION — DONE vs SKIPPED at run level is ambiguous

Line 167 says "Cascade rollup from step(s) that ran cleanly without verdict intent" produces DONE. Line 168 says "Cascade rollup where the only contained outcomes were SKIPPED" produces SKIPPED. Both are plausible. The "Plain meaning" section on line 42 says "a run with all-skipped steps doesn't usually emit anything meaningful here". Reconcile — either SKIPPED-only runs *do* stamp SKIPPED (cascade) or they don't.

---

## Cross-links findings

### CRITICAL — No outbound links from this page

The page does not link to any other docs page. Concepts pages should anchor in the wider doc graph — to the related concepts the reader needs (`step-hierarchy`, `step-manifest`, `event-log`), to references (`models.md`, `parquet-schema.md`), and to relevant how-tos (`limits.md`, `traceability.md`). The page is a doc-graph dead-end. Multiple peer concept pages link *to* this page, but it links *out* to nothing.

Minimum suggested outbound links:
- `concepts/step-hierarchy.md` — for the level hierarchy (vector / step / run) the page assumes.
- `concepts/step-manifest.md` — for the "Never Ran" / `outcome IS NULL` row semantics.
- `reference/models.md` — for the `Outcome` enum source-of-truth.
- `reference/parquet-schema.md` — for the `run_outcome` / `step_outcome` / `measurement_outcome` column definitions.
- `how-to/limits.md` — for how limits get attached, since that's the trigger for measurement-level PASSED/FAILED.

### WARNING — Inbound links exist but are not reciprocated

The following pages link in:
- `concepts/index.md:23`
- `concepts/step-hierarchy.md:3, 15, 143`
- `concepts/step-manifest.md:71` (informal `concepts/outcomes.md` reference, not a markdown link)
- `reference/parquet-schema.md:282` (references `Outcome` source-of-truth without linking)

The hierarchy page even names this page as a peer ("Pair it with Outcomes for what each level's verdict means"). Reciprocate by linking back from outcomes.md to `step-hierarchy.md` for the hierarchy explanation.

### WARNING — `reference/parquet-schema.md` "Outcome values" section overlaps but isn't cross-linked

`docs/reference/parquet-schema.md:270` has a section titled "Outcome values" that defines what each outcome string means in the parquet column. It even cites the source: `src/litmus/data/models.py (Outcome)`. The two pages should explicitly link each other ("for column-level details, see Parquet schema"; "for outcome semantics, see Outcomes concept").

### WARNING — `reference/models.md` covers `Outcome` enum but has its own contradicting summary

`docs/reference/models.md:629-644` defines the Outcome enum and its own one-line severity ladder: `skipped < done < passed < failed < errored < terminated < aborted`. That matches this page. But models.md describes DONE as "Container outcome — work finished, no measurements" which differs from this page's "recorded value, no judgment evaluated" (which itself is consistent with the source docstring at `data/models.py:71-76`).

The models.md gloss is wrong / outdated and should be aligned. Either way, the two pages need to link each other and use the same one-liner.

### SUGGESTION — Link from "TestStand convention" to a Why / Concepts page on conventions

If you have (or plan) a `concepts/lineage.md` or "Why these names" page, link from line 62 to it. Otherwise consider adding a short callout explaining the TestStand mapping.

### SUGGESTION — Link from the slot section to a Slot orchestrator concept / how-to page

The "Slot orchestrator (cross-process)" section introduces `SlotResult`, `SlotRunner._monitor_slot`, `SessionEnded.outcome`. None of these have any context for a first-time reader. If a `concepts/multi-dut.md` or `how-to/multi-dut-testing.md` exists (the latter does), link to it. As-is the section reads as a teaser with no follow-on.

---

## Coordinator note

Tool environment did not expose an `Agent`/`Task` tool, so the six dimension audits were performed inline by reading the page and the cited source files directly rather than dispatched as sub-agents. The structure (one block per dimension, severity-tagged findings) matches the requested report format; the accuracy block in particular cross-checks every `file:line` reference against the actual source tree as of audit date.
