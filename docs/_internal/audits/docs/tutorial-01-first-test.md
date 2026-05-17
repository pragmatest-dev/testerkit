# Page audit: docs/tutorial/01-first-test.md

**Quadrant:** Tutorial (Step 1 of 10 — writing the first test)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 2 | 2 | 1 |
| Voice | 0 | 3 | 2 |
| Audience | 2 | 3 | 1 |
| Accuracy | 1 | 2 | 1 |
| Gaps | 2 | 3 | 2 |
| Cross-links | 1 | 1 | 1 |
| **Total** | **8** | **14** | **8** |

---

## Ordering

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial
**Method:** Evaluate whether the section sequence follows expected Tutorial flow: orient → prereqs → action → observe → explain → validate → troubleshoot → recap → navigate.

### Findings

**CRITICAL — "Bench-bringup pattern" section (lines 78–110) is fatally out of place**

The page's stated goal is "A simple test that passes. Nothing fancy yet." The bench-bringup section introduces `Limit`, `verify`, parquet columns, traceability fields, station/product/fixture YAML — all concepts that belong in steps 3–7. This section appears between "About conftest.py" and "Verify the Setup", breaking the flow of a step-1 narrative. A reader who has only just been told to write `assert True` is suddenly reading about `measurement_outcome` columns and NULL traceability fields. The mismatch between the stated goal and the section content is severe enough to undermine trust in the tutorial.

**CRITICAL — "Verify the Setup" appears after the advanced "Bench-bringup pattern"**

The "Verify the Setup" section (lines 112–120) shows how to confirm pytest discovered the tests (`--collect-only`). This validation step belongs immediately after "The Code" / the first `pytest` run block — not buried after an advanced scaffold example. A reader following the primary narrative will have already run their test and moved on; finding this section late makes it feel like an afterthought rather than a confirmation step.

**WARNING — "About conftest.py" (lines 72–76) precedes the bench-bringup pattern but has no narrative bridge**

The "About conftest.py" section introduces `conftest.py`, `dmm`, and `psu` fixtures before those names appear in the bench-bringup code below it. The section ends with "For step 1, ignore station YAML entirely" — which is the right advice, but it is immediately followed by a section that uses Litmus-specific fixtures the reader was just told to ignore. The ordering creates a read-then-contradict loop.

**WARNING — "Why Start Simple?" appears after "Project Structure"**

Tutorials conventionally place the rationale early — either in the intro or just before the first action. Placing "Why Start Simple?" (lines 64–70) after the code sample and project structure diagram means the reader encountered the action before the motivation. Moving this above "The Code" or folding it into the intro would improve the learning arc.

**SUGGESTION — "Project Structure" (lines 49–60) position is serviceable but slightly late**

Project structure is orientation information. It fits best between "Prerequisites" and "The Code", so the reader knows where to put the file they are about to create. Its current placement — after "What's Happening" — is not wrong, but shifting it earlier would tighten the setup-then-act sequence.

---

## Voice

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial
**Method:** Check for consistent second-person imperative voice, active sentences, and tone appropriate to a tutorial (encouraging, directive, not passive or lecture-y).

### Findings

**WARNING — "This step uses a `conftest.py`..." (line 74) uses third-person narration**

"This step uses a `conftest.py` to define the `dmm` (and later `psu`) fixtures." A tutorial speaks directly to the reader. Preferred form: "In this step, you define a `conftest.py` to hold the `dmm` (and later `psu`) fixtures."

**WARNING — "Rows land in parquet with... populated." (line 108) is passive reference prose in a tutorial**

"Rows land in parquet with `measurement_value`, `limit_low` / `limit_high`, and `measurement_outcome` populated." This reads like a reference page. A tutorial framing might say: "When you run this, Litmus writes the measurement value, limits, and outcome to a parquet row — you'll see those column names again in later steps." However, the deeper issue (see Audience and Gaps) is whether this sentence belongs on this page at all.

**WARNING — "This shows what pytest discovered without running tests." (line 120) is third-person**

Should be: "You can see what pytest discovered without actually running any tests."

**SUGGESTION — Parenthetical definition dump (line 80) reads like inline documentation, not tutorial prose**

