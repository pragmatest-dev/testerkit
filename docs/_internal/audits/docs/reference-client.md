# Page audit: docs/reference/client.md

**Quadrant:** Reference (LitmusClient — submitting test runs from non-pytest sources)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 3 | 2 |
| Audience | 1 | 3 | 1 |
| Accuracy | 3 | 5 | 3 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **7** | **20** | **12** |

---

## Ordering

**WARNING — "Installation" heading carries no installation content**
Section 5–9 is labelled "Installation" but its body is a single `from litmus.client import LitmusClient` import. There is no `pip install`, no extras, no environment guidance. For a reader who arrives on this page from `reference/index.md` ("Suits LabVIEW / TestStand bridges"), the first section should answer "what package and which Python?" before the import. Either retitle to "Import" or expand to include install command, Python-version requirement, and where the result files end up.

**WARNING — "Querying Results" is buried beneath integration recipes**
The page goes: Basic Usage → API Reference → Complete Example → Integration Patterns (LabVIEW / TestStand / CLI) → Querying Results → Next Steps. A reference reader scanning for "how do I read back what I wrote?" hits three pages of recipes first. Querying is a peer of writing — it should sit immediately after "API Reference" (or its `get_*` methods should be expanded inline in the API Reference table rather than re-introduced 200 lines later).

**SUGGESTION — Comparator table appears under "Measurements" but applies to vector and step measure too**
The Comparator table is nested inside `### Measurements`, which is itself nested inside `### StepBuilder` by document flow. Readers will wonder whether `VectorBuilder.measure` accepts the same comparators (it does). Either promote the comparator table to a peer-level section or add one line under `VectorBuilder` saying "same signature as `StepBuilder.measure` (see [Measurements](#measurements))." There is currently no `VectorBuilder` section at all (see Gaps).

**SUGGESTION — "Integration Patterns" mixes a sketch with two working code blocks**
LabVIEW is a fenced pseudo-code block describing a Python Node configuration; TestStand and CLI are real, runnable Python. The asymmetry signals "we don't actually know how LabVIEW calls this." Either drop LabVIEW or replace the sketch with a runnable Python wrapper that LabVIEW's Python Node can target (mirroring the TestStand structure).

---

## Voice

**WARNING — Page slides between Tutorial and Reference voice**
A pure reference page tells the reader what each symbol is and how it behaves, in stable noun-verb form. This page instead opens with "Get up and running…" energy ("The `LitmusClient` provides a simple API…", "Basic Usage", "Complete Example", "Next Steps → Quick Start"). The reference quadrant should not have a "Complete Example" section — that belongs in a how-to or tutorial. Trim "Basic Usage" and "Complete Example" to a single minimal example near the top, and let the API Reference do the work.

**WARNING — Inconsistent person and mood across sections**
- "Get up and running" / "saves results to ./results by default" — second-person tutorial voice.
- "Returned by `client.start_run()`." — declarative reference voice.
- "Use this for parametrized tests where you want to record…" — second-person again (in the inline `vector()` description, paraphrased from source).

Pick one. Reference convention here (see `connect.md`, `litmus-fixtures.md`) is third-person declarative: "Returns…", "Marks the step as failed."

**WARNING — "Simple API" / "simple measurements" / "simple tests" — filler adjectives**
"Simple" appears three times (`L3`, `L155`, source-copied phrases). It's an editorial verdict, not a fact, and it primes readers to feel bad when their case isn't simple. Drop "simple" everywhere and let the API speak for itself.

**SUGGESTION — Tagline conflates the client with the use case**
"The `LitmusClient` provides a simple API for submitting test results from external tools — LabVIEW, TestStand, custom scripts, or any system that can call Python." A reference page should describe the symbol, not advertise its market. Suggested: "`LitmusClient` writes test-run parquet files from Python without requiring pytest. The same parquet schema `pytest` produces; readable by `litmus runs`, the operator UI, and the HTTP API." This also fixes the platform-vs-plugin framing — "external tools" implies non-Litmus, when LitmusClient IS the Litmus catch-all surface.

**SUGGESTION — "Next Steps" is a tutorial concept**
Reference pages end with "See also" or "Related," not "Next Steps." The current "Next Steps" link to `tutorial/00-quickstart.md` is doubly wrong — see Audience.

---

## Audience

**CRITICAL — "Next Steps → Quick Start (Getting started guide)" sends a non-pytest user to a pytest tutorial**
The page is explicitly for "LabVIEW, TestStand, custom scripts, or any system that can call Python" — i.e., people who chose this path because they are NOT writing pytest. The current "Next Steps" link drops them into `tutorial/00-quickstart.md`, which immediately runs `litmus init --starter && pytest`. That is the wrong audience entirely. The correct onward links are: `connect.md` (interactive instrument access), `integration/results-api.md` or `integration/pytest-existing.md` for migration, `reference/parquet-schema.md` for the result shape they just wrote.

