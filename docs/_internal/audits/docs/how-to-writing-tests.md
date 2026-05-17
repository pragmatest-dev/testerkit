# Page audit: docs/how-to/writing-tests.md

**Quadrant:** How-to (end-to-end test authoring patterns)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 3 | 2 |
| Voice | 1 | 3 | 2 |
| Audience | 1 | 2 | 2 |
| Accuracy | 1 | 2 | 2 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 1 | 3 | 4 |
| **Total** | **7** | **17** | **14** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L5–L12 | The page opens with a fine-grained `verify` vs `logger.measure` comparison before introducing either fixture. A reader hitting this page first sees pass/fail semantics for two callables they don't yet know exist or how they're obtained. The "core per-test fixtures" table at L14 should come first. |
| WARNING | L24–L34 | "Minimum viable test" uses `psu` and `dmm` fixtures before the page explains where role fixtures come from (auto-registration from station config is mentioned in passing at L34, but the full mechanism isn't covered until "Instrument access" at L336). Either move the instrument-access section up, or reorder so role fixtures are explained before the MVP. |
| WARNING | L36–L62 | "Test classes are sequences" introduces `@pytest.mark.litmus_sweeps` (L43) before the dedicated "Sweeping inputs" section (L65) that explains its shape, semantics, and YAML form. The reader sees a parameterised class with `voltage=[1,2,3]` and `voltage` injected as a method arg, but the sweep mechanic is not yet defined. |
| WARNING | L142–L161 | The "Limits" section introduces the marker merge cascade with the phrase "see *Merge cascade* below" (L143). There is no section actually titled "Merge cascade" — the merge order is described inline inside "Sidecar YAML" (L269). The forward-reference breaks the page's build order. |
| SUGGESTION | L194–L235 | The `litmus_characteristics × litmus_connections` matrix (11-row table) is dense how-to-style spec content. Consider placing it after "Limits" but before "Sidecar YAML" so the reader knows what limits the markers compose with, OR split it out into its own how-to page and link from here. Currently it interrupts the flow from "limits → sidecar YAML → multi-folder structure". |
| SUGGESTION | L320–L322 | "Duplicate-name guard" sits between "Retries & test dependencies" and "Graceful degradation". It would read better grouped with the `verify`/`logger.measure` discussion at the top, since it's a constraint on those callables. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L172 | Forbidden phrase ("lifecycle") | "Manual operator setup at a **lifecycle point**" — say "before / during / after the run" or "at a step boundary". |
| WARNING | L34 | Passive / hides actor | "Instrument fixtures (`psu`, `dmm`) **are auto-registered** from the station config" — name the actor: "The plugin auto-registers instrument fixtures from the station config". |
| WARNING | L9 | Passive / hides actor | "**records the measurement row** … **stamps `measurement_outcome`** … **raises `AssertionError`**" — the prose flips to active mid-sentence but the subject "(verify)" is implicit; consider explicit "the `verify` callable records … stamps … raises". |
| WARNING | L22 | Passive | "Logger snapshots ambient ContextVars … **at write time**" is fine, but the broader claim "Data flow is one-way" hides the actor — who enforces one-way? |
| SUGGESTION | L34 | Hedging | "define a same-named `conftest.py` fixture **only if you need** custom setup/teardown" — fine, but stronger as "override by defining a same-named fixture in `conftest.py`". |
| SUGGESTION | L307 | Hedging | "The conftest shim **is the fastest route** from 'I have a folder of tests' to 'green runs.'" — opinion-flavored; either commit ("Use the conftest shim when…") or remove the editorialising. |
| SUGGESTION | L70 | Phrasing | "Each combination is one **test vector** — pytest runs the test once per combination" — this restates "combination" three times. Tighten. |

Note on intentional language: "operator-editable" (L88), "fastest path" (L292), "stable" (L305) are how-to-appropriate scoping cues rather than marketing — flagged none as marketing.

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L194–L235 | Cold cross-page drops | The "characteristics × connections" matrix uses `dut_pin`, `net`, `resolved_pins`, `FixtureConnection`, `_active_connection_var`, and `UsageError` without defining any of them on-page or linking to their concept page. A test engineer encountering this table cold cannot act. Either inline a one-liner per term or link each first-use to `concepts/capabilities.md`, `concepts/fixtures.md`, etc. |
| WARNING | L22 | Programmer jargon | "Logger snapshots ambient **ContextVars** (Python's built-in async-safe scoped state — Litmus uses them for run id, station, DUT, active instruments) at write time." — the parenthetical helps, but a test engineer doesn't need to know about ContextVars at all. Replace with "the logger reads run / station / DUT / active-instrument identity from the active scope at write time." |
| WARNING | L165–L173 | Concept first-use without link | Marker table introduces `litmus_characteristics`, `litmus_connections`, `litmus_mocks`, `litmus_prompts`, `litmus_retry` without per-row links to their reference entries. The reader gets a one-line purpose but no way to deepen. |
| SUGGESTION | L36–L40 | Anti-audience tone | "This matches the way TestStand … OpenTAP … Spintop OpenHTF model test sequences" — framework-comparison content on an authoring page. Useful as flavour but consider trimming to one line or moving to the `why-pytest.md` concept. |
| SUGGESTION | L22 | Programmer jargon | "Data flow is one-way: `test → spec → logger`" — `test → spec → logger` is internal architecture vocabulary; a test engineer's mental model is "I record values; Litmus stores them." Either drop or relocate. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L303 | "This is what `examples/03-profiles/conftest.py` does." | No `examples/03-profiles/` directory exists. The current numbering is `examples/07-profiles/`, AND that directory has no `conftest.py`. None of the existing example conftests contain a `sys.path` shim. The referenced example does not exist. | `examples/` (verified by `ls`) |
| WARNING | L9 | "`verify(name, value)` … **stamps `measurement_outcome`**" | `verify` invokes `logger.measure(name, value, limit=limit, outcome=outcome)` — it passes an `outcome` (Outcome enum value), not a field literally named `measurement_outcome`. The measurement record has an `outcome` field; `measurement_outcome` may be a parquet column alias but is not the API surface verify "stamps." Doc could mislead about the API. | `src/litmus/execution/verify.py:206` |
| WARNING | L10 | "`logger.measure(name, value)` … records a row with `outcome = DONE` and **never raises**" | True for the happy path, but `logger.measure` DOES raise `DuplicateMeasurementError` (an `AssertionError` subclass) on duplicate names — the page itself documents this at L322. "Never raises" is too absolute; "never raises on out-of-range values" is accurate. | `src/litmus/execution/logger.py:1106` |
| SUGGESTION | L387 | "[Litmus fixtures] — all **20** fixtures" | The reference page lists 20 fixtures via `### fixture` headings (verified). | `docs/reference/litmus-fixtures.md` |
| SUGGESTION | L388 | "the **seven** `litmus_*` markers" | `LITMUS_MARKER_NAMES` has exactly 7 entries. | `src/litmus/pytest_plugin/markers.py:30–37` |
| ✅ VERIFIED | — | 18 claims verified against source: fixture names (`context`, `verify`, `logger`, `pins`, `vectors`, `instrument`, `prompt`), Context methods (`get_param`, `changed`, `last`, `observe`, `configure`), marker names (all 7), `DuplicateMeasurementError`, `allow_repeat=True`, `litmus_retry(max_retries=…)`, Outcome.DONE, CLI flags (`--dut-serial`, `--station`, `--operator`, `--test-phase`, `--mock-instruments`, `--product`), `ctx.connections` attribute, ContextVar usage, retry → `flaky(reruns=N)` translation. | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L24–L34 (MVP test) | No prerequisites stated: the MVP test assumes a `station.yaml` defining `psu` and `dmm`, a `products/*.yaml` with an `output_voltage` characteristic, and a `--product=<id>` flag — none of these prerequisites are listed before the example. A reader copying this test gets fixture-not-found / no-limit errors and no signposted path. |
| CRITICAL | L142–L161 (Limits resolution) | The fallback path "4. None — unchecked, recorded anyway (characterization mode)" is stated for `logger.measure`, but the parallel question for `verify` (what happens when `verify` is called with NO resolvable limit) is unanswered. From source: `verify` raises `MissingLimitError`. This must-know behaviour is missing. |
| WARNING | L43 (sweep on a class) | The `@pytest.mark.litmus_sweeps(voltage=[1,2,3])` decorator on a class is shown, but the page doesn't explain that class-level sweeps wrap the entire class as the outer loop (the prose at L40 implies it but doesn't state the rule). What if some methods don't need the sweep param? What if a method has its own sweep marker too? Both are reasonable reader questions. |
| WARNING | L88–L97 (sweeps in YAML) | The YAML example shows file-level `tests: test_rails: sweeps:` but doesn't state how the YAML file gets discovered (filename convention: `test_<module>.yaml` next to `test_<module>.py` — implied at L238 but not yet at L88, and never spelled out as a discovery rule). |
| WARNING | L122–L139 (vectors fixture) | "Each iteration pushes the active row's values so `verify`, `context.changed`, and row stamping behave the same as in parametrized mode." — what API does the caller use to advance? Iterating the `vectors` fixture (`for v in vectors:`) is shown but the page doesn't say whether `context.changed("vin")` reflects the previous iteration in the SAME pytest case or the previous pytest case. |
| WARNING | L194 (`litmus_characteristics × litmus_connections`) | The matrix lists 11 cases but no example shows what a typical `litmus_characteristics` declaration actually looks like in code. The reader gets behaviour without a concrete sample. |
| SUGGESTION | L320 (Duplicate-name guard) | No example of the failure message or how to fix it (other than `allow_repeat=True`). One worked example would help. |
| SUGGESTION | L354 (CLI) | The CLI block shows flags but doesn't show the expected successful output (e.g., header line `litmus: results → /…`). "How do I know it worked" is unstated. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L62 | First use of `step_path` / `parent_path` is in a sentence — the link `[step hierarchy concepts page](../concepts/step-hierarchy.md)` is present but the per-test concepts of `step_path` / `parent_path` aren't linked from their introduction; a reader unfamiliar with them stops here. Inline a one-liner OR explicitly link "step_path" to the concept anchor. (File exists; anchor undefined.) |
| WARNING | L22 | First use of "ContextVars" — links to external Python docs, but not to the Litmus concept page (`concepts/sessions.md` / `concepts/fixtures.md`) explaining how Litmus uses them. |
| WARNING | L88 (sidecar YAML) | First use of "YAML — operator-editable" doesn't link to `reference/configuration.md` or the sidecar YAML reference. The full sidecar section at L236 explains shape but doesn't link to a defining reference. |
| WARNING | L165–L173 | The marker table at L165 has no per-row links to `reference/litmus-markers.md#<marker>`. Each marker name on first use should link. Currently the only marker-reference link is in the "Next steps" footer. |
| SUGGESTION | L40 | "TestStand (National Instruments' commercial test executive), OpenTAP (Keysight's open-source test sequencer), and Spintop OpenHTF" — duplicate of `vector-expansion.md` L122. Consider linking to one canonical mention. |
| SUGGESTION | L165 | The `context` fixture row at L18 doesn't link to `reference/litmus-fixtures.md#context`. Similarly `verify` and `logger` rows. |
| SUGGESTION | L194 | The `litmus_characteristics × litmus_connections` matrix should link to the canonical fixtures-config reference (`reference/configuration.md` or `concepts/fixtures.md`). |
| SUGGESTION | L378 ("Same tests, different labs") | "Profiles" links to `profiles.md`, but the same target is referenced again in "Next Steps" L390. One link per section is enough; the section-end one suffices. |

Link target verification (all exist):
- `../concepts/step-hierarchy.md` ✓
- `vector-expansion.md` ✓
- `limits.md` ✓ (anchor `#condition-indexed-bands` not strictly verified — exists at L92 as `## Condition-indexed bands`, slug should match)
- `profiles.md` ✓
- `../reference/litmus-fixtures.md` ✓
- `../reference/litmus-markers.md` ✓
- `../reference/pytest-native.md` ✓
- `mock-mode.md` ✓
- `https://docs.pytest.org/` (external, not verified)