"(Forward references: `Limit` is Litmus's pass/fail-bound model, `verify` is the fixture that records a measurement and checks it against a limit — both introduced fully in step 3 / step 4. [PyVISA](...) and [PyMeasure](...) are the external instrument-driver libraries you'd swap into the fixture for real hardware.)" This parenthetical is doing explanation work that belongs in those later steps, and its voice is encyclopedic rather than conversational. If forward-linking is needed, a single sentence with links ("You'll use `Limit` and `verify` properly in steps 3 and 4.") is enough.

**SUGGESTION — "For a brand-new board, the smallest scaffold is just a `conftest.py` fixture and one test." (line 80) is impersonal**

The subject is omitted. Tutorial voice prefers: "When you start with a brand-new board, begin with a `conftest.py` fixture and one test."

---

## Audience

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial (Step 1 of 10)
**Method:** Assess whether assumed knowledge, introduced jargon, and section scope match a reader new to Litmus at step 1 of a progressive tutorial.

### Findings

**CRITICAL — "Bench-bringup pattern" (lines 78–110) violates the stated step scope**

The stated goal is "A simple test that passes. Nothing fancy yet." The bench-bringup section introduces, without prior definition: `MagicMock`, `Limit(low=, high=, nominal=, units=)`, the `verify` fixture, parquet as a storage format, and four traceability column names (`dut_pin`, `instrument_channel`, `fixture_connection`, `spec_ref`). It then introduces station YAML, product YAML, and fixture YAML as future concepts — none of which have been explained. A reader at step 1 has no mental model for any of these. The section is not gated ("optional" / "advanced") and appears in the main flow, so the reader has no signal that they can skip it.

**CRITICAL — "About conftest.py" (lines 72–76) describes a pattern the primary example does not use**

The primary instructed test (`test_hello.py`, lines 22–27) uses no fixtures. The "About conftest.py" section then says "This step uses a `conftest.py` to define the `dmm` (and later `psu`) fixtures" — which is not true of the primary example. A reader following the main flow will look at their `test_hello.py`, find no conftest, and be confused about what "this step" is referring to.

**WARNING — "parquet" mentioned without definition (line 108)**

Step 1 is the first tutorial step. Parquet as a storage format is never explained at this point. A reader who is new to Litmus (and possibly new to hardware testing toolchains) will not know what parquet is or why it matters.

**WARNING — "litmus init --tier=bringup" mentioned without context (line 80)**

The `litmus init` command is mentioned as something that "creates this layout" but there is no explanation of what `litmus init` is, where the user runs it (from which directory), or what output to expect. The quickstart (00-quickstart.md) uses `litmus init quick_start --starter` but does not introduce `--tier=bringup`. A reader arriving at step 1 from the quickstart will not know this command.

**WARNING — Project Structure diagram (lines 49–60) shows `pyproject.toml` with no creation instructions**

The Prerequisites section clones the litmus repo itself (`git clone ... && cd litmus && uv sync`). A reader who followed those instructions is now inside the litmus repository, not their own `my_project/`. The Project Structure diagram shows `my_project/` with `pyproject.toml`, but there are no instructions for creating this structure. The two narratives (clone litmus vs. create your own project) are in tension.

**SUGGESTION — Forward-reference parenthetical (line 80) creates cognitive overload**

The parenthetical mentions `Limit`, `verify`, PyVISA, PyMeasure, fixture connections, and characteristic IDs in a single aside. Even with links to later steps, this is too many new concepts to drop on a step-1 reader. A single-sentence forward reference ("You'll build on this pattern in steps 3 and 4.") serves the same navigational function without the load.

---

## Accuracy

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial
**Method:** Verify every factual claim against source code, CLI definitions, schema files, and example directories.

### Verified claims (pass)

