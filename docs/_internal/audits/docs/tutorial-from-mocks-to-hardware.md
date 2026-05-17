# Page audit: docs/tutorial/from-mocks-to-hardware.md

**Quadrant:** Tutorial (supplementary — transitioning from mock instruments to real hardware)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 2 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 1 | 2 | 1 |
| Gaps | 0 | 2 | 2 |
| Cross-links | 0 | 1 | 2 |
| **Total** | **1** | **10** | **11** |

---

## Ordering Findings

| Severity | ID | Finding |
|---|---|---|
| WARNING | ORD-1 | Section B ("Create Your Real Station Config") presents the side-by-side commented YAML block before explaining the meaning of the three fields in the table below it. A reader must parse an unfamiliar YAML format before the vocabulary (mock: true, driver:, mock_config:) is defined. The table should precede or immediately accompany the side-by-side block, not follow it. |
| SUGGESTION | ORD-2 | The VISA address-format table appears in Section A after the example output. The reader needs this lookup table to understand the output they just saw, so it is correctly placed — but it would be cleaner to lead with the table header ("Common address formats") before showing the output so readers know what they are looking at while reading the output. |
| SUGGESTION | ORD-3 | Section C ("Run — Verify, Then Connect") is short and clear. However, the closing line — "The `instruments` fixture handles mock vs. real based on the config and CLI flags" — introduces a named fixture concept without a reference. This would fit better as a parenthetical or link inline with the pytest invocations rather than at the end where it reads like an afterthought. |

---

## Voice Findings

| Severity | ID | Finding |
|---|---|---|
| WARNING | VOI-1 | Section headings use single-letter alphabetical labels (A, B, C, D) instead of action verbs. Tutorial headings should tell the reader what to do: "Discover What's on Your Bench" is already descriptive, but leading with "A." signals a reference list rather than a guided sequence. Drop the letters or replace with numbered steps consistent with the rest of the tutorial series (which uses "Step N" labeling). |
| WARNING | VOI-2 | Line 31 contains a 47-word parenthetical defining VISA inline: "(VISA / Virtual Instrument Software Architecture — the cross-vendor address format every PyVISA-backed driver uses — what you put in station config)". This is an academic aside inserted into procedural copy. It breaks the action flow. Move the definition to a callout box, a footnote, or a "What is VISA?" expandable, or cut it entirely and link to the CLI reference where VISA is explained. |
| SUGGESTION | VOI-3 | The phrase "Here's the key insight" (Section B) is the only moment of direct narrative voice in the page. The rest of the page is drier. Either make more sections use this engaging framing or remove this phrase and let the content speak — the inconsistency is mildly jarring. |
| SUGGESTION | VOI-4 | The troubleshooting section uses passive constructions in the "Fix" column (e.g., "Verify resource string with `litmus discover`", "Update the cal due date, or accept for development"). These are imperative enough, but "accept for development" is vague — "accept the warning during development" is clearer. Minor polish. |

---

## Audience Findings

| Severity | ID | Finding |
|---|---|---|
| WARNING | AUD-1 | The page assumes the reader knows what a PyMeasure driver class path looks like (`pymeasure.instruments.keysight.Keysight34461A`) and how to find the right one for their instrument. No guidance is given on how to look up the correct import path — not even a link to the PyMeasure instrument list. A test engineer new to PyMeasure will be stuck here. At minimum, add a note directing readers to the PyMeasure documentation or the `07-real-instruments.md` step which covers this. |
| WARNING | AUD-2 | The troubleshooting row for "instrument identity mismatch" says "Update `instruments/{role}.yaml` with the correct serial/model". The page has not mentioned an `instruments/` YAML directory at all — the tutorial has only discussed `stations/` YAML. A reader following this tutorial will not know what `instruments/{role}.yaml` refers to. This is inconsistent with the rest of the page's scope and introduces a concept (per-instrument asset files) without context. |
| SUGGESTION | AUD-3 | The side-by-side YAML comparison uses comment characters (`#`) to show the "before" state alongside the "after" state in the same code block. This is a visually clever technique but requires the reader to parse commented vs. uncommented YAML simultaneously. For a new user who just learned YAML basics, showing two separate blocks labeled "Before (starter)" and "After (real bench)" would be clearer. |
| SUGGESTION | AUD-4 | The page does not explain what "role" means in the context of instruments (dmm, psu are role names). This term appears in the troubleshooting table ("Fixture `psu` not found (or any role)") without having been defined. The concept is implicit in the YAML examples but never named. A one-sentence definition on first use would help. |

---

## Accuracy Findings

