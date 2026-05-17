# Page audit: docs/how-to/profiles.md

**Quadrant:** How-to (profiles — named test selection and override sets)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 2 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 3 | 2 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 0 | 3 | 4 |
| **Total** | **4** | **16** | **15** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L34-44 | "Selecting a profile" section uses `--test-phase=validation` and `--test-phase=production` before the page has explained what `test_phase` IS — that explanation lives 170 lines later in the "Test phase and mocks" section (L207). A how-to reader following top-to-bottom encounters the conventional facet key used in every example before its meaning is established. |
| WARNING | L17-32 | "Why profiles?" appears AFTER an information-dense opener (L1-15) that already introduces three Litmus-specific concepts (facets, sidecars, TestEntry shape, recursive tree). For a how-to page, prerequisites/motivation should land before the first dense conceptual paragraph. |
| SUGGESTION | L182-204 | "Merge order" cascade table is the single most important reference on this page (a reader will return to it repeatedly), yet it sits buried two-thirds down and after the worked extends example. Consider promoting it nearer the top, or referencing it from L204 ("CLI always wins") with an anchor link. |
| SUGGESTION | L221-277 | "Worked example" section comes AFTER "Test phase and mocks" but USES `test_phase` facets in every snippet. If the worked example is the page's actual "how-to do it end to end", it deserves to be the climax — currently the page ends on `## Non-goals` and `## See also`, both of which are reference material, not steps. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L43 | hedging / vague | `"name-based escape hatch"` — "escape hatch" is colloquial; "name lookup" is clearer |
| WARNING | L121 | passive (actor hidden) | `"The loader reads both..."` — fine as written, but `"A name conflict ... raises UsageError"` (L123) hides who raises and when. Project load is the actor; say so explicitly. |
| SUGGESTION | L8 | hedging | `"picks exactly one profile whose declared facets match"` — fine, but `"selects"` reads more direct than `"picks"`. |
| SUGGESTION | L31 | hedging | `"Profiles sit between those two"` — softens a structural claim. State the position: "Profiles are the third tier: versioned YAML, session-wide, overlaid on sidecars." |
| SUGGESTION | L94 | jargon | `"bare — binds to module-level test_standalone"` — see audience note; "binds" is the banned T&M-jargon term. "matches" or "names" is clearer. |

(no marketing language, no forbidden phrases other than the "binds" usage below, no inconsistent person.)

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L92, L94, L46 | forbidden T&M jargon ("binding"/"binds") | `"qualified — binds to TestRails.test_rail"`, `"bare — binds to module-level test_standalone"`, and L46 `"Litmus auto-synthesizes a --<facet>=<value> CLI flag"` (synthesizes is fine; the L92/L94 "binds" violates the no-jargon rule per CLAUDE.md feedback file). Use "matches" or "names" instead. |
| WARNING | L59 | cold-drop of model name | `"A profile is a TestEntry"` — TestEntry is a Litmus Pydantic model that a test engineer wouldn't have encountered. The link goes to `../reference/models.md` (entire page, not an anchor). A one-line "(the per-test config record sidecars use)" appears but is parenthetical. Either lead with the concept (per-test override block) and put the model name in parens, or anchor the link to the specific model section. |
| SUGGESTION | L25 | programmer jargon | `"per-module sidecar"` — sidecar is already established Litmus vocabulary, fine. But "code-adjacent" (L28) is jargon a test engineer would skim past; say "lives next to the test file" instead. |
| SUGGESTION | L171 | jargon-adjacent | `"chain walked parent-first"` and `"flatten before merging"` — pure programmer vocabulary for an operation that could be described in operator terms: "parent values first, child overrides last". |

