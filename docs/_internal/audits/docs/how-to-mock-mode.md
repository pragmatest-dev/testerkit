# Page audit: docs/how-to/mock-mode.md

**Quadrant:** How-to (running tests without hardware via mock instruments)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 1 |
| Voice | 0 | 3 | 2 |
| Audience | 1 | 2 | 1 |
| Accuracy | 4 | 4 | 1 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **8** | **17** | **9** |

---

## Ordering

### CRITICAL

**Lines 79-92 — Stray "Or in a sidecar YAML" block lives inside Test-Level section but introduces a second, contradictory schema.**
The section "Test-Level (Override for Specific Tests)" starts at line 45 showing the `tests: test_name: mocks:` nested form. Then at line 67 we pivot to `mocker.patch.object`. Then at line 79 we get a paragraph about profile suppression with `mocks: []`. Then at line 83 "Or in a sidecar YAML" shows a *file-level* `mocks:` block (not under `tests:`). A reader walking top-to-bottom is now looking at three "test-level" variants with no signposting of how they relate. The file-level form belongs in its own subsection (or before the per-test form, since file-level is the broader scope).

### WARNING

**Lines 96-115 — "Vector-Level" section title is misleading: the section explicitly says vector-scoped mocks are NOT supported.**
The H3 promises a third tier ("Vector-Level"), then immediately retracts it ("vector-scoped mocks in the sidecar are not currently supported"). This breaks the implicit pattern set by "Station-Level" and "Test-Level". Rename to "Per-Vector Values (from the test body)" or fold into Test-Level as "When vectors need different return values".

**Lines 117-129 — "Mock Value Priority" appears AFTER three different configuration sites have been shown.**
The priority list is the conceptual key that makes sense of station vs test-level vs limit-nominal. Readers need it before, or at the start of, "Configuring Mock Values" — not after they've already read three variants and a half-baked vector section.

### SUGGESTION

**Lines 188-195 — Environment variable section is buried under per-instrument and CI/CD sections.**
`LITMUS_MOCK_INSTRUMENTS=1` is a peer of the `--mock-instruments` flag. It belongs in the Quick Start area (or immediately after), not 180 lines later. CI/CD already implies env-var usage; readers searching "how do I enable mock mode without the flag?" won't scroll this far.

---

## Voice

### WARNING

**Line 13 — "The same test code works with real hardware or mocks" — declarative claim with no anchor.**
How-to voice should be imperative ("Run the same test code on real hardware and on mocks") or evidentiary ("…because instrument fixtures resolve from the same station YAML"). The bare claim reads like marketing.

**Lines 127-129 — Bulleted "This allows realistic tests where:" is explanation, not how-to.**
"Simple tests use limit nominal values automatically / Complex tests configure per-vector outputs" is Diátaxis-Explanation content sitting inside a how-to. How-to should say "Use limit nominals as defaults when you don't need per-vector control; switch to `mocker.patch.object(...)` when you do." Action-oriented, not benefit-oriented.

**Lines 183-186 — "This is useful when:" bullet list reads as marketing rationale.**
"One instrument is unavailable or broken / Testing instrument-specific edge cases / Simulating hard-to-reproduce conditions" doesn't help the operator execute. Either drop, or rewrite as concrete task framings ("If your PSU is in cal lab, set `mock: true` on it while keeping the DMM on real hardware").

### SUGGESTION

**Lines 215-229 — "1. Use Realistic Values" / "2. Test Edge Cases" / "3. Match Limit Nominals" — numbered Best Practices is explanation-shaped.**
Each best practice could be a small how-to recipe ("To match limit nominals: …"). The current Good/Bad table at lines 219-228 is fine; it's the framing that feels essay-like in a how-to page.

