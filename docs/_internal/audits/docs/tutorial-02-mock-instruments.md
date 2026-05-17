# Page audit: docs/tutorial/02-mock-instruments.md

**Quadrant:** Tutorial (Step 2 of 10 — running without hardware using mock instruments)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 1 |
| Voice | 0 | 0 | 3 |
| Audience | 1 | 1 | 1 |
| Accuracy | 0 | 1 | 0 |
| Gaps | 1 | 2 | 1 |
| Cross-links | 0 | 2 | 2 |
| **Total** | **2** | **8** | **8** |

---

## Ordering

| # | Severity | Location | Finding |
|---|---|---|---|
| O-1 | WARNING | "Mock factory cheat sheet" section | The cheat sheet (dict form, callable form) appears before the conceptual "Mocks vs real hardware" table. Advanced factory variants should follow, not precede, the core concept anchor. Move the cheat sheet after the table, or defer it to an expandable aside. |
| O-2 | WARNING | Lines 34 and 52–63 | The three mock factory forms (constant, dict, callable) are described twice: once inline in the conftest explanation and again in the cheat sheet. This creates redundant ordering. The inline prose should mention only the constant form; the cheat sheet section should cover all three forms. |
| O-3 | SUGGESTION | "Mock factory cheat sheet" | The dict-form example uses SCPI query strings ("MEAS:VOLT:DC?") — domain jargon not introduced in steps 1 or 2. Either annotate the dict example with a clarifying comment or move the SCPI example to a later step where VISA/SCPI is covered (step 7). |

---

## Voice

| # | Severity | Location | Finding |
|---|---|---|---|
| V-1 | SUGGESTION | Line 34 ("quacks like a DMM") | "Quacks like" is a Python community duck-typing idiom but opaque to test engineers new to Python. Replace with "behaves like a DMM — every method is available and returns the configured value." |
| V-2 | SUGGESTION | Line 65 ("that's the seam where a missing mock spec shows up") | "Seam" is a software design metaphor (Feathers, _Working Effectively with Legacy Code_) not in common use for test engineers. Rewrite as "that's where a missing mock configuration becomes visible." |
| V-3 | SUGGESTION | Line 44 ("Same test code as step 1, no hardware required.") | Sentence fragment. Acceptable in tutorials but marginally clearer as "The same test code from step 1 runs — no hardware required." |

---

## Audience

| # | Severity | Location | Finding |
|---|---|---|---|
| A-1 | CRITICAL | Line 5 ("In step 1 you wrote vanilla pytest tests against psu and dmm fixtures") | This contradicts what step 1 actually shows. Step 1 (01-first-test.md) has `test_hello.py` with `assert True` and a `conftest.py` using `MagicMock`, not real `PSU`/`DMM` driver stubs. A reader following the tutorial linearly arrives at step 2 with the wrong conftest. The prerequisite state must be corrected or step 1 must be updated to bridge to step 2. |
| A-2 | WARNING | Lines 57-59 (dict form with SCPI strings) | The dict-form example uses SCPI command strings (`MEAS:VOLT:DC?`, `*IDN?`) and the `query` method — VISA/SCPI concepts not introduced until step 7. A step-2 reader has no context for these. Either remove the SCPI example and substitute a domain-agnostic one, or note that SCPI is covered later. |
| A-3 | SUGGESTION | Line 65 ("Reading an unconfigured attribute (not a method call) raises AttributeError") | The distinction between attribute access and method call is assumed knowledge. A brief parenthetical like "(i.e., `mock.some_setting` vs `mock.some_method()`) would help readers at the Python-beginner level. |

---

## Accuracy

| # | Severity | Location | Finding |
|---|---|---|---|
| AC-1 | WARNING | Line 32 ("it returns True whenever --mock-instruments is on the command line or LITMUS_MOCK_INSTRUMENTS=1 is set") | The resolution order has a third source: `mock_instruments: true` in `litmus.yaml`. The actual precedence is CLI flag > `LITMUS_MOCK_INSTRUMENTS` env var > `litmus.yaml` default > `false`. Omitting the YAML default is misleading to users who want project-wide defaults. Verified in `src/litmus/pytest_plugin/helpers.py` lines 305–316. |

---

## Gaps

| # | Severity | Location | Finding |
|---|---|---|---|
| G-1 | CRITICAL | Opening paragraph ("In step 1 you wrote vanilla pytest tests against psu and dmm fixtures") | The tutorial is missing a prerequisite state description. Step 1 ends with `test_hello.py` and a `MagicMock`-based conftest; step 2 opens with real driver stubs (`from drivers import DMM, PSU`). There is no bridge: "Add these driver stubs" or "use the examples/01-vanilla layout." A reader following linearly cannot complete step 2 without first knowing where `drivers.py` and the initial conftest come from. |
| G-2 | WARNING | Line 14 (`from drivers import DMM, PSU`) | The `drivers` module is never explained — where to create it, what it should contain, or that it comes from the examples directory. Readers not using the cloned repo are blocked. At minimum, add a note like "The `drivers/` module contains your real driver classes — see examples/01-vanilla/drivers/ for a runnable example." |
| G-3 | WARNING | Entire page | No troubleshooting section, unlike step 1 which includes one. Common failure modes for this step include: `fixture 'mock_instruments' not found` (Litmus plugin not active), `ImportError: cannot import name 'Mock' from 'litmus.instruments.mocks'` (wrong version), and driver connection errors when `--mock-instruments` is omitted. |
| G-4 | SUGGESTION | "What you learned" section | No "see also" pointer to `as_mock()` / `set_mock_value()` for readers who want to update mock values mid-test. A one-line aside ("To update mock values during a test, see `as_mock()` in the reference") would serve advanced readers without cluttering the tutorial. |

---

## Cross-links

| # | Severity | Location | Finding |
|---|---|---|---|
| CL-1 | WARNING | Line 82 ("lift this conftest conditional into station YAML (step 7)") | Plain-text reference to step 7 but no hyperlink. Should be `[step 7](07-real-instruments.md)` or `[station YAML](07-real-instruments.md)` so readers can preview what they're building toward. |
| CL-2 | WARNING | "Next Step" section (line 84-88) | Step 2 has only a forward link to step 3. Steps 3, 4, and later steps use two-way nav footers ("← Step N · Step N+2 →"). Step 2 should add a backward link to `[← Step 1: Run Something](01-first-test.md)` for nav consistency. |
| CL-3 | SUGGESTION | Line 9-15 (Mock import / conftest pattern intro) | No link to `docs/how-to/mock-mode.md` which covers the Mock factory at production depth (value priority, per-instrument control, CI/CD patterns). Add a "For production-depth mock configuration, see [Mock mode](../how-to/mock-mode.md)" note at the end of the conftest pattern section. |
| CL-4 | SUGGESTION | Line 32 ("LITMUS_MOCK_INSTRUMENTS=1") | No link to configuration reference that documents this env var. Consider linking to `../reference/configuration.md` or `../reference/cli.md` at first mention. |