**WARNING — Page assumes Python fluency without naming it**
The opener mentions LabVIEW and TestStand as primary audiences, but every example assumes Python idioms — context managers, kwargs-only parameters, f-strings, `sys.argv`. A TestStand engineer evaluating "should I use this?" needs one sentence up top: "Requires Python ≥ 3.11 on the test station. LabVIEW calls it via the Python Node; TestStand via the Python adapter or subprocess."

**WARNING — "From LabVIEW" example is opaque to LabVIEW engineers**
The LabVIEW snippet:

```
Python Node
├── Module: litmus
├── Function: submit_result
└── Inputs: serial, station, measurements[]
```

A LabVIEW engineer cannot copy this. They need to know: which `.py` file is "Module: litmus" — is it the installed package or a wrapper script? `submit_result` is not a function in `litmus.client` (verified — see Accuracy). The TestStand block one section down DOES write a wrapper module (`wrapper.py`) — the LabVIEW block needs the same: a runnable wrapper plus the LabVIEW Python-Node call sites that target it.

**WARNING — "test_phase" and "station_type" are undocumented domain terms**
Tables show `station_type="production"` and `test_phase="production"` as accepted values, and the prose nowhere distinguishes them. A LabVIEW or TestStand engineer onboarding has no way to know: what are the legal `test_phase` values? Is it free text? Is "production" magic? Same for `station_type`. Reference must enumerate (or, if free text, say "free text — convention: production / characterization / bringup").

**SUGGESTION — Reader-task mapping for the bullet "any system that can call Python"**
"Any system that can call Python" is true but useless. The integration section already enumerates three. Add a fourth pattern (Jupyter / ad-hoc script / cron job) that doesn't involve cross-language bridging, so the operator who just wants `python my_oneoff.py` sees themselves on the page.

---

## Accuracy

**CRITICAL — `submit_result` does not exist**
The LabVIEW Python-Node snippet (L182) names `Function: submit_result`. There is no `submit_result` function in `litmus.client`, in `litmus/__init__.py`, or anywhere in `src/litmus/`. Verified by grep. A LabVIEW engineer who pastes this into a Python Node will get `AttributeError: module 'litmus' has no attribute 'submit_result'`. The wrapper they need is `LitmusClient().start_run(...).step(...).measure(...).finish()`, or a user-written `def submit_result(...)` in a wrapper module — which the doc does not provide.

**CRITICAL — Default `data_dir="results"` is CWD-relative and won't show up in the UI / CLI**
`LitmusClient.__init__(self, data_dir: str | Path = "results")` writes to `./results/` relative to the working directory at instantiation time (`src/litmus/client.py:360-366`). The rest of Litmus (operator UI, `litmus runs`, `litmus serve`, MCP tools) reads from `resolve_data_dir()` which resolves via `litmus.yaml` → `LITMUS_HOME` → `platformdirs.user_data_dir("litmus")` (`src/litmus/data/data_dir.py:32-61`). The two paths only coincide when CWD happens to be a project root containing a `litmus.yaml` with `data_dir: results`. For LabVIEW / TestStand bridges started from any other CWD (typical: `C:\Program Files\…`, `C:\TestStand\…`), the run lands in a directory the UI never reads. The page does not warn about this and does not show how to point the client at the canonical location. Either change the default to call `resolve_data_dir()`, or document the trap explicitly.

**CRITICAL — Comparator table has incorrect pass conditions for LT/LE/GT/GE**
Page L116-126 states:

| `LT` | value < high |
| `LE` | value <= high |
| `GT` | value > low |
| `GE` | value >= low |

But `_COMPARATOR_CHECKS` in `src/litmus/models/test_config.py:220-223`:

```python
"LT": lambda lim, v: lim.high is None or v < lim.high,
"LE": lambda lim, v: lim.high is None or v <= lim.high,
"GT": lambda lim, v: lim.low is None or v > lim.low,
"GE": lambda lim, v: lim.low is None or v >= lim.low,
```

The actual semantic is "pass iff the relevant limit is unset OR the inequality holds." In particular, with no limit at all the comparator passes — the doc shows them as strict comparisons that would fail when `high=None` or `low=None`. The doc table is right about the inequality direction but wrong about the boundary case. Also missing from the table entirely: `GELT`, `GTLE`, `GTLT` (all four range-comparators are implemented and listed in the source docstring of `VectorBuilder.measure`).

