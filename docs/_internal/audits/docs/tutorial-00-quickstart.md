# Page audit: docs/tutorial/00-quickstart.md

**Quadrant:** Tutorial (step 0 — overview and quickstart, before the numbered steps begin)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 3 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 1 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 0 | 1 | 3 |
| **Total** | **3** | **10** | **14** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L19–L29 | The cheat-sheet callout appears immediately after the three-line install block — before the reader has seen a single piece of project structure. It links forward to nine concepts (product spec, station YAML, sidecar YAML, fixtures, markers, mock_config, characteristics, capability matching, MCP) that aren't introduced until "Understanding the Starter Project" and later sections. A reader scanning top-to-bottom will hit nine forward-references they can't resolve yet. The cheat-sheet would sit better immediately before "Next Steps" at the bottom of the page, after the reader has seen all the examples. |
| ⚠️ WARNING | L251–L263 | "Optional: Set Up AI Assistance" appears after "View Results" but before "Next: Connect Real Hardware" and "Next Steps." The natural reading flow is: install → run → view results → next steps → optional add-ons. Moving the AI section after "Next Steps" keeps the critical path uninterrupted. |
| 💡 SUGGESTION | L31–L35 | "How to Install" repeats `pip install litmus-test` verbatim from L7. If the section must exist, it should appear before the three-command block, not after it. As written the reader sees the command twice with no additional information in between. |
| 💡 SUGGESTION | L192–L209 | "The Pattern" section appears after the full YAML walk-through sections. For a Tutorial page whose goal is to build a mental model, the four-step pattern (GET / SET UP / MEASURE / CHECK) would be more useful placed right after the initial three-command block — giving readers the conceptual frame before they read the detailed examples. |
| 💡 SUGGESTION | L37–L57 | "Project Structure" introduces `fixtures/` and `instruments/` as folder names but their purpose isn't explained until "Key Folders" table (L242–L249). "Key Folders" is a tabular repeat of what the tree already shows. Consider either moving the table here to replace the bare tree, or removing the "Key Folders" section entirely. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L3 | Hedging / marketing | "Get up and running with Litmus in under a minute." — "in under a minute" is an unverifiable time claim. Readers with slow pip installs or proxy setups will be misled. Drop the time claim: "Install Litmus and run your first tests." |
| 💡 SUGGESTION | L107 | Passive voice hides actor | "For real hardware, just remove `mock: true`. Litmus uses PyVISA directly:" — "just" is a minimising word that often irritates readers who find a step harder than expected. "Remove `mock: true`. Litmus connects directly via PyVISA:" is cleaner. |
| 💡 SUGGESTION | L184–L189 | Throat-clearing / aside | The blockquote about `--dut-serial` for early articles (L184–L189) is a parenthetical that interrupts the command reference. The content is useful but the "On `--dut-serial` for early articles:" opener reads as a private editorial note rather than doc prose. Rephrase as a direct note or move it to a FAQ section. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L133 | Cold cross-page drop | "the per-test `context` / `verify` / `logger`, plus `pins`, `instruments`, per-role auto-fixtures from the station YAML" — `pins`, `instruments`, and "per-role auto-fixtures" are named without any inline hint of what they do. A new reader won't know whether these are pytest fixtures they request in the signature, objects they import, or something else. At minimum add a parenthetical: "(auto-fixtures named after each instrument role in the station YAML, e.g. `dmm`, `psu`)" |
| ⚠️ WARNING | L152 | Cold cross-page drop — wrong parameter names | "use `logger.measure(name, value, low=..., high=...)`" — `logger.measure` does not accept `low=` or `high=` parameters. The actual signature is `logger.measure(name, value, *, limit=None, outcome=Outcome.DONE, allow_repeat=False)`. A new user copying this call will get a `TypeError`. (See also Accuracy section.) |
| 💡 SUGGESTION | L19 | Wrong vocabulary | The callout says "three of the 20 fixtures Litmus contributes" (L24) and "one of the seven Litmus markers" (L25). These counts appear before any explanation of what "fixtures" and "markers" mean in the pytest sense. For readers coming from LabVIEW or TestStand, "fixture" means the physical test fixture (jig). A one-line qualifier — "(pytest fixtures: functions that set up test resources)" — would avoid confusion. |
| 💡 SUGGESTION | L154–L172 | Condescension risk | The sidecar section says "Same merge rules as stacked pytest decorators — file scope, class scope, per-test" without defining what "merge rules" means for the YAML keys shown. The analogy to "stacked pytest decorators" will resonate with pytest veterans but not with test engineers migrating from TestStand, who may never have written a decorator. The merge precedence order (per-test wins over class wins over file) is more useful than the analogy. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L152 | `logger.measure(name, value, low=..., high=...)` — implies `low=` and `high=` are keyword arguments | `logger.measure(name, value, *, limit=None, outcome=Outcome.DONE, allow_repeat=False)` — no `low=` or `high=` params; limits are passed as a `Limit` object via `limit=` | `src/litmus/execution/logger.py:941` |
| ❌ CRITICAL | L133 | "the Litmus plugin contributes [20 fixtures]" | 21 `@pytest.fixture` decorators are defined in `src/litmus/pytest_plugin/__init__.py` (at lines 369, 422, 437, 559, 571, 606, 652, 695, 762, 776, 842, 877, 897, 919, 942, 974, 980, 1008, 1020, 1104, 1149) | `src/litmus/pytest_plugin/__init__.py` |
| ⚠️ WARNING | L24 | "three of the 20 fixtures Litmus contributes" (re: `verify` / `logger` / `context`) + "20 fixtures" repeated at L133 | 21 fixtures counted in source; the cheat-sheet and the test-code section both repeat "20" | `src/litmus/pytest_plugin/__init__.py` |
| 💡 SUGGESTION | L133 | "logger" described as one of the "per-test" fixtures | `logger` is `scope="session"` (autouse); it is session-scoped, not per-test. `context` and `verify` are function-scoped (per-test). The phrase "the per-test `context` / `verify` / `logger`" groups all three as per-test, which is wrong for `logger`. | `src/litmus/pytest_plugin/__init__.py:369` |
| 💡 SUGGESTION | L234–L236 | `table = pq.read_table("results/runs")` with comment "recurses into date subdirs" | The parquet files are written to `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet` — the `results/` prefix is the starter project's `data_dir`, not a hardcoded path. A user whose `data_dir` differs (or whose project uses the global platformdirs default) will not find files at `results/runs`. Clarify that `results/` should be replaced with the project's configured `data_dir`. | `src/litmus/data/backends/parquet.py:193` |
| ✅ VERIFIED | — | 15 claims verified against source | — | — |

