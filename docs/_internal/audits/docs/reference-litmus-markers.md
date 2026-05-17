# Page audit: docs/reference/litmus-markers.md

**Quadrant:** Reference
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 1 | 2 |
| Accuracy | 1 | 3 | 3 |
| Gaps | 0 | 4 | 3 |
| Cross-links | 0 | 2 | 4 |
| **Total** | **1** | **12** | **16** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L7-20 ("No-stacking rule") | The page opens with a rule that uses every marker name (`litmus_sweeps`, `litmus_limits`, `litmus_mocks`, `litmus_prompts`) and the term `parametrize` before any of them have been introduced. A first-time reader who lands on this page (the canonical reference) hits "Multi-entry payloads (a list of dicts for sweeps/mocks, multiple kwargs for limits/prompts) consolidate onto one marker" with no idea what a multi-entry payload looks like. Consider moving the no-stacking rule to the end as a constraint, or putting an at-a-glance table of the seven markers up top before any rules. |
| 💡 SUGGESTION | L3 (intro) | The intro mentions "the sidecar YAML" and `TestEntry` before defining either. A one-line sentence ("the sidecar is `tests/test_<module>.yaml`, see [Test configuration](configuration.md#test-configuration)") inline at first use would prevent a reader new to the page from having to chase a link. |
| 💡 SUGGESTION | Section ordering | The seven markers are listed in `LITMUS_MARKER_NAMES` order, which is a code ordering, not a reader ordering. For a reference page the most-used markers (`litmus_limits`, `litmus_sweeps`, `litmus_mocks`) reasonably lead, but `litmus_characteristics` and `litmus_connections` appear before `litmus_retry` and `litmus_prompts` even though characteristics/connections are the most specialized (require a product spec + fixture). Consider regrouping under sub-headings: "Per-test data" (`limits`, `sweeps`, `mocks`), "Operator + retries" (`prompts`, `retry`), "Spec-driven" (`characteristics`, `connections`). |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L84 | Hedging / vague | "both work; both feed..." — fine, but the next sentence "Use `litmus_sweeps` when you want range expanders... use `@pytest.mark.parametrize` when you want pytest's `pytest.param(..., id='...')`" reads cleanly; nothing to fix there. (No real finding — keep this row only if you flag the next item.) |
| 💡 SUGGESTION | L84 | Hedging | "both work" — clearer as "both produce the same parametrization". "both work" implies one might not. |
| 💡 SUGGESTION | L221 | Passive / actor-hidden | "applies session-wide via `--test-profile=<name>`" — name the actor: "Litmus applies the profile session-wide when the user passes `--test-profile=<name>`." |

(The page is otherwise tight — no marketing words, no forbidden phrases, consistent second person, no throat-clearing.)

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L9 | Programmer jargon | "Multi-entry payloads (a list of dicts for sweeps/mocks, multiple kwargs for limits/prompts) consolidate onto one marker." "Payload" and "consolidate" are programmer terms — test engineers say "entries" or "values". Could read: "Multiple values go in a single marker call — a list of entries for sweeps/mocks, multiple keyword arguments for limits/prompts." |
| 💡 SUGGESTION | L3 | Cold drop | "the recursive node type in the sidecar YAML" — "recursive node type" is CS jargon. A test engineer reading this page wants to know what to put in the YAML, not the data structure. Consider: "the per-test config block in the sidecar YAML". |
| 💡 SUGGESTION | L149 | Cold drop | "`FixtureConnection` iterator" — `FixtureConnection` appears unlinked. First use of a Pydantic model should link to models.md. |