**WARNING — Properties table for `RunBuilder` is misleadingly thin**
The doc lists only `run.id` (L72-73). But `RunBuilder` exposes nothing else as a property; the underlying `TestRun` has `session_id`, `dut`, `outcome`, etc. — none of which the builder surfaces. A reader scanning the Properties table will think the run has no useful state. Either drop the Properties table entirely (it has one row), or note "the builder is write-only; query the saved run via `client.get_run(run.id)` after `finish()`."

**WARNING — `run.finish()` return is unspecified by the table**
Doc table (L80) says `run.finish()` → "Finalize and save the run". Source signature is `finish(self) -> TestRun`, and the Complete Example uses the return value as `result = run.finish(); print(f"Test complete: {result.outcome}")`. Reference must list the return shape on the API row, not only by example 100 lines later.

**WARNING — Comparator `EQ`/`NE` description is incomplete**
The Comparator table reads `EQ: value == nominal`. The source `_COMPARATOR_CHECKS["EQ"]` requires `lim.nominal is not None`; otherwise it returns False. With no `nominal` supplied, the EQ comparator silently fails every measurement. Documentation should say "requires `nominal=`."

**WARNING — `get_measurements` return shape claim is partially wrong**
Page L252 says: "Get measurements — returns list[dict] using parquet column names". Source: `RunStore.get_measurements` expands `dynamic_attrs` MAP into top-level keys and coerces numeric-string values to float (`src/litmus/data/run_store.py:210-229`). So callers see a mix of (a) parquet columns (`measurement_name`, `measurement_value`, …) AND (b) dynamic keys derived from `out_*` / `in_*` columns AND (c) coerced floats from VARCHAR-typed dynamic attrs. The doc's three-key example obscures that any input-param column (`in_voltage`, `in_temperature`) and observation column (`out_temp`, …) will also appear. Be explicit.

**WARNING — "operator" parameter is documented but actually stored as `operator_id`**
Page L64 documents `operator="Jane Doe"`. Source `RunBuilder.__init__` passes this to `TestRun(operator_id=operator, …)` (`src/litmus/client.py:288`). On readback, the value lives under `RunSummary.operator` (which is populated from the parquet `operator_id` column — `src/litmus/data/run_store.py:162`). The mapping is non-obvious; a reader who writes `operator="Jane Doe"` and then queries the parquet via DuckDB will look for a column called `operator` and find `operator_id` instead. Document the round-trip.

**SUGGESTION — Comparator default note ("default: GELE") buried in the table caption**
L111 says `# Optional: comparison mode (default: GELE)`. The Comparator table re-states "GELE (default)". Both are correct, but the rule "if you supply `low`/`high`, GELE; if you supply `nominal` only, you need EQ/NE explicitly" is not stated and is the actual gotcha for new readers.

**SUGGESTION — "Save results" comment on `run.finish()` is a tutorial cue, not the truth**
L31 comment: `# Save results`. The method does more than save — it timestamps `ended_at`, propagates outcome from steps, then writes the parquet. The comment is fine for tutorial flow; for reference, prefer "finalize and persist."

**SUGGESTION — Parquet write location is implied but never stated**
Source: `ParquetBackend.save_test_run` writes to `<data_dir>/runs/<YYYY-MM-DD>/<timestamp>[_serial].parquet` (`src/litmus/data/backends/parquet.py:185-220`). The page never tells a LabVIEW engineer where their files end up. One sentence under `LitmusClient(data_dir=…)` would close this.

---

## Gaps

**CRITICAL — No `VectorBuilder` section**
`RunBuilder` and `StepBuilder` each get their own API Reference subsection. `VectorBuilder` does not. Its methods (`measure`, `fail`, `skip`) are referenced obliquely under "Test Vectors" but never enumerated. Source defines `VectorBuilder` as a peer of `StepBuilder` with the same `.measure()`, `.fail()`, `.skip()` surface (`src/litmus/client.py:61-150`). Add `### VectorBuilder` after StepBuilder, with the same table format.

**CRITICAL — No guidance on multi-process safety / concurrent writers**
The "submit results from anywhere" framing invites the question "can my line of 4 testers all call `LitmusClient()` concurrently?" The answer is non-trivial (parquet files are per-run, but the daemon coordination for the operator UI requires running through `resolve_data_dir()`). Reference must address it — either "yes, each process gets its own parquet file; collisions are by `timestamp_serial`" or "no, use one of these patterns." Currently silent.

**WARNING — No error / exception documentation**
What happens if `data_dir` is unwritable? If `dut_serial` is empty? If `finish()` is called twice? If a `step.measure(name, None)` is recorded? (Source: outcome becomes `ERRORED` — `src/litmus/data/models.py:211-212`.) None of this appears. A reference page must enumerate the failure modes.