- `litmus init --tier=bringup` — exists (`src/litmus/cli.py` line 49–50; `src/litmus/init.py` line 38).
- `Limit` is in `litmus.models.test_config` — confirmed (`src/litmus/models/test_config.py` line 233).
- `verify` is a Litmus fixture — confirmed (`src/litmus/pytest_plugin/__init__.py`; documented in `docs/reference/litmus-fixtures.md` line 29).
- `measurement_value`, `measurement_outcome` parquet column names — confirmed (`src/litmus/data/schemas.py` lines 107, 109).
- `limit_low` / `limit_high` parquet column names — confirmed (`src/litmus/models/test_config.py` docstring lines 307–308; `src/litmus/data/_accumulator_pool.py`).
- `dut_pin`, `instrument_channel`, `fixture_connection`, `spec_ref` columns — all confirmed in `src/litmus/data/schemas.py` and `src/litmus/models/test_config.py`.
- Auto-registration of instrument role fixtures from station YAML — confirmed (`src/litmus/pytest_plugin/hooks.py` lines 190, 232, 261–274).
- `examples/01-vanilla` exists at the repo path.
- GitHub URL `https://github.com/pragmatest-dev/litmus` — matches `pyproject.toml` lines 86–87.

### Findings

**CRITICAL — The link anchor `#verify--function` (line 80) is broken**

The link `../reference/litmus-fixtures.md#verify--function` points to an anchor that does not exist. The heading in `litmus-fixtures.md` is `### \`verify\` — function` (line 29). MkDocs slugifies this to `verify-function` (backticks stripped, em dash and surrounding spaces collapsed to one hyphen, then deduplicated). The correct anchor is `#verify-function`, not `#verify--function`. A reader clicking this link will land at the top of the page, not at the `verify` section.

**WARNING — `examples/01-vanilla` link points to an example that does not demonstrate the bench-bringup pattern (line 110)**

"See `examples/01-vanilla` for a runnable example." is placed immediately after the bench-bringup pattern (MagicMock + Limit + verify). But `examples/01-vanilla` uses plain `assert` statements with no Litmus fixtures (`test_rail.py` lines 22–24: `assert 3.2 <= v <= 3.4`). It explicitly documents itself as "No Litmus features are in use." The bench-bringup pattern in the tutorial is closer to what `litmus init --tier=bringup` generates. The link is misleading: it implies 01-vanilla demonstrates the preceding code, but it does not.

**WARNING — Prerequisites clone the litmus repo itself, not scaffold a new project (lines 11–15)**

The instructions say `git clone https://github.com/pragmatest-dev/litmus.git && cd litmus && uv sync`. This puts the reader inside the Litmus source repository. The "Project Structure" section then shows `my_project/` as the expected layout. These two narratives are contradictory. The correct prerequisite for a new project should be `pip install litmus-test` (or `uv add litmus-test`) followed by `litmus init`, matching what `00-quickstart.md` shows.

**SUGGESTION — MagicMock return value and `float()` wrapper are correct but the interaction is non-obvious**

Line 92 sets `inst.measure_dc_voltage.return_value = 3.3`, and line 103 calls `float(dmm.measure_dc_voltage())`. Because `return_value` is already `3.3`, `float(3.3)` succeeds. However, if a reader copies the `float()` pattern without setting `return_value`, `float(MagicMock())` raises `TypeError`. A brief comment in the code ("# return_value=3.3 makes measure_dc_voltage() return a float") would prevent confusion.

---

## Gaps

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial (Step 1 of 10)
**Method:** Identify missing content, incomplete narratives, unexplained forward references, and concepts the page promises but does not deliver.

### Findings

**CRITICAL — Installation path for a new project is absent**

The Prerequisites section instructs the reader to clone the litmus source repo (`git clone ... && cd litmus && uv sync`). There is no instruction for the more common scenario: installing Litmus into a new project (`pip install litmus-test` or `uv add litmus-test`, then `litmus init`). The quickstart at `00-quickstart.md` uses `pip install litmus-test` as its first step. Step 1 contradicts the quickstart without explanation, leaving the reader with no clear path if they have already installed Litmus from PyPI.

**CRITICAL — No narrative bridge between the primary example and the bench-bringup pattern**

The primary instructed flow is: create `test_hello.py` with `assert True`, run `pytest`, see it pass. The bench-bringup section then jumps to a `conftest.py` with `MagicMock`, `Limit`, and `verify` — with no sentence connecting the two. A reader who completed the primary flow has no context for why a second, more complex example appears on the same page. At minimum, the section needs a sentence like "If your project has real hardware, here is the minimal starting point; skip this for now and return after step 4." Without that bridge, the section reads as an accidental paste from a different document.

**WARNING — No explicit "you succeeded" marker after the primary run**

