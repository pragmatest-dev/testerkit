# Page audit: docs/tutorial/07-real-instruments.md

**Quadrant:** Tutorial (step 7 of 10 — connecting to real instruments)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 0 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 2 | 1 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 1 | 2 | 3 |
| **Total** | **4** | **11** | **10** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L91–97 "Mock Value Priority" | The priority list (litmus_mocks > mock_config > Zero) is presented as a summary after the mechanism has already been shown at L71–76 and L80–89. A reader who only skims headlines will hit this list before seeing the two mechanisms it ranks. Moving the priority table to immediately follow the introduction of the first mechanism (L71) and before the second (L78) would give the list its natural context. |
| 💡 SUGGESTION | L114–123 "VISA Address Formats" | The VISA table appears after "CI/CD Configuration" (L99). A reader who needs to write their station YAML (L9–35) needs the VISA address formats first. The table belongs between "Station Configuration" and "Instrument Role Fixtures" — before the reader writes a resource string they may not recognise. |
| 💡 SUGGESTION | L125–136 "Discovering Instruments" | "Discovering Instruments" is placed near the end of the page, but discovery is logically the first hardware step a reader takes before they know the correct resource string to put in their station YAML. Moving this section before or immediately after "Station Configuration" would match actual workflow order. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L74–76 | Passive voice hides actor | "Mock instruments are used instead of real drivers … Responses come from `mock_config` values … No hardware required" — all three bullets are passive; the actor (Litmus / the plugin) is absent. Prefer: "Litmus uses mock instruments … returns values from `mock_config` … requires no hardware." |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L80 | Cold cross-page drop — Litmus-specific term with no definition or link | "use `litmus_mocks` in the sidecar" — `litmus_mocks` is used as a marker name with no link to the markers reference and no one-liner explaining what a "sidecar" is. This is the first time this page uses either term. |
| ⚠️ WARNING | L48 | Cold cross-page drop — `logger` fixture used with no definition or link | `logger` appears in the test function signature with no explanation. Prior tutorial steps may have introduced it, but this page has no back-reference or link; a reader arriving directly at step 7 is blocked. |
| 💡 SUGGESTION | L133–135 | Cold drop — MCP tool invoked with no context | `litmus_discover()` is shown as a code block with no explanation of what MCP is, how to invoke the MCP server, or when this tool would be used instead of the Python snippet above it. A one-liner pointing to the MCP how-to would suffice. |
| 💡 SUGGESTION | L116 | Unnecessary expansion of an acronym the audience already knows | "VISA (Virtual Instrument Software Architecture)" — test engineers who use GPIB, TCPIP::, and ASRL resources know what VISA stands for. Drop the parenthetical or link to the standard instead. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L25–26, L32–33, L152–158 | `mock_config:` keys shown as `voltage:` and `current:` | `mock_config` keys must be **method or property names** on the driver class (e.g., `measure_voltage`, `measure_current`). `lifecycle.py` docstring: "Method return values for mock instruments (e.g. `{'measure_voltage': 3.3}`)". `init.py` template uses `measure_voltage`, `measure_current`, `set_voltage`, `enable_output`. Bare attribute names like `voltage` work only if the PyMeasure class exposes them as properties — not stated or explained. The mock_config examples throughout the page are at minimum misleadingly underspecified and likely wrong for most readers' drivers. | `src/litmus/instruments/lifecycle.py:126`, `src/litmus/init.py:419–432` |
| ❌ CRITICAL | L133–135 | "Or use the Litmus MCP tool: `litmus_discover()`" — formatted as an inline code call with no enclosing sentence context, implies it runs in a Python shell | `litmus_discover` is an MCP tool invoked by an AI agent, not a Python function or CLI command. The CLI equivalent is `litmus discover` (a top-level `@main.command()` subcommand: `litmus discover --visa --no-identify`). Showing `litmus_discover()` without clarifying the invocation surface will confuse any reader who tries to run it in a terminal or Python REPL. | `src/litmus/cli.py:1461–1468`, `src/litmus/mcp/server.py:341` |
| ⚠️ WARNING | L93–97 "Mock Value Priority" | Priority list: 1. `litmus_mocks` marker, 2. Station `mock_config`, 3. Zero | The full priority chain (per `how-to/mock-mode.md`, which was written from source): 1. `mocker.patch.object` (per-vector, test body), 2. `litmus_mocks` sidecar/marker (constant per test), 3. Limit `nominal`, 4. Station `mock_config`, 5. Zero. The page omits `mocker.patch.object` (step 1) and `limit nominal` (step 3). | `src/litmus/execution/harness.py:1035–1097`, `docs/how-to/mock-mode.md:120–129` |
| ⚠️ WARNING | L44 | "When you run with `--station`, Litmus auto-registers each instrument role as a pytest fixture." | Correct — but the fixtures are **session-scoped** (created once per pytest session, not per-test). The page never states the scope, which matters when readers try to parametrize by instrument or reset state between tests. | `src/litmus/pytest_plugin/hooks.py:264` |
| 💡 SUGGESTION | L81–89 "Per-Test Mock Values" sidecar example | Sidecar shown with top-level `mocks:` key at file root | This is valid — `SidecarConfig` extends `TestEntry` which carries `mocks: list[MockEntry]` at the root level. However, the how-to uses `tests: test_name: mocks: [...]` (per-test scope) and the Complete Example (L162–172) also uses root-level `mocks:`. The page should note that root-level `mocks:` applies to all tests in the file; per-test scope requires nesting under `tests: <test_name>: mocks:`. | `src/litmus/models/test_config.py:120–161`, `src/litmus/execution/sidecar.py:72–79` |
| ✅ VERIFIED | — | 8 claims verified against source | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L9–35 "Station Configuration" | No prerequisite stated: the page never tells the reader where to put `stations/bench_1.yaml` relative to the project root, nor that `--station=stations/bench_1.yaml` resolves relative to the pytest invocation directory. A new reader who puts the file in the wrong place gets a silent no-station warning (`station not found in stations/ directory`) with no pointer to fix it. |
| ⚠️ WARNING | L56–59 "Run with real hardware" | No guidance on what happens when an instrument is unreachable at session start — the plugin raises `pytest.UsageError` but the page shows only the happy path. A one-liner ("if the instrument isn't reachable, Litmus raises an error at session start before any test runs") would close the most common first-run failure mode. |
| ⚠️ WARNING | L42–54 "Instrument Role Fixtures" | No explicit link or forward reference to step 6 (specifications) or step 5 (configuration). The page begins as if it is the first page the reader has seen. Tutorial convention is to state "continuing from step 6" or link back, so readers who land here directly know what to build on. |
| ⚠️ WARNING | L61–69 "Running with Mock Instruments" | The page says "the same test code works in both modes" but never explains the mechanism — how does the test call `dmm.measure_voltage()` if no real DMM is connected? A sentence like "Litmus replaces each instrument fixture with a `Mock` object whose methods return the values from `mock_config`" gives the reader the mental model they need. Without it, mock mode looks like magic. |
| 💡 SUGGESTION | L91–97 "Mock Value Priority" | No guidance on "how do I know my mock values are being used?" — no example output, no CLI check, no suggestion to use `-s` to print the returned value. A reader setting up mocks for CI has no way to verify the mock is returning what they expect without running the test and hoping the limit passes. |
| 💡 SUGGESTION | L99–112 "CI/CD Configuration" | No mention that `--dut-serial=CI-TEST` combined with `--mock-instruments` auto-demotes the run's `test_phase` to `development`, which means those CI runs are filtered out of production dashboards. This is the primary "why would I care about test_phase" teaching moment for step 7. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | L80 | First use of `litmus_mocks` — no link to `reference/litmus-markers.md` and no inline definition. A reader who wants to use this marker has no path to its full specification (allowed `patch.object` kwargs, inline vs sidecar forms, stacking rules). |
| ⚠️ WARNING | L48 | First use of `logger` fixture — no link to `reference/litmus-fixtures.md`. This fixture is central to the test code shown but is not defined on this page and not linked. |
| ⚠️ WARNING | "Next Step" section (L206–210) | The page links forward to step 8 but has no "See also" entries for the how-to pages that cover this topic in depth: `how-to/mock-mode.md` (complete mock guide), `how-to/configuring-stations.md` (full station YAML reference), and `concepts/stations.md` (explains WHY the station model is designed as it is). Tutorial pages should forward-link to their concept peer. |
| 💡 SUGGESTION | L125–136 "Discovering Instruments" | The `litmus discover` CLI command has no link to `reference/cli.md` where its full flag set (`--visa`, `--no-identify`, etc.) is documented. |
| 💡 SUGGESTION | L9 "Station Configuration" | `StationConfig` is the YAML schema being shown; no link to `reference/configuration.md#station` where the full schema (including `station_type`, `hostname`, `supported_phases`, `mock: true` per-instrument) is documented. |
| 💡 SUGGESTION | L80–89 "Per-Test Mock Values" | The sidecar YAML concept is used here without a link to `reference/configuration.md` (sidecar schema) or `how-to/mock-mode.md` (practical mock patterns). Both are obvious follow-on pages for a reader who wants more than the single example shown. |