**Line 269 — Trailing factoid paragraph reads like a footnote dropped in mid-flow.**
"`mocks:` is a list of `MockEntry` dicts (`target:` + `unittest.mock.patch.object` kwargs like `return_value`, `side_effect`, `wraps`, `spec`), never a `name: value` dict." — true and useful, but stranded under "Best Practice 3" where it has no obvious owner. Promote to the first sidecar example or move to a "Schema reminder" callout near the Test-Level section.

---

## Audience

### CRITICAL

**Lines 121-125 — "Mock Value Priority" mixes the marker-pipeline and harness-pipeline as if they were one resolution order.**
A test engineer reading this list expects ONE resolution order. But the code has two parallel systems (see Accuracy CRITICAL #1): the autouse `_litmus_apply_mocks` fixture (which reads `litmus_mocks` markers from sidecar `mocks:`, file → class → test → profile cascade) and the harness `_get_mock_config_for_vector` (which falls through vector → test-level → limit nominal). Station `mock_config` is applied by `InstrumentPool` at session start and is independent of both. The current list collapses three different mechanisms into a numbered sequence — the operator will form a wrong mental model and be unable to debug a mock that doesn't fire.

### WARNING

**Line 74 — Example uses both `context` and `dmm` and `logger` as fixtures with no explanation.**
The first `mocker.patch.object` example signature is `(mocker, context, dmm, logger)`. Mock-mode is one of the first how-tos a new user hits. They'll wonder why `context` is here, why `logger` instead of `verify`, and what `dmm` does — none of which the page explains or links to. Linking to `reference/litmus-fixtures.md` once near the top would fix this.

**Lines 271-283 — "Hardware Tests" subsection is for a different audience than the rest of the page.**
The whole page is "how to run without hardware". Then the last functional section pivots to "how to mark tests that REQUIRE hardware". This is useful info, but it belongs in `writing-tests.md` or a "markers" reference page, not the mock-mode how-to. A reader skimming for mock setup gets dragged into pytest-mark filtering.

### SUGGESTION

**Line 98 — "drive the mock from the test body using the swept parameter" — assumes the reader knows what a "swept parameter" is.**
A new user is here because they want to run without hardware. "Swept parameter" is a Litmus-vectors concept. Either link to `vector-expansion.md` or rephrase ("the parametrize argument the test takes, e.g. `load` in the example below").

---

## Accuracy

### CRITICAL

**Lines 117-125 — "Mock Value Priority" is factually wrong as a single resolution order.**
Verified against `src/litmus/pytest_plugin/autouse.py:300-345` and `src/litmus/execution/harness.py:1035-1062` and `src/litmus/instruments/pool.py:92-112`:
- The `litmus_mocks` marker pipeline (autouse fixture `_litmus_apply_mocks`) applies sidecar/profile `mocks:` entries via `patch.object`, cascade order file → class → test → profile, **never falls back to limit nominal**.
- The harness `_get_mock_config_for_vector` falls back vector → test-level → **limit nominal** (only `voltage`/`current` named limits, with the hardcoded `dmm.measure_voltage` / `psu.measure_current` mapping at lines 1057-1060). This pipeline calls `set_mock_value` on mock instruments — it is NOT the same install path as `_litmus_apply_mocks`.
- Station `mock_config` is applied by `InstrumentPool` at session start (`src/litmus/instruments/pool.py:92`), entirely independent.
The doc presents these as a single ordered chain with `mocker.patch.object` at the top and "Zero" at the bottom. None of that ordering is what the code actually does.

**Lines 1057-1060 (in harness.py) — Limit-nominal fallback is hardcoded to specific instrument.method pairs.**
The page says "Limit `nominal` — From the measurement's limit config". The actual code only maps a limit whose name contains "voltage" → `dmm.measure_voltage`, and "current" → `psu.measure_current`. A limit named `output_voltage` triggers this; a limit named `vout` or `rail_3v3` does not. The page massively overstates how general this fallback is.

**Lines 60-65 — Sidecar `tests: test_name: mocks:` example uses `target: dmm.measure_voltage` and `target: psu.measure_current` but everywhere else in the codebase the DMM method is `measure_dc_voltage`.**
The mock DMM driver in `examples/02-verify/conftest.py:33`, `examples/03-inline-limits/conftest.py:23`, the auto-generated init code in `src/litmus/init.py:431`, and the harness fallback (which hardcodes `dmm.measure_voltage`) are all inconsistent. The example at line 76 of this same page uses `measure_dc_voltage`. Within the page, lines 61 and 104 and 241 use `measure_voltage`; line 76 uses `measure_dc_voltage`; line 88 uses `measure_dc_voltage`. The mix will baffle a reader copy-pasting.

**Line 269 — "`unittest.mock.patch.object` kwargs like `return_value`, `side_effect`, `wraps`, `spec`" is accurate, but the page never says "`mocks:` uses `patch.object` semantics" until this stranded sentence.**
This is the SINGLE most important fact for a reader writing sidecar mocks. Verified at `src/litmus/models/test_config.py:72-101` (`MockEntry.patch_kwargs()` forwards everything except `target` to `patch.object`) and `src/litmus/pytest_plugin/autouse.py:340-345` (`install_mocks` invocation). Should be stated in the Test-Level section, not in a postscript under Best Practice 3.

### WARNING

**Lines 79-82 — "To suppress sidecar `mocks:` entries session-wide, declare a profile with `mocks: []`" is half the story.**
Verified at `src/litmus/pytest_plugin/autouse.py:312-316`: stacking concatenates lists, *later entries with the same target overwrite earlier*. `mocks: []` doesn't strip earlier entries — the cascade re-uses target keys; an empty list adds nothing. To actually override a specific target you'd need to re-declare it with the new value. The page's blanket "suppresses session-wide" claim doesn't match how the by_target dict merges.

**Line 81 — "instrument-layer `--mock-instruments` is independent" is true but underspecified.**
What IS the instrument layer? Verified at `src/litmus/instruments/pool.py:92-112`: when `--mock-instruments` is on, `InstrumentPool` constructs `Mock(driver_class, **mock_config)` for each role, seeding from station `mock_config`. The `litmus_mocks` markers then layer `patch.object` on top of the resulting instances. The page never explains this layering, just gestures at "independent".

**Line 162 — `catalog_ref: generic_dmm` example.**
Verified `catalog_ref` is a valid field on `StationInstrumentConfig` (`src/litmus/models/station.py:30`). But the example uses an undefined catalog entry name `generic_dmm`. Either link to a real catalog entry that ships with the project, or remove the `catalog_ref:` line — it's a distraction from the `mock: true` point and may not resolve in a real project.

**Line 9-10 — Quickstart command shows `--station=stations/bench_1.yaml` (path form) but the rest of the codebase prefers `--station=<id>` (ID form).**
Verified at `src/litmus/pytest_plugin/__init__.py:572-603`: `--station` accepts both forms. Path form works but the docs in `configuring-stations.md` and tutorials all use the ID form. Inconsistency, not a bug — but readers cargo-culting from this page will hardcode paths into CI.

### SUGGESTION

**Line 162 — `mock: true` station entry omits `description:` — fine for the example, but worth noting that `mock: true` entries can have `type:` alone with no `driver:` or `resource:` (lines 162-164 do this correctly).**
Verified at `src/litmus/models/station.py:36-47`: the `resource_required_for_real_hardware` validator passes when `mock=True` regardless of driver/resource. The example is correct; consider an inline comment confirming this for readers used to the strict validator.

---

## Gaps

### CRITICAL

**Missing: how to verify mocks are actually firing.**
A how-to for running without hardware MUST tell the reader how to confirm the mock is being used. The page never mentions: checking `mock_instruments` fixture value, looking at run metadata (`logger.test_run.test_phase` is demoted to `"development"` per `src/litmus/pytest_plugin/__init__.py:561-568`), inspecting `InstrumentConnected` events (which carry the `mocked` flag per `src/litmus/instruments/pool.py:108`), or seeing what happens if a method is unconfigured (`Mock` returns `None` for class attrs, raises `AttributeError` otherwise per `src/litmus/instruments/mocks.py:151-159`).

### WARNING

**Missing: callable / side_effect / dict-lookup mock values.**
Per `src/litmus/instruments/mocks.py:71-95`, mock values can be:
- A simple value (always returned)
- A dict (looked up by first positional arg — perfect for SCPI command/response)
- A callable (passed all args)
The page shows only simple constants. The dict-lookup form is essential for SCPI-style mocks and is one of the most useful patterns. `side_effect` (forwarded to `patch.object`) for raising exceptions is also missing.

**Missing: how `mock: true` per-instrument interacts with `--mock-instruments`.**
Lines 144-186 show per-instrument mocking via `mock: true` in station YAML. But what if you set `mock: true` on an instrument AND pass `--mock-instruments`? Per `src/litmus/pytest_plugin/__init__.py:749` (`use_mock = mock_instruments or (inline_config.mock if inline_config else False)`), the flag is an OR, but the page doesn't say so. A reader will wonder if `--no-mock-instruments` overrides per-instrument `mock: true`. (It does not — verified.)

**Missing: `mocker.patch.object` is from `pytest-mock`, which must be installed.**
Lines 74-77 use `mocker` without noting that `pytest-mock` is the source. Litmus does pull it in (verified in pyproject), but a reader copy-pasting into a non-Litmus project won't know.

### SUGGESTION

**Missing: how mock identity / IDN is handled.**
Real hardware verifies `*IDN?` against config at session start. Mocks bypass this. A reader who's just set up a station and is wondering "what does mock mode skip?" deserves a one-liner. (Cross-link: `parquet-schema.md:122` already says this; this page should too.)

**Missing: how to mock an exception (driver raises during measurement).**
Useful for testing retry/error paths. `MockEntry` supports `side_effect: SomeException` per the `patch.object` kwargs pass-through.

---

## Cross-links

### CRITICAL

**No link to `reference/litmus-fixtures.md` despite the page using `mock_instruments`, `context`, `verify`, `logger`, `dmm` fixtures throughout.**
The page's "Next Steps" only links to writing-tests / configuring-stations / custom-drivers. The Litmus fixtures reference is the canonical home for `mock_instruments` (lines 197-211 of this page literally explain the fixture but never link to its reference entry at `reference/litmus-fixtures.md:240`).

### WARNING

**No link to `reference/litmus-markers.md` for `litmus_mocks`.**
The whole sidecar `mocks:` block IS the YAML-serialized form of `@pytest.mark.litmus_mocks([...])`. The markers reference (`docs/reference/litmus-markers.md:87-109`) has the canonical signature and the `target` grammar. This page should link there from the Test-Level section.

**No link to `how-to/profiles.md` from the profile-override paragraph.**
Lines 79-82 talk about declaring a profile with `mocks: []` to suppress sidecars. There's a dedicated profiles how-to that explains the profile cascade — link it.

**No link to `how-to/limits.md` from the "Limit nominal" priority entry.**
Line 122 references "the measurement's limit config" without linking. Limits have their own how-to (`docs/how-to/limits.md`) that explains the schema and resolution.

### SUGGESTION

**Inbound: tutorial 02-mock-instruments.md doesn't link to this page.**
Verified at `docs/tutorial/02-mock-instruments.md:84-88`: the tutorial step on mocking ends with a link to `03-fixtures.md`. It should also point readers to this how-to for the deeper material (sidecar mocks, mock_config, profile overrides).

**Inbound: `docs/reference/configuration.md` (line 389) mentions `mock_instruments: bool` in `litmus.yaml` but never links to this page.**
Configuration reference users hitting `mock_instruments:` should land here for "how do I actually use this?"

---

(All six audit dimensions complete.)
