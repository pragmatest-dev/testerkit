# Page audit: docs/tutorial/04-limits.md

**Quadrant:** Tutorial (step 4 of 10 — adding limits and pass/fail criteria)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 1 |
| Voice | 0 | 0 | 1 |
| Audience | 0 | 1 | 1 |
| Accuracy | 3 | 2 | 1 |
| Gaps | 0 | 3 | 2 |
| Cross-links | 0 | 3 | 2 |
| **Total** | **3** | **10** | **8** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L38 | The Outcome ladder and rollup semantics are explained before the reader has seen `verify` or `logger.measure` used with an outcome-producing limit. The Outcome table belongs after the inline-limit and marker examples, not between the `Limit` shape and those examples — the reader doesn't yet have a mental model of why any outcome other than PASSED/FAILED matters at this step. |
| SUGGESTION | L117–L126 | The "Characterization mode" section sits after the full comparator table. A reader who wants characterization mode right after the inline-limit example has to scroll past 30 lines of comparators they may not need yet. Consider moving it directly after "Inline limit on the call" since it's the natural "what if I don't want a limit at all?" counterpart. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| SUGGESTION | L139 | Forbidden phrase (name what is bound instead) | "class/function-level limit binding" — name the mechanism: "class/function-level `litmus_limits` marker" |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L5 | Cold cross-page drop — Litmus concept used without linking to its definition | "the same resolution chain" — the limit resolution chain is a core Litmus concept (documented in `how-to/limits.md`); first use here has no link and no inline definition. A new reader has no idea what the cascade is or where to find it. |
| SUGGESTION | L38 | Outcome values SKIPPED / ERRORED / ABORTED / TERMINATED are presented in the table with no explanation of when a test engineer would ever see them in pass/fail context at step 4. The table is useful but the non-measurement outcomes (SKIPPED, DONE, ABORTED, TERMINATED) need even a one-line "you'll see these in the run record" framing for a first-time reader. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L5 | "`logger.measure(..., low=..., high=..., units=...)`" | `TestRunLogger.measure()` signature is `measure(name, value, *, limit=None, outcome=Outcome.DONE, allow_repeat=False)` — there are no `low=`, `high=`, or `units=` keyword arguments. Calling it with those kwargs raises `TypeError` at runtime. | `src/litmus/execution/logger.py:941` |
| CRITICAL | L58–L64 | "`logger.measure("output_voltage", dmm.measure_dc_voltage(), low=3.135, high=3.465, units="V")`" | Same error as above — `low=`, `high=`, `units=` are not accepted by `TestRunLogger.measure()`. The correct call is `logger.measure("output_voltage", ..., limit=Limit(low=3.135, high=3.465, units="V"))`. | `src/litmus/execution/logger.py:941` |
| CRITICAL | L92–L99 | Three `Limit(...)` constructor examples omit `units=` entirely: `Limit(high=1.0, comparator=Comparator.LE)`, `Limit(low=0.0, comparator=Comparator.GE)`, `Limit(nominal=5.0, comparator=Comparator.EQ)` | `Limit.units` is a required field (`units: str` — no default). All three examples raise `ValidationError: units Field required` at runtime. The `units=` argument must be included in every constructor call. | `src/litmus/models/test_config.py:252` |
| WARNING | L36–L37 | "`Outcome.DONE | done | Container outcome — work finished, no measurements`" | `DONE` is not a "container outcome" — it is the measurement-level outcome stamped when `logger.measure()` is called without a limit (the recorder semantic). Source docstring: "recorded value, no judgment evaluated ... characterization-mode measurements that explicitly aren't being judged." Steps and runs may also land `DONE` via rollup, but DONE is primarily a per-measurement outcome. Calling it "no measurements" is incorrect and contradicts the characterization mode section two paragraphs later. | `src/litmus/data/models.py:71` |
| WARNING | L119 | "`logger.measure records the row with measurement_outcome left NULL (unchecked)`" | `TestRunLogger.measure()` defaults to `outcome=Outcome.DONE` and passes that to the Measurement row. The `measurement_outcome` parquet column is stamped `"done"`, not NULL. NULL (`None`) is the outcome when a measurement row was constructed but `check_limit` was never called — a different case. Characterization mode rows are `"done"`, not NULL. | `src/litmus/execution/logger.py:947`, `src/litmus/data/schemas.py:109` |
| SUGGESTION | L9–L21 | `Limit` shape section lists only `low`, `high`, `nominal`, `units`, `comparator` fields | `Limit` also has `characteristic_id` and `spec_ref` traceability fields. They are optional but are central to the traceability story and are referenced in the how-to. Not listing them here is a gap, though they appear in later steps. | `src/litmus/models/test_config.py:253–254` |
| VERIFIED | — | 14 claims verified against source: `Limit` module path (`litmus.models.test_config`), `Comparator` module path (`litmus.models.enums`), `Outcome` module path (`src/litmus/data/models.py`), all 10 `Comparator` enum values (EQ/NE/LT/LE/GT/GE/GELE/GELT/GTLE/GTLT), `litmus_limits` marker name in `LITMUS_MARKER_NAMES`, `verify` signature (`name, value, limit=None, characteristic=None`), `LimitFailure` subclasses `AssertionError`, Outcome severity ladder ordering (skipped=1 < done=2 < passed=3 < failed=4 < errored=5 < terminated=6 < aborted=7), all Outcome string values ("passed"/"failed"/"skipped"/"errored"/"done"/"terminated"/"aborted"). | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| WARNING | L70–L82 | The `litmus_limits` marker section says `verify("output_voltage", ...)` resolves the limit from the marker "without you passing `limit=` explicitly" — but never says what happens if the marker key doesn't match the measurement name. A reader who typos `output_voltge` in the marker and `output_voltage` in the `verify` call will get a `MissingLimitError` with no explanation here. At least a one-sentence "the name must match exactly" rule is needed. |
| WARNING | L40–L53 | The "Inline limit on the call" section shows `verify(...)` with a `Limit` object but never says what happens when `verify` is called without any limit configured anywhere. The reader of step 4 doesn't yet know a `MissingLimitError` is raised — that omission is later addressed in step 3's reference material but not here, where it's most actionable. |
| WARNING | L117–L126 | Characterization mode section says "use `logger.measure` for characterization" but doesn't tell the reader what outcome they'll see in the run record (`done`, not `passed` or `NULL`). A test engineer checking `litmus show` after running the characterization test will be confused by the `done` outcome if they expected something else. |
| SUGGESTION | L86–L116 | The comparator section has no guidance on which comparators need which `Limit` fields. For example, `EQ` requires `nominal=`; `LE` requires `high=`; `GE` requires `low=`. The table shows pass conditions but a reader who sets `comparator=Comparator.GE` without setting `low=` will get a silent always-pass (the lambda short-circuits on `lim.low is None`). A column or note listing required fields per comparator would prevent silent misconfigurations. |
| SUGGESTION | L5 | The intro mentions "the same resolution chain" for both inline and marker limits without explaining what that chain is or pointing to where it is documented. A reader who wants to understand the priority order (sidecar file-level → class → per-test → inline marker → profile → product spec) has no pointer from this page. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| WARNING | L5 | First use of "resolution chain" — no link to `how-to/limits.md` which documents the full cascade. This is the primary conceptual reference for why the marker and inline paths "pass the limit through the same resolution chain." |
| WARNING | L36–L38 | `Outcome` table introduces all seven outcome values but does not link to `concepts/outcomes.md`, which provides the authoritative semantic explanation for each value and the severity ladder. |
| WARNING | L70 | First use of `litmus_limits` marker — no link to `reference/litmus-markers.md#litmus_limits`. The marker name appears in prose and a code block but is never linked to its reference definition. |
| SUGGESTION | L26 | "`Measurement`" links to `../reference/models.md` with no fragment anchor. The models reference has a `### Measurement` section at line 646. Adding `#measurement` would land the reader at the right section rather than the top of a long ERD page. |
| SUGGESTION | L5 | "`limit`" links to `../reference/models.md` with no fragment anchor. The models reference has a `### Outcome` section and a `### Measurement` section but no top-level `### Limit` section — the link lands at the top of the page. Consider linking to `reference/litmus-fixtures.md` (which documents `verify`/`logger` in the context of limits) or to `how-to/limits.md#limit-structure` instead. |