(no anti-audience content; no condescension; no `product_id`/`station_id` leaks; uses `tps54302`-style ids consistently which is appropriate for an example.)

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L42 | `pytest # no facets → baseline` (table comment) — implies bare `pytest` with profiles declared runs without a profile | `resolve_default_profile` RAISES `ProfileError`/`UsageError` when profiles are declared, no facet flags passed, AND `default_profile` is unset. The doc never mentions `default_profile`, never mentions `--no-test-profile`, and tells the operator bare `pytest` is "baseline" which is wrong for any project with profiles. | `src/litmus/execution/profiles.py:556-584` |
| CRITICAL | L282-283 | `"The active profile name is recorded on the run as profile=<name> and shows up in litmus show <run_id>"` | The `litmus show <run_id>` terminal output prints run_id, DUT Serial, Station, Outcome, Started, Ended, Steps, Measurements — and nothing else from the profile fields. `profile` is in the Pydantic model on `TestRun` but `show()` does not render it. | `src/litmus/cli.py:658-665`; `src/litmus/data/models.py:419` |
| WARNING | L123 | `"raises UsageError at project load"` (for inline/file profile name conflict) | Code raises `ValueError`, not `UsageError`. `UsageError` only happens later if a pytest hook wraps it. | `src/litmus/store.py:349-352` |
| WARNING | L180 | `"Cycles and unknown parents raise UsageError at project load"` | Code raises `ProfileError`. The pytest hook wraps that as `pytest.UsageError` at session start (not project load). | `src/litmus/execution/profiles.py:167-176` |
| WARNING | L62-69 | Profile shape table lists `runner` separately as `dict[str, Any]` AND lists `limits / sweeps / mocks / characteristics / connections / retry / prompts` as "Litmus marker fields" — but the table omits the two profile-only fields `station_type` and `fixture` that are actual `ProfileConfig` attributes. | `ProfileConfig` adds `description`, `facets`, `extends`, `station_type`, `fixture` (5 profile-only fields, not 3). | `src/litmus/models/project.py:37-49` |
| SUGGESTION | L67 | doc: `runner: dict[str, Any] | Opaque per-runner block (validated by plugin)` | True for storage in core; pytest's runner schema (`PytestRunner`) accepts exactly these keys: `addopts`, `markexpr`, `keyword`, `plugins`, `parallelism`, `timeout`, `markers`. Listing them once would make the runner block more useful (currently `addopts` and `markers` are shown in examples but the operator has no inventory). | `src/litmus/execution/profiles.py:67-75` |
| SUGGESTION | L226-227 | `runner.addopts: "--strict-markers" inherited from family` | Confirmed correct, BUT `addopts` actually CONCATENATES parent-then-child (space-joined) rather than child-last-wins like other runner fields. The doc's general "child overrides last-wins" sentence (L132) is a slight oversimplification when `addopts` is the field. | `src/litmus/execution/profiles.py:200-201, 226-227` |
| VERIFIED | — | 14 claims verified against source: facet auto-synthesis flag form (L46), zero/many-match → UsageError (L51-52), `--test-profile` cross-check mismatch error (L54-55), `extends: str \| None` single parent (L65), `facets: dict[str, str]` (L62), `tests: dict[str, TestEntry]` recursion (L69), `profiles/*.yaml` glob discovery and name-stem keying (L106-119), `--mock-instruments`/dirty-git demotion to `development` for the stamp only (L214-219), `profile_facets` column on TestRun (L219, L283), `description` shown only on profile model (not in `show`), `--test-phase` flag exists and accepts arbitrary strings (L210), CLI flag form `--<facet-key>` with `_`→`-` (L46), `runner.markers` is list of single-key dicts (L82-84). | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L34-44 ("Selecting a profile") | Page never explains `--no-test-profile` or `default_profile` (in `litmus.yaml`). Combined with the inaccurate "no facets → baseline" line (L42), a reader running bare `pytest` after defining a single profile will hit `ProfileError: "Profiles are declared in litmus.yaml but no profile was selected..."` with no guidance from this page on how to fix it. |
| CRITICAL | L34-44 | No "how do I know it worked" guidance. After selecting a profile, what visible signal confirms the active profile applied? No example of pytest header output, no `litmus show` snippet, no `litmus runs` column. The "Provenance" section (L279-285) gestures at `litmus show <run_id>` but (a) that command doesn't actually print profile info per the accuracy finding, and (b) it's about provenance after the run, not feedback during selection. |
| WARNING | L17-32 ("Why profiles?") | No statement of when profiles are the WRONG tool. Sidecar vs profile boundary is described as "session scope vs per-module" but a reader still doesn't know: "I have three temperatures × two voltages — sweep or profile?" or "I have a per-DUT calibration constant — sidecar or profile?" |
| WARNING | L75-85 | The first runner example uses `markers: - skipif: "not os.getenv('HAS_BENCH')"` — but where is `skipif` defined, and what evaluates the string? Is it pytest's `pytest.mark.skipif` with `condition=<string>`? Test engineers without deep pytest will not know the string-evaluated semantics. |
| WARNING | L131-180 (extends chain) | No guidance on naming conventions. Examples use `power_family` (family base), `production-tps54302` (variant), `characterization` (standalone) — but the page never states the convention. A reader writing their first profile won't know whether the stem should be `family-variant` or `variant_family` or something else. The auto-CLI-flag synthesis depends on facet keys, not file names, but this isn't reinforced. |
| WARNING | L289-298 (Non-goals) | "Multi-match facet composition" is listed as a non-goal but the page never explains the WORKAROUND. If two profiles both could match `--product=tps54302`, how should the operator structure their YAML to avoid ambiguity? Current advice: "tighten the query" (L52). Concrete example missing. |
| SUGGESTION | L207-220 (Test phase and mocks) | "Dirty git tree" demotion is mentioned but the page never tells the reader how to check this themselves. A one-liner — `git status --porcelain` returning nothing means clean — would let them predict the stamp before running. |
| SUGGESTION | L62-69 (Profile shape) | The shape table doesn't say which fields are required vs optional. `facets` is "exact-match keys" but is it required? (Code says it's required to be selectable from the CLI; family profiles omit it intentionally.) `description` is optional. `extends` is optional. State this. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| WARNING | L59 | Link `[TestEntry](../reference/models.md)` resolves to the whole page; no `#TestEntry` anchor exists in `docs/reference/models.md` (verified — no `## TestEntry` or `### TestEntry` heading). Add the anchor or change the link text to make the genericity intentional. |
| WARNING | L302 ("See also") | Missing link to `docs/concepts/sessions.md` — profiles are explicitly "session-scoped" (L13, L294) and a reader who has not yet read the sessions concept page needs that grounding. |
| WARNING | L302 | Missing link to `docs/how-to/configuring-stations.md` — the `station_type` and `fixture` fields on a profile (which the page omits per the accuracy finding) are governed by station config. Once those are added, the cross-link is mandatory. |
| SUGGESTION | L3, L34 | First use of "facets" (L3, L7-8) carries no link. There's no dedicated concept page, but a glossary line or anchor in `docs/reference/configuration.md#profiles` would help. |
| SUGGESTION | L10 | First use of "sidecar" carries no link. `docs/how-to/writing-tests.md` is the de-facto reference and IS in "See also", but in-prose linking on first use is the convention. |
| SUGGESTION | L59 | First use of "TestEntry" — see WARNING above; if anchor is added, this becomes the canonical link target. |
| SUGGESTION | L207-208 | First use of `test_phase` as the conventional facet key — link to where the demotion behavior is documented in the reference, or to `docs/concepts/sessions.md` if it covers run-stamping. Currently the only place this is explained is on this same page (L207-219). |

---
