# Page audit: docs/integration/pytest-existing.md

**Quadrant:** Integration (adopting Litmus from an existing pytest suite)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 2 |
| Voice | 0 | 2 | 4 |
| Audience | 1 | 3 | 2 |
| Accuracy | 4 | 3 | 2 |
| Gaps | 3 | 4 | 3 |
| Cross-links | 1 | 3 | 4 |
| **Total** | **10** | **17** | **17** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L25-44 (Quick Integration → Step 3) | Step 3 ("Use Litmus in New Tests") shows `test_new_voltage_check(context, dmm, logger)` before the page has explained where `dmm` comes from, what `context` does, or how `logger` is wired. The reader following top-to-bottom encounters three undefined fixtures in the very first code example. The "Fixture Patterns / Using Station Instruments" section (L165-188) that explains the `dmm` source comes 120 lines later. |
| WARNING | L26-35 (Step 2) | "Step 2: Add to conftest.py" leads with a `pytest_addoption` block before the reader has been told that Litmus's plugin already provides these flags. This sets up the very next line (Step 3) to look like a complete configuration — but it's actively wrong (see Accuracy). The first conftest example should be empty / minimal, with the addoption block either deleted or relocated to a much later "if you need extra flags" section. |
| WARNING | L102-125 (Level 2: Add TestHarness) | The section opens with a parenthetical disclaimer about `harness.step` being a context manager and "no `harness.finish()`" — which presupposes the reader was about to look for `harness.finish()`. The page hasn't introduced `TestHarness` yet, so this clarification arrives before the thing it clarifies. Put the disclaimer after the example, or remove it (the example already shows the context manager). |
| SUGGESTION | L61-161 (Incremental Adoption) | The "Level 1 → Level 4" ladder is presented as progressive sophistication, but Level 1 ("Just Results") uses `LitmusClient` directly, while Levels 2–4 use entirely different code paths (`TestHarness`, `VisaInstrument`, fixtures). A reader who builds Level 1 cannot get to Level 2 by adding to it — they have to throw it away. Either reorder so the levels are cumulative, or rename the section to "Four entry points, pick one" and drop the staircase framing. |
| SUGGESTION | L153-161 (Level 4: Full pytest-native) | "Full pytest-native" arrives as Level 4 at the end, but it's the recommended default per the page's own opening callout (L5-7). A reader following the page top-to-bottom is led through three intermediate adoptions before reaching the form the page itself prefers. Consider front-loading Level 4 as the target state, then framing 1–3 as off-ramps for projects that can't get there yet. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L102 | Hedging | "For more detailed tracking, wrap the measurement block in a harness step" — "more detailed" is uncommitted; say what tracking the harness adds (vector-level retry, limit resolution, channel traceability) or drop the qualifier. |
| WARNING | L320-325 (Gradual Migration) | Hedging | "Convert high-value tests first / Keep low-value tests as-is / Use results bridge for legacy tests" — "high-value", "low-value", and "legacy" are all subjective labels with no operational criterion. A test engineer cannot act on this without a yardstick. |
| SUGGESTION | L327-333 (Benefits of Integration) | Marketing | The entire "Benefits" section is promotional bullet copy ("No big-bang migration", "Shared fixtures", "Unified reporting", "CI compatible") that belongs in a landing page, not in an integration how-to. The reader is already here — they don't need to be re-sold. |
| SUGGESTION | L9-14 (Overview) | Throat-clearing | "If you already have pytest tests, you can: 1. Keep existing tests as-is 2. Add Litmus features incrementally 3. Mix Litmus and non-Litmus tests" — this is the page's title restated as a list. Open with the first concrete action instead. |
| SUGGESTION | L101-103 | Throat-clearing / hedging | "For more detailed tracking, wrap the measurement block in a harness step (`harness.step(name)` is a context manager; there is no `harness.finish()` method):" — the apologetic clarification mid-sentence reads as the author defending a previous version's mistake. Strip it. |
| SUGGESTION | L142 | Hedging | "bring your own driver class; Litmus provides the VISA base" — fine, but the surrounding phrasing "# After — bring your own driver class" is a comment, not a sentence. Make the prose state the requirement directly. |
| SUGGESTION | L322-325 (Gradual Migration) | Hedging | "Use results bridge for legacy tests" — "results bridge" is not a term used anywhere else on this page or in the codebase. Either name the actual mechanism (Results API / `LitmusClient`) or delete the bullet. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L25-44 (Step 2 → Step 3) | Cold cross-page drop | `context`, `dmm`, `logger` appear in the first code example with no inline explanation and no link to `reference/litmus-fixtures.md`. A test engineer who lands here from `docs/integration/index.md` has no reason to know `context` is a fixture and not a parameter they have to construct. The opening callout links to the pytest-native reference, but the first example uses fixtures the callout never named. |
| WARNING | L101-125 (Level 2) | Programmer jargon | "Add TestHarness" — `TestHarness` is a Python class name pasted into a heading. A test engineer would expect a verb or a feature ("Track measurements with the test harness"). Headings should describe what the reader gains, not the class they import. |
| WARNING | L70 (Level 1) | Cold cross-page drop | `from litmus.client import LitmusClient` is introduced as the first non-pytest-fixture entry point with no link to its reference (`docs/integration/results-api.md` exists). A reader new to Litmus has no way to discover that there's a dedicated page for this API. |
| WARNING | L106-109 | Programmer jargon | `TestHarness(logger=logger)` and `TestRunLogger(dut_serial="SN001", station_id="bench_1")` are shown bare. The page doesn't say what a "logger" is in Litmus terms (the autouse session fixture that opens the event log and parquet subscriber) or why a test engineer would construct one by hand in a pytest project where the plugin already provides one. A test engineer reading this will rightly wonder "why am I building this myself?" |
| SUGGESTION | L34 | Wrong vocabulary | `--station`, `help="Station ID"` — operator-facing copy across Litmus uses `station_hostname` for the friendly form. If this flag survives in the doc, the help text should match the project convention. |
| SUGGESTION | L185-188 (Run with simulation) | Vocabulary | "`pytest tests/ --station=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001`" — the `--station` value here is a YAML path, but other examples on this page use a bare id (`--station=bench_1`). Test engineers reading the page in order will not know which form is canonical. The configuration reference makes this clear (id vs path); the doc should pick one form for its primary examples. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L30-35 | Doc tells users to add `pytest_addoption` for `--dut-serial`, `--station`, `--mock-instruments` in their own conftest. | Litmus's pytest plugin already registers all three in its own `pytest_addoption`. Re-registering them in a user conftest raises `argparse.ArgumentError: argument --dut-serial: conflicting option string`. Following Step 2 of the page on a fresh project produces an immediate collection error. | `src/litmus/pytest_plugin/hooks.py:896-962` |
| CRITICAL | L117-120 | Doc shows `harness.measure("vcc", vcc, units="V", low=3.2, high=3.4)` and `harness.measure("vdd", vdd, units="V", low=1.7, high=1.9)`. | `TestHarness.measure` signature is `measure(name, value, units=None, limit=None, dut_pin=None, instrument_channel=None, fixture_connection=None)`. There are no `low=` / `high=` kwargs — limits are resolved from the harness's `limits` config or passed as a single `limit=Limit(...)` object. The doc example raises `TypeError: measure() got an unexpected keyword argument 'low'`. | `src/litmus/execution/harness.py:824-833` |
| CRITICAL | L196-203 | Doc shows `from litmus.store import load_station` and `station = load_station("bench_1")`. | `load_station(path: Path)` takes a YAML **path**, not a station id. Loading by id is `get_station("bench_1")`. `load_station("bench_1")` will try to read a file at `./bench_1` and raise `FileNotFoundError`. | `src/litmus/store.py:378-393` |
| CRITICAL | L224-243 | Sidecar YAML example uses the deprecated top-level test-name shape (`test_voltage: { limits: { ... } }`) with each test as a root key. | Current `SidecarConfig` (`extra="forbid"`) requires the flat shape: `limits:` / `sweeps:` / `mocks:` etc. at root (applied to every test), with per-test overrides nested under a `tests:` dict. The doc's shape fails Pydantic validation with `Extra inputs are not permitted` on every test-name key. Compare against the canonical example in `docs/reference/configuration.md:222-247`. | `src/litmus/models/test_config.py:120-200` |
| WARNING | L72-86 (Level 1) | Doc shows `litmus_client.start_run(..., test_phase="production")` and treats `litmus_run` as a per-test fixture wrapping the client. | The `LitmusClient` API is correct (start_run / step / measure / finish all exist), but the per-test `litmus_run` fixture pattern competes with Litmus's own autouse `logger` fixture, which already opens a test run per session. Two parallel run-tracking paths in the same suite will produce two parallel sets of parquet rows. The page doesn't warn the user. | `src/litmus/pytest_plugin/__init__.py:369-420` (autouse `logger`), `src/litmus/client.py:343-410` |
| WARNING | L109 | Doc constructs `logger = TestRunLogger(dut_serial="SN001", station_id="bench_1")` at module top-level. | `TestRunLogger.__init__` auto-detects git info, opens an event log, and starts background daemon notification on construction (see `_get_run_id` + `EnvironmentSnapshot` capture). Building one at module import time runs that side-effect chain at pytest collection, not at test start. The doc gives no warning. The Litmus-blessed path is to take the `logger` fixture, not construct one. | `src/litmus/execution/logger.py:376-470` |
| WARNING | L21-23 | "Install from source (not yet on PyPI)" with `git clone https://github.com/pragmatest-dev/litmus.git`. | The repo URL is correct (matches `pyproject.toml`), but `uv sync` after `git clone && cd litmus` installs Litmus into the cloned repo's venv — it does not install Litmus into the **user's existing pytest project** (which is what the page is about). The reader needs `uv pip install -e /path/to/cloned/litmus` or `uv add /path/to/cloned/litmus` from their own project. | `pyproject.toml:86`; standard uv semantics |
| SUGGESTION | L173-182 (Fixture Patterns) | "DMM from station config" pulls `instruments["dmm"]` via a custom session-scoped fixture. | The Litmus plugin also provides per-role auto-fixtures: any role name in your station YAML's `instruments:` dict is exposed as a pytest fixture by the plugin's auto-fixture mechanism (see the "Per-role auto-fixtures" section in `litmus-fixtures.md`). The wrapper fixtures shown are redundant for the canonical case. | `docs/reference/litmus-fixtures.md:278` ("Per-role auto-fixtures" section) |
| SUGGESTION | L155 | "20-fixture surface" | The fixtures reference currently lists 20 fixtures (`grep -c '^### ' docs/reference/litmus-fixtures.md` → 20). Verified — but pin this to the reference page as the source of truth rather than hard-coding the count in prose elsewhere on the page. | `docs/reference/litmus-fixtures.md` |
| VERIFIED | — | 12 claims verified against source (RunBuilder.step / measure signature, ProjectConfig fields, StationConfig + StationInstrumentConfig fields, LITMUS_MARKER_NAMES count = 7, LitmusClient.start_run kwargs, VisaInstrument import path + simulate kwarg, integration/index.md cross-link targets exist, reference/litmus-fixtures.md fixture count = 20). | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L25-35 (Step 2) | No statement that Litmus's plugin already provides the CLI flags. A reader following the page hits an `argparse` conflict on first `pytest` invocation and has no way to recognize the cause from the page. Either remove the `pytest_addoption` block or precede it with "these flags are already registered by Litmus; only add this if you've disabled the plugin." |
| CRITICAL | L25 (Step 2) | No statement that Litmus's pytest plugin auto-loads via the `pyproject.toml` entry point once `litmus` is installed. A reader new to pytest plugins may try to add `pytest_plugins = ["litmus"]` to their conftest or wonder why fixtures appear "out of nowhere." State the auto-discovery contract once, then everything that follows reads correctly. |
| CRITICAL | L210-220 (Configuration Files / Project Config) | No statement of where `litmus.yaml` must live (project root) or what happens when it's missing. A reader integrating Litmus into an existing project needs to know whether they need this file at all to run the Step 3 example. The configuration reference covers this — the page should at minimum link to it. |
| WARNING | L70-86 (Level 1: Just Results) | What happens if two tests run in parallel and each creates its own `litmus_run`? The `function`-scoped fixture creates a fresh `LitmusClient` per test, each writing to the same `data_dir` — does this collide on parquet writes? The page doesn't say. |
| WARNING | L102-125 (Level 2) | What happens if `TestRunLogger` is constructed at module level and pytest is interrupted between collection and the first test? Does the half-built run write to parquet? Is there a way to abort? The page doesn't say. |
| WARNING | L165-188 (Using Station Instruments) | Where does the `instruments` fixture come from? The page shows `def dmm(instruments)` but never explains that `instruments` is supplied by the Litmus plugin and depends on `--station=<id>` resolving to a station YAML. A reader who hasn't created a `stations/bench_1.yaml` will see `fixture 'instruments' not found`. |
| WARNING | L286-307 (Mark Tests) | The `@pytest.mark.litmus` marker is shown without telling the reader they need to register it via `pytest.ini` / `pyproject.toml` `markers = [...]` or `--strict-markers` will warn. A reader copy-pasting this gets warnings at collection. |
| SUGGESTION | L260-271 (CI/CD) | The CI snippet runs with `--station=ci_station` but no example of the station YAML for a CI environment (mock-only, no hardware) is shown. The reader has to know to set every instrument to `mock: true` — and that constraint is on a different page. |
| SUGGESTION | L327-333 (Benefits) | No discussion of what does **not** work in incremental adoption: e.g., does `verify` work without a station? Does `logger.measure` work outside a Litmus session? The page sells the path but doesn't acknowledge the limits. |
| SUGGESTION | L335-341 (Next Steps) | Missing a "How do I know it worked?" section. After Step 4 runs, what does a reader look at to confirm the test results were captured? Suggest `litmus runs` or `litmus serve` as a verification handoff. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L41-42, L91, L106-109, L158-160 | First use of `context`, `dmm`, `logger`, `verify` fixtures (L41), `LitmusClient` (L70), `TestHarness` + `TestRunLogger` (L106-109) — none link to their reference pages. The opening callout (L5-7) links to `pytest-native.md` but not to `litmus-fixtures.md` where these specific fixtures are defined. |
| WARNING | L337-341 (Next Steps) | Missing "See also" entries for the closely related siblings in the same `docs/integration/` directory: `results-api.md` (the canonical doc for the `LitmusClient` shown in Level 1), `openhtf-adapter.md` (relevant for the same migration audience), `lakehouse-import.md`, `logging.md`. All exist; none are linked. |
| WARNING | L337-341 (Next Steps) | "Test Harness" and "Instrument Drivers" links use the page titles `harness.md` and `instruments.md` — relative paths resolved to `docs/integration/harness.md` and `docs/integration/instruments.md`. Verified: both files exist. However the link **text** ("Test Harness", "Instrument Drivers") doesn't match the actual page titles (`harness.md` is "Harness" / `instruments.md` is "Instruments" per `docs/integration/index.md`). Align wording with the actual titles to avoid surprise. |
| WARNING | L155 | Inline parenthetical links to `../reference/litmus-fixtures.md` — but this is the first link the reader sees to the fixtures reference, and it's buried 130+ lines in. Promote the link to the page's opening callout (L5-7) so it's available at the first cold-drop. |
| SUGGESTION | L210-243 (Configuration Files) | "Project Config" and "Test Config" examples have no link to `docs/reference/configuration.md`, which is the authoritative source for both shapes (and would have prevented the deprecated-sidecar bug under Accuracy). |
| SUGGESTION | L165-188 (Fixture Patterns) | "The recommended approach is to use the `instruments` fixture from station config" — no link to the station configuration how-to (`docs/how-to/configuring-stations.md`) or to the fixtures reference's `instruments` entry. |
| SUGGESTION | L153-160 (Level 4: Full pytest-native) | "see [Litmus fixtures](../reference/litmus-fixtures.md) for the full 20-fixture surface" — this is the right link, but it could also link to `docs/concepts/fixtures.md` (the concept page that explains *why* the fixtures are shaped this way), since this is a migration audience asking "what am I getting into?" |
| SUGGESTION | L153-160 | "Convert tests to use Litmus's per-test fixtures" — could link to `docs/how-to/writing-tests.md` as the natural next how-to for a reader who's reached Level 4. |