| Severity | ID | Finding |
|---|---|---|
| CRITICAL | ACC-1 | The troubleshooting row states: `"Mock instruments not allowed for test_phase='validation'"` as a symptom, with cause `"Phase enforcement (test_phase is a station-level setting that gates mocks — in validation/production)"`. This is factually incorrect. Inspecting `src/litmus/execution/profiles.py:resolve_test_phase()` confirms that `--mock-instruments` does NOT raise an error or block execution based on phase. The only effect is that the data stamp is demoted to `"development"` regardless of the requested phase. Mocks are not gated or blocked in any phase. The symptom message quoted does not exist anywhere in the codebase. This row teaches the wrong mental model and will cause confusion when users cannot reproduce the error. The fix column's suggested remediation (`--test-phase=development`) is also wrong — since mocks already silently demote to development, there is nothing to "fix". Remove or rewrite this row to accurately describe what actually happens (data stamp demotion, not blocking). |
| WARNING | ACC-2 | The `litmus discover` example output format does not match actual CLI output. The actual `_format_instrument()` function (cli.py:303) produces one-line entries like `"Keysight Technologies 34461A (SN: MY12345678) (TCPIP::192.168.1.100::INSTR)"`. The actual `discover` command groups output by protocol header: `"\nVISA: Found N instrument(s)"` followed by `"-" * 60` separator, then the formatted instrument lines. The page shows a multi-line block per instrument with "Type: dmm (from catalog)" annotations that do not appear in the actual output. A reader who runs `litmus discover` will see different output than advertised and may doubt whether the command succeeded. |
| WARNING | ACC-3 | The minimal real station YAML example (lines 75-84) shows a station with only `dmm` but no `id` or `name` fields: the block begins with `id: my_bench` and `name: "My Test Bench"` — actually these ARE present. Re-checking: the minimal example does include `id:` and `name:`. However, `StationInstrumentConfig` has a model validator (line 36-46 of station.py) that raises a `ValueError` if `mock` is False AND both `resource` and `driver` are None. The minimal real station example (lines 75-84) has `resource: "TCPIP::192.168.1.100::INSTR"` but no `driver:`, which is valid. But the statement "A minimal real station (no driver, raw PyVISA)" is slightly misleading — PyVISA is not actually used in any raw sense here; without a `driver:` field Litmus would need to open the resource directly, and the page does not explain how the test code accesses the instrument in that case. The "raw PyVISA" claim needs clarification about what happens at runtime without a driver. |
| SUGGESTION | ACC-4 | The page says `litmus station init` "walks you through role assignment". Inspecting the CLI (station_init at line 1595 of cli.py) confirms this command exists. However, the page does not note that `litmus station init` is a subcommand of `litmus station`, not a top-level command. A reader typing `litmus station-init` will get a "no such command" error. The CLI reference link at the bottom would catch this, but a parenthetical "(run as `litmus station init` — two words)" would prevent a common beginner mistake. |

---

## Gaps Findings

| Severity | ID | Finding |
|---|---|---|
| WARNING | GAP-1 | The page does not explain what happens at runtime when a real instrument is not reachable (e.g., wrong IP, instrument powered off). Section D lists "Instrument not responding / timeout" in the troubleshooting table but the fix is only "Verify resource string with `litmus discover`. Check network/GPIB cables." — it does not say what error message or exception the user will actually see in pytest output. Without knowing the symptom text, users cannot connect the table row to a real failure they observe. |
| WARNING | GAP-2 | The page does not explain how to install PyVISA or a VISA backend (e.g., NI-VISA or pyvisa-py). The `litmus discover` command depends on a VISA backend being available. A user on a fresh machine may run `litmus discover` and get an import error or empty results because no backend is installed. At minimum, a note saying "requires a VISA backend: `uv add pyvisa pyvisa-py` for the pure-Python backend, or install NI-VISA for hardware-tier scanning" would prevent a dead end. |
| SUGGESTION | GAP-3 | The page does not describe the `hostname:` field in station YAML, which allows automatic station selection without passing `--station`. For a user transitioning to real hardware who may set up multiple bench machines, this is a significant quality-of-life feature that saves them from always typing `--station=my_bench`. A one-sentence mention with a pointer to the station configuration how-to would be valuable. |
| SUGGESTION | GAP-4 | The page mentions `LITMUS_MOCK_INSTRUMENTS=1` is equivalent to `--mock-instruments` (this is documented in the plugin source) but does not tell the reader. For CI use-cases — which the page explicitly mentions ("you can run the same station config in CI with `--mock-instruments`") — the env-var form is the standard way to pass the flag in a CI system. Not mentioning it leaves the CI story half-told. |

---

## Cross-links Findings

| Severity | ID | Finding |
|---|---|---|
| WARNING | CRX-1 | The troubleshooting row for "Mock instruments not allowed for test_phase='validation'" links to `../how-to/profiles.md` for explanation. Given that the described behavior does not exist in the codebase (see ACC-1), this link propagates a false narrative. If the row is corrected or removed, the link should be removed with it. Even if the row is retained in some form, linking to the profiles how-to without context about what the reader should look for there is unhelpful. |
| SUGGESTION | CRX-2 | Section B introduces the `--mock-instruments` flag but does not link to the mock-mode how-to (`../how-to/mock-mode.md`), which covers test-level mock overrides, environment variable usage, and CI patterns in more depth. A reader who wants to understand mock configuration more fully has no path from this page to that content. |
| SUGGESTION | CRX-3 | The page links to `07-real-instruments.md` at the bottom as "What to Do Next" but does not link to the station configuration how-to (`../how-to/configuring-stations.md`), which covers `station_type`, `hostname`-based auto-selection, and the full station YAML schema. A reader who wants to go deeper on station config after this tutorial has no obvious next step except the numbered tutorial steps, which may cover different ground. |