After `pytest tests/test_hello.py -v`, the expected output shows `PASSED`. There is no sentence confirming "If you see PASSED, your environment is working." The "Verify the Setup" section uses `--collect-only`, which is a different operation than running tests. A beginner may not know that `PASSED` is the success signal, and no troubleshooting entry handles "I see a different result."

**WARNING — "litmus init --tier=bringup" output is not shown**

Line 80 says "`litmus init --tier=bringup` creates this layout" — but what layout? The reader is shown a `conftest.py` and a `test_smoke.py`, but there is no tree diagram showing what files `litmus init --tier=bringup` actually creates, and no expected terminal output. Compare to how the quickstart shows `litmus init quick_start --starter` in a complete, runnable sequence.

**WARNING — Traceability columns described before the reader knows Litmus records anything**

Line 108 says "Traceability columns (`dut_pin`, `instrument_channel`, `fixture_connection`, `spec_ref`) stay NULL until you graduate to station + product + fixture." This statement assumes the reader knows: (1) Litmus records measurements to parquet, (2) what those four columns represent, (3) what station/product/fixture YAML files are. None of these have been introduced at step 1. The statement is accurate but premature.

**SUGGESTION — No link back to tutorial index or step 0 (quickstart)**

The only navigation link is "Next Step" at the bottom. A reader who arrived at step 1 from the docs index or quickstart has no way to go back. A breadcrumb or "Previous: Quickstart" link at the top or bottom would complete the navigation.

**SUGGESTION — "What You Learned" recap (lines 139–143) describes only the basic pytest facts, not anything Litmus-specific**

The recap says the reader learned: how to create a pytest test file, how to run tests with pytest, and basic project structure. These are pytest fundamentals, not Litmus knowledge. If step 1 deliberately teaches only pytest basics, that is fine — but the recap should acknowledge it explicitly ("Step 1 intentionally uses only pytest. Step 2 adds the first Litmus feature.") rather than leaving the reader to wonder what Litmus contributed.

---

## Cross-links

**Page:** `docs/tutorial/01-first-test.md`
**Quadrant:** Tutorial
**Method:** Check every link (internal and external) for target existence, anchor correctness, and whether links that should exist are missing.

### Findings

**CRITICAL — Anchor `#verify--function` (line 80) is broken**

Link: `../reference/litmus-fixtures.md#verify--function`

The target file exists (`docs/reference/litmus-fixtures.md`). The heading at line 29 of that file is `### \`verify\` — function`. MkDocs slugifies this to `verify-function` (em dash and surrounding spaces collapse to a single hyphen after special-character stripping). The link uses `#verify--function` (double hyphen), which does not match. The reader lands at the top of the reference page rather than the `verify` section.

Correct link: `../reference/litmus-fixtures.md#verify-function`

**WARNING — `examples/01-vanilla` external link leads to an example inconsistent with the surrounding text**

Link: `https://github.com/pragmatest-dev/litmus/tree/main/examples/01-vanilla` (line 110)

The file `examples/01-vanilla/tests/test_rail.py` explicitly uses only `assert` statements ("No Litmus features are in use"). The bench-bringup pattern immediately above the link uses `Limit` and `verify`. A reader clicking through will find an example that does not match what they just read. The correct forward-reference for the bench-bringup pattern is either `examples/02-verify` or the scaffold generated by `litmus init --tier=bringup`.

**SUGGESTION — No link to tutorial index or step 0 (quickstart) from the page navigation**

The page links forward to `02-mock-instruments.md` but has no backward link to `00-quickstart.md` or the tutorial `index.md`. Adding a "Previous: Quick Start" link at the top or bottom would make the tutorial navigable in both directions.

### Verified links (pass)

- `../concepts/stations.md` — file exists.
- `../reference/models.md` — file exists.
- `../reference/litmus-fixtures.md` — file exists (anchor broken, see above).
- `../how-to/traceability.md` — file exists.
- `../concepts/products.md` — file exists.
- `../concepts/fixtures.md` — file exists.
- `02-mock-instruments.md` — file exists.
- `https://github.com/pragmatest-dev/litmus.git` (Prerequisites clone URL) — matches `pyproject.toml`.
- `https://pyvisa.readthedocs.io/` — external, not verified at runtime.
- `https://pymeasure.readthedocs.io/` — external, not verified at runtime.