**WARNING — No retry / outcome-cascade documentation**
Page silently shows `run.finish()` returning a run with `outcome`, but never explains how outcome is computed. The cascade is `measurement → vector → step → run` with severity-worst-wins (`Outcome.severity`, `escalate_outcome` in `data/models.py`). Without this, the reader can't predict whether mixing `step.fail()` plus passing measurements yields PASSED or FAILED at the run level.

**WARNING — No mention of session_id / cross-store join key**
`TestRun.session_id` is auto-generated (`data/models.py:391`) and is the key Litmus uses to join run results to events and channels. A non-pytest user who wants to attach channel data later will need to know this id exists and that it's reachable via `run._test_run.session_id` (currently no public accessor — also a gap in the public surface). Reference page should at minimum acknowledge it.

**WARNING — `run.abort()` semantics are under-documented**
Doc says "Abort without saving" and "Returns: aborted TestRun (not saved)". What this actually means: the run is invisible to `litmus runs`, the operator UI, the HTTP API. There's no event emitted, no trace. That's a strong claim — make it explicit, and contrast with `run.finish()` after `step.fail()` (which IS saved with `outcome=FAILED`).

**SUGGESTION — No `step.observe()` / `step.measure(name, value)` no-limits variant note**
A reader scanning `step.measure(...)` doesn't realize "call it with just `(name, value)` and no limits, and you get `outcome=DONE`" — the recorder semantic. The behavior IS in source (`client.py:120-122`, comment "No limit configured → recorder semantic"). A one-sentence callout under Measurements would close the loop.

**SUGGESTION — No example of reading a previously-written run back via the same client**
Querying Results shows `list_runs` / `get_run` / `get_measurements`, but the example creates a fresh client at L241 (`client = LitmusClient()`) — implying the reader has to re-instantiate. State whether the same client works for read-after-write (it does), and whether `client.list_runs()` immediately shows a just-`finish()`ed run (it does, modulo daemon visibility — see also the data_dir gap).

---

## Cross-links

**CRITICAL — Two pages document the same API surface**
`docs/reference/client.md` (this page) and `docs/integration/results-api.md` both document `LitmusClient` with overlapping but not identical content (results-api.md L34-80 reproduces the same API tables with slightly different examples). One is canonical reference, the other is integration recipes — but right now they drift. Either:
- make `integration/results-api.md` a brief intro that links to this reference page, or
- make this page the integration narrative and host the API tables somewhere else (e.g., generate from docstrings).

The current state guarantees the two will desync.

**WARNING — `Next Steps` does not link to integration pages where LitmusClient is used in context**
The page's only "Next Steps" entry beyond API reference is `tutorial/00-quickstart.md` (wrong audience — see Audience). It does NOT link to:
- `integration/results-api.md` — the actual integration narrative
- `integration/pytest-existing.md` — uses LitmusClient inside conftest
- `integration/openhtf-adapter.md` — uses LitmusClient as the backing store
- `reference/parquet-schema.md` — what the writer just wrote
- `reference/connect.md` — the sibling non-pytest surface (for instrument access)
- `concepts/outcomes.md` — explains the outcome cascade (cited heavily from `client.py`)

The reference index page (`docs/reference/index.md`) does the right grouping ("Submitting results from outside pytest" links `client.md`, `connect.md`, `api.md`); this page should mirror that.

**WARNING — Comparator table does not link to `Limit` / spec-driven testing**
The Comparator section is the natural junction to `how-to/limits.md`, `how-to/spec-driven-testing.md`, and `reference/litmus-markers.md` (`litmus_limits` marker — same comparator vocabulary). Right now those connections are invisible.

**WARNING — `dut_part_number`, `station_id`, `operator` reference no glossary / concept page**
These are operator-facing identifiers governed by a memory-level rule (`feedback_operator_facing_identifiers.md`: station → `station_hostname`, product → `dut_part_number`). The fields here use `station_id` (per API), but a reader has no link explaining why `station_id` is "the bench identifier", what naming convention to follow, or where Litmus reads it back. Link to `concepts/stations.md` and `concepts/products.md`.

**SUGGESTION — `litmus runs` / `litmus show` link**
A non-pytest user who writes their first run will immediately want to verify it landed. Link `cli.md` from "Querying Results" so they can do `litmus runs` from the shell instead of writing more Python. Currently no link.

**SUGGESTION — Backlinks from `integration/*` pages should land in the Cross-links section here**
`integration/openhtf-adapter.md` and `integration/pytest-existing.md` both import `LitmusClient`. This page does not point back to either. A "See also" / "Used by" block listing the integration pages closes the navigation loop.