Verified correct: `pip install litmus-test` package name (pyproject.toml:6); `litmus init quick_start --starter` flag (cli.py:44–46); `litmus runs`, `litmus show`, `litmus serve` CLI commands (cli.py:519–561); `litmus mcp serve` command (cli.py:988–995); `litmus setup claude-code`, `claude-desktop`, `copilot` subcommands (cli.py:1130, 1185, 1303); `--dut-serial` flag (plugin:199); `--mock-instruments` flag (plugin:560); `context.get_param` method (harness.py:288); `verify(name, value)` call signature (plugin:1009); `litmus serve` default port 8000 (cli.py:520); `record_type == "measurement"` column in parquet (schemas.py:48); `results/runs/{date}/` path shape (parquet.py:7); `LITMUS_MARKER_NAMES` tuple contains exactly 7 markers (markers.py); `results/` folder created by `litmus init` (init.py:82).

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L152 | `logger.measure(name, value, low=..., high=...)` — this is the only place the page shows a user how to log a measurement without a product spec, but the parameter names are wrong (see Accuracy). A reader who tries this call will get a `TypeError`. The page needs to show the correct signature for ad-hoc limits. |
| ⚠️ WARNING | L5–L15 | The three-command block (`pip install`, `litmus init`, `uv sync && pytest`) silently requires `uv` to be installed. If the reader only has `pip`, `uv sync` will fail. The prerequisite is never stated. At minimum add a one-liner: "Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip + virtualenv)." |
| ⚠️ WARNING | L176–L189 | The "Running Tests" block shows `pytest tests/ --station=my_station --mock-instruments --dut-serial=TEST001 -v` but the starter project run at L14 is just `uv sync && pytest` with no flags. A reader who runs the starter project successfully and then tries the explicit form from L176 without a `stations/my_station.yaml` will get an error. The page never explains that `--station` is optional when only one station YAML exists, or that the bare `pytest` works because of the starter's pre-configured setup. |
| ⚠️ WARNING | L60–L62 | "When you run `litmus init quick_start --starter`, it generates all of these files." — The page never shows what `litmus.yaml` (the project config file) contains or what it does. The project tree at L41–L57 omits `litmus.yaml` entirely, yet it controls `data_dir`, `default_station`, and other key behaviors. A reader who modifies the project will encounter this file without any guidance. |
| 💡 SUGGESTION | L107–L119 | The "real hardware" YAML examples remove `mock: true` but never tell the reader what error to expect if PyVISA can't connect. A one-line callout — "If the instrument isn't reachable, the test will error on first instrument access; run `litmus discover` to confirm the resource string" — would save a common debugging step. |
| 💡 SUGGESTION | L231–L237 | The "Programmatic" parquet reading example reads from `"results/runs"` without showing what columns exist or linking to the parquet schema reference. A reader trying to filter by measurement name or outcome will have no idea what to filter on. Link to `reference/parquet-schema.md` here. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L55 | The project tree shows `results/` with a sub-note "(gitignored)" but there is no link to `reference/configuration.md` or `reference/parquet-schema.md` where the data layout is documented. First-use of the results storage structure should link to `docs/reference/parquet-schema.md`. |
| 💡 SUGGESTION | L107 | "Litmus uses [PyVISA](https://pyvisa.readthedocs.io/) directly" — the link is to the external PyVISA docs, which is fine, but there is no link to the Litmus VISA instrument reference (`docs/reference/connect.md` or `docs/reference/litmus-fixtures.md`) where users learn how the fixture integrates PyVISA. |
| 💡 SUGGESTION | L211 | "For the full reference — markers, sidecar YAML, `context.changed()`, mocks, retries — see the [Writing Tests guide](../how-to/writing-tests.md)." — `context.changed()` is mentioned but never introduced on this page. This is a first-use of a Litmus-specific method with no inline explanation. Either define it in a sentence ("`.changed()` returns true when a param value differs from the prior vector iteration") or link to its definition in `docs/reference/litmus-fixtures.md`. |
| 💡 SUGGESTION | L231–L237 | The programmatic parquet reading section has no link to `docs/reference/parquet-schema.md`, which documents all columns available for filtering. This is the first time parquet columns (`record_type`, etc.) are mentioned on the page. |