(No anti-audience content; the page is correctly aimed at a test engineer who has already met markers and YAML.)

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L221 | "Resolution order (least → most specific): sidecar file-level → sidecar class/method → **inline marker → profile chain** → CLI flags." | Two things wrong: (1) **No CLI flag exists for limits/markers** — `litmus_limits`/`litmus_sweeps`/etc. have no CLI override. The cascade ends at the product spec (`how-to/limits.md` L42 confirms this). (2) The order of `inline` vs `profile` contradicts the cascade code: `_apply_cascade_to_items` calls `add_marker` AFTER inline markers are already on the function, and the limit-merge in `autouse.py` walks `own_markers` in insertion order with later-wins → **cascade (sidecar+profile) overrides inline**, not the other way around. Sibling doc `how-to/limits.md` agrees inline < profile, contradicting this page's order. | `src/litmus/pytest_plugin/hooks.py:802-808`; `src/litmus/pytest_plugin/autouse.py:217-232`; `docs/how-to/limits.md:35-43` |
| ⚠️ WARNING | L9 | "Stacking a Litmus marker raises `StackedMarkersError` at collection." | The underlying error class is `StackedMarkersError`, but the pytest adapter re-raises it as `pytest.UsageError` so users will see `UsageError`, not `StackedMarkersError`, in their pytest output. Either name the surfaced type (`pytest.UsageError`) or say "raises an error" without naming the type. | `src/litmus/pytest_plugin/hooks.py:783-788` |
| ⚠️ WARNING | L26 | "the only difference is `verify` raises `AssertionError` on FAIL where `logger.measure` doesn't" | Incomplete. `verify` also raises `MissingLimitError` when no limit can be resolved (e.g., neither marker, sidecar, profile, nor product spec defines one). `logger.measure` records without judging in that case. | `src/litmus/execution/verify.py:87-93, 195-201` |
| ⚠️ WARNING | L168 | `on` field default: `None` (any) | Defaulting to `None` is correct, but `pytest-rerunfailures` interprets the absence of `only_rerun` as "retry on any failure including non-asserts". Confirm "None = any exception" matches user expectation — code passes `only_rerun=[...]` only when `on` is non-None, so semantics line up. (See `src/litmus/pytest_plugin/retry.py:35-36`.) The doc claim is correct; flagging for the editor to double-check the wording "any" in case "any failure" is meant. | `src/litmus/pytest_plugin/retry.py:32-36` |
| 💡 SUGGESTION | L226 | "all 20 fixtures the plugin exposes" | `grep "^@pytest.fixture" src/litmus/pytest_plugin/__init__.py` returns 21 named fixtures (plus 7 autouse fixtures in `autouse.py`). The "20" number is stale or counts something different. Verify the litmus-fixtures.md page's actual fixture count and update. | `src/litmus/pytest_plugin/__init__.py` (21 matches); `src/litmus/pytest_plugin/autouse.py` (7 autouse) |
| 💡 SUGGESTION | L139 | "Dict mapping instrument → channels — bind by instrument and channel selector: `@pytest.mark.litmus_connections(dmm=["CH1", "CH2"])`" | The Pydantic shape (`test_config.py:176`) allows the channels value to be either a `list[str | int]` or the sentinel `"all"`. The "all" sentinel is undocumented on this page. | `src/litmus/models/test_config.py:163-176` |
| 💡 SUGGESTION | L4 | `LITMUS_MARKER_NAMES` lives in `src/litmus/pytest_plugin/markers.py` | Verified. The doc cites the file path inline as a source-of-truth pointer, which is good practice. No change needed; flagging as a positive example. | `src/litmus/pytest_plugin/markers.py:30-38` |
| ✅ VERIFIED | — | 14 claims verified against source (marker names match `LITMUS_MARKER_NAMES`; no-stacking exception for `parametrize`; `MeasurementLimitConfig` shape; `MockEntry.target` "`<fixture>.<attr>`" rule; `SweepEntry` single-key independent / multi-key zip / list-of-entries cross-product semantics; `RetryConfig` field types and defaults; `PromptConfig` field types, defaults, and `prompt_type` literals; `litmus_connections` list-vs-dict discriminated shape; `connections` fixture exposes `ConnectionIterator`; pytest-rerunfailures kwarg mapping; `vector_retry` parquet column exists; sidecar YAML key names match Pydantic field names) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ⚠️ WARNING | `litmus_limits` section (L24-48) | No mention of what happens when a measurement name has no limit configured. `verify("v", x)` raises `MissingLimitError` in that case (a real and distinct failure mode); `logger.measure("v", x)` records without limit fields. This is the single most-asked "what if" for new users and the page omits it. |
| ⚠️ WARNING | `litmus_sweeps` section (L52-83) | Does not mention `linspace` / `arange` / `logspace` range expanders by syntax — the section claims they exist ("Use `litmus_sweeps` when you want range expanders") but shows no example. A reader on the reference page should see the syntax inline (even one line) or at minimum a link straight to the syntax. The link `[Test vectors & sweeps](../how-to/vector-expansion.md)` is there but the syntax is far enough away that copy-paste from this page is impossible. |
| ⚠️ WARNING | `litmus_mocks` section (L87-109) | No mention of what happens when the `target` fixture doesn't exist at test setup, or when the attribute on the resolved fixture doesn't exist. Both are common typo failure modes for new users. |
| ⚠️ WARNING | `litmus_characteristics` / `litmus_connections` (L113-149) | The two markers are described as "Combined with `litmus_connections`" and "Pairs with `litmus_characteristics`" but the page never says **which marker is required, which is optional, what happens with just one, and what `context.characteristics` returns when no marker is present**. The cross-link to spec-driven-testing.md helps but a one-line "if absent" row would prevent confusion. |
| 💡 SUGGESTION | `litmus_retry` (L153-176) | No discussion of how `litmus_retry` interacts with `@pytest.mark.flaky` if both are on the same test. (Does `litmus_retry` translate to `flaky` and overwrite it? Are they merged?) Pre-empt the question. |
| 💡 SUGGESTION | `litmus_prompts` (L180-208) | `timeout_seconds` is documented as "Auto-fail after timeout" — clarify whether the test fails with `AssertionError`, `TimeoutError`, or an outcome stamped on the measurement record. Operators reading the report want to know what shows up. |
| 💡 SUGGESTION | "Where markers live" (L211-221) | The three-channel table is good, but doesn't show that **inline + sidecar can coexist on the same test** (they merge per the cascade). A short sentence above the table — "you can mix delivery channels; per-field merge order is below" — would close the loop. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L48 | `[`MeasurementLimitConfig`](models.md)` — links to the file root with no anchor. `models.md` does mention `MeasurementLimitConfig` in the ERD code blocks (L259) but has no `### MeasurementLimitConfig` heading the link can target. Same problem for `MockEntry`, `SweepEntry`, `RetryConfig`, `PromptConfig` in the See also (L227). Either add anchors in `models.md` for each model name, or change the links to acknowledge they point to the diagram (e.g., `[ERD](models.md)`). |
| ⚠️ WARNING | "See also" (L223-228) | Missing entry to [Test configuration cascade reference](configuration.md#test-configuration) — the only existing prose explanation of merge order, and this page's L221 already links into it inline. A See-also entry mirroring it would make the relationship discoverable. |
| 💡 SUGGESTION | L9 (first use of `parametrize`) | First use of `@pytest.mark.parametrize` should link to the pytest-native reference (`pytest-native.md`) or pytest's own docs — readers landing here from Google may not know what parametrize is. |
| 💡 SUGGESTION | L26 | First use of `logger.measure` — no link. `logger` is a fixture; link to `litmus-fixtures.md#logger`. |
| 💡 SUGGESTION | L26 | First use of `verify` (as a fixture name) — no link to `litmus-fixtures.md#verify`. Same for `prompt` (L182, L190), `context` (L122), `pins` (implicit at L989-quoted-from-source, but not in page itself), `connections` (L149). The fixture cross-references would close the markers↔fixtures loop. |
| 💡 SUGGESTION | L155 | First use of `pytest-rerunfailures` — no link. A link to the project (PyPI or GitHub) would help readers who want to read the underlying `flaky` semantics. |

(All in-page links resolve to existing files. The two anchors I checked — `limits.md#condition-indexed-bands`, `parquet-schema.md#retries`, `configuration.md#test-configuration` — all exist.)
