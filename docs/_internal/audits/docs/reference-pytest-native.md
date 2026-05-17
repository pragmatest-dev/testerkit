# Page audit: docs/reference/pytest-native.md

**Quadrant:** Reference (how the bundled pytest plugin uses pytest's own collection / fixtures / markers / flags)
**Audited:** 2026-05-17

**Note:** The audit-coordinator's Agent/Task dispatch tool was not available in this environment, so all six audit dimensions were performed inline by reading the page, related docs, and the implementing source (`src/litmus/pytest_plugin/`). Findings below follow the same structure each agent would have produced.

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 1 | 2 |
| Accuracy | 0 | 3 | 2 |
| Gaps | 0 | 3 | 3 |
| Cross-links | 0 | 1 | 3 |
| **Total** | **0** | **10** | **14** |

---

## Ordering

### WARNING — "Discovery vs activation" buried after Plugins section

Lines 102–109 introduce the **fundamental mental model** ("Discovery is pytest's; Activation is Litmus's") that frames the whole rest of the page. It's currently the second-to-last section, after a third-party-plugin compatibility matrix.

For a reader landing on this page from `reference/index.md` ("pytest-native — the baseline every other page builds on"), the activation/discovery split is the *first* idea they need to anchor every later section to. Putting it last means the Collection / Fixtures / Markers / CLI sections all describe halves of a split the reader hasn't been shown yet.

**Suggested fix:** Promote the "Discovery vs activation" callout to the second section, immediately after the lead paragraph and before "Collection". The rest of the page then reads as elaboration of that split.

### SUGGESTION — Command-line flags section mixes two unequal tables

Lines 64–91 ship one "pytest native flags that matter" table and one "Litmus flags" table back to back. The native-flag table is general pytest knowledge (`-k`, `-m`, `--lf`, `--tb=…`) that a hardware engineer might not know, while the Litmus-flag table is the actually load-bearing content for activation. The current ordering reads as if pytest's flags are primary and Litmus's are an addendum, which inverts the page's purpose.

Consider leading with the Litmus flag table (the new surface) and demoting the pytest-flag table to a "useful pytest flags for hardware work" callout below it.

### SUGGESTION — Sidecar recursion paragraph (line 21) belongs in Test configuration, not here

The sidecar scoping description (`tests: { ClassName: { ... } }`, recursive nesting) is *test configuration* schema, not a pytest-collection topic. It interrupts the Collection narrative with sidecar YAML structure that the very next sentence ("See Test configuration") admits belongs elsewhere. Consider dropping the body of line 21 and keeping only the cross-link.

---

## Voice

### WARNING — "Litmus has no opinion here" (line 106) breaks the reference register

Reference pages should be neutral and authoritative. "Litmus has no opinion" is a marketing/conversational flourish that contrasts oddly with the otherwise factual register of the page. Replace with a factual statement: "Discovery uses pytest's defaults unchanged."

### SUGGESTION — Editorial sentence in pytest-xdist row (line 98)

"Generally **not** appropriate for hardware tests on a single bench (instruments aren't reentrant). Fine for mock-only suites and CI lint passes."

This is the strongest editorial in the page — a value judgement on a third-party plugin. In a *reference*, it should be either a factual constraint ("Instruments are not reentrant; running xdist on real hardware will conflict for shared resources.") or moved to a how-to / explanation page. The current phrasing ("generally not appropriate") leaves the reader without a definitive rule.

### SUGGESTION — "the seven `@pytest.mark.litmus_*` markers" / "20 fixtures" counts in prose

Lines 34 and 49 cite counts ("20 fixtures", "seven markers"). Reference pages that cite counts pin themselves to a release: any addition of a fixture or marker silently breaks accuracy here. Prefer "the fixtures the Litmus plugin contributes (see [Litmus fixtures])" and "the `@pytest.mark.litmus_*` markers Litmus adds" — the linked pages already own the canonical list. (Note: the counts are accurate as of audit — confirmed 20 fixture sections in `litmus-fixtures.md` and 7 `litmus_*` markers registered in `pytest_plugin/markers.py`.)

---

## Audience

### WARNING — Lead paragraph mixes audience signals

Lines 3 starts with "Litmus is a hardware test **platform**; pytest is its primary test-runner integration." That's positioning for someone who doesn't know what Litmus is — a tutorial / index reader. The very next sentence drops into pytest jargon ("collection, fixtures, markers, plugins, `conftest.py`, command-line flags") that assumes deep pytest familiarity. The page can't be both an introduction and a reference for an expert; reference pages should commit to "you already know pytest; here's what's different."

**Suggested fix:** Trim the platform-positioning sentence (it's already in the docs lead and `reference/index.md`). Open with the actual reference promise: "This page maps pytest's native surface onto what the Litmus plugin assumes, replaces, or adds."

### SUGGESTION — "What pytest gives you natively" framing

The page is titled "pytest-native reference" but spends as much (or more) ink on what Litmus adds. The audience signal in the title is "pytest concepts unchanged"; in practice the page is "pytest concepts ± Litmus deltas." Consider either renaming the page to "pytest integration reference" or restructuring so each pytest concept has a clear "native:" / "Litmus adds:" split (currently mixed inline).

### SUGGESTION — Conceptual phrases that need a glossary anchor

Terms used as if defined elsewhere but never linked at first use on this page:

- "spec lookup" (line 86 — `--product` description)
- "pin → instrument routing" (line 87 — `--fixture` description)
- "in_* columns" (line 51)
- "spec-derived limits" (line 90)

A test engineer landing on this page from a search result may bounce when these phrases appear without a one-click definition. Either link them on first use or add a "Vocabulary" callout at the top.

---

## Accuracy

(All claims cross-checked against `src/litmus/pytest_plugin/__init__.py`, `hooks.py`, `markers.py`, `retry.py`, and `src/litmus/execution/sidecar.py`.)

### WARNING — Sidecar path notation is misleading

Line 18: "merges per-test sidecar YAML (`tests/test_<module>.yaml`)"

The actual resolution in `src/litmus/execution/sidecar.py:55` is `yaml_path = module_file.with_suffix(".yaml")`. For a module at `tests/test_foo.py`, the sidecar is `tests/test_foo.yaml` — the file *replaces* the `.py` suffix.

The doc's notation `tests/test_<module>.yaml` reads as if `<module>` is a placeholder you fill in. That's correct only if `<module>` = "foo" — but then the prefix `test_` is already part of the filename and the placeholder is `<module>` is awkward. A reader could easily think the sidecar is at e.g. `tests/test_widget.yaml` for `tests/widget.py`, which is wrong (pytest doesn't collect `widget.py` by default either, but the docs example would still mislead).

**Suggested fix:** Write it as "alongside the test module — for `tests/test_foo.py` the sidecar is `tests/test_foo.yaml`." Concrete is better than placeholdered.

### WARNING — Sidecar scoping description (line 21) doesn't match the schema

Line 21: "top-level keys apply to every test in the file; `tests: { ClassName: { ... } }` scopes per class; `tests: { ClassName: { tests: { test_method: { ... } } } }` scopes per method."

Verified against `SidecarConfig` / `TestEntry` in `src/litmus/models/test_config.py` — the recursive `tests:` tree is correct.

But this paragraph contradicts itself with "is recursive" (true) and then shows two specific shapes as if they were the only legal forms. The actual model allows arbitrary nesting (e.g., a function-level entry can itself contain `tests:` for parametrize cases). Either explicitly say "the same shape applies at every level" or link to the canonical schema and stop sketching it inline.

### WARNING — "no custom collectors, no replacement of `pytest_collect_file`" (line 7)

Verified: the plugin's `__all__` (lines 117–132 of `pytest_plugin/__init__.py`) does not include `pytest_collect_file` and `hooks.py` does not implement one. ACCURATE.

However the plugin **does** implement `pytest_generate_tests` (line 98 of `pytest_plugin/__init__.py` imports it from `hooks.py`), which is a collection-time hook that drives Litmus-side parametrize expansion (`vectors:`, `litmus_sweeps`, profile overrides). The doc says "No custom collectors, no replacement of `pytest_collect_file`" but is silent on `pytest_generate_tests`, which is a substantive Litmus contribution to collection. Either mention it in the bullet list below ("What Litmus adds at collection time") or revise the negative-statement framing — currently a reader could think collection is "vanilla pytest plus a modify-items pass," which understates what the plugin does.

### SUGGESTION — `pytest_collection_modifyitems` description is half right

Line 18: the hook "merges per-test sidecar YAML into each item's marker set. This expands `litmus_sweeps` into one pytest case per row exactly as if you had written `@pytest.mark.parametrize` — pytest still owns the case multiplication."

Source check: `pytest_collection_modifyitems` (hooks.py:406) does merge cascade markers and reorder class-sweep items, but the **case multiplication** for `litmus_sweeps` happens in `pytest_generate_tests` (hooks.py:1472, the "Pytest adapter — load sidecar / profile, delegate to runner-neutral parametrize calls"), NOT in `modifyitems`. The doc conflates two hooks. The visible-to-user effect is correct ("one pytest case per row"); the mechanism attribution isn't.

### SUGGESTION — `--no-test-profile` claim about pairing

Line 88: "Pair with `--no-test-profile` to disable a `default_profile:` set in `litmus.yaml`."

Verified against `pytest_addoption` (hooks.py:992): the flag exists and matches this description. ACCURATE — listed here only for the record.

---

## Gaps

### WARNING — `pytest_generate_tests` is unmentioned

(See Accuracy CRITICAL above.) The plugin contributes a `pytest_generate_tests` hook — the single most consequential collection-time hook the plugin adds (turns `litmus_sweeps` / sidecar `vectors:` / profile overrides into one pytest case per row). The page's "What Litmus adds at collection time" bullet list (lines 17–19) currently lists only `pytest_collection_modifyitems`. This is the headline omission for a reference titled "pytest-native."

### WARNING — `--strict-traceability` is missing from the flag table

The Litmus flag list (lines 82–91) is described as "the flags that matter most" with a pointer to CLI reference for the full set. That's defensible — but `--strict-traceability` (registered at hooks.py:979) materially changes test-pass semantics (failures on missing traceability fields). For a reference page, a flag that can flip pass to fail is more important than `--guardband` (which is listed). Either include it or explicitly call out that the list is curated.

### WARNING — No mention of pytest entry-point plugin registration

Line 62 says "The Litmus plugin loads via the standard pytest entry-point mechanism — no `conftest.py` manipulation needed."

That's a useful sentence but it's buried mid-section. For someone debugging "why aren't my Litmus fixtures available?" it should be more prominent — e.g., a sentence in the lead paragraph or a dedicated "Installation / Activation" subsection. Currently the page doesn't anywhere state "install the litmus package and the plugin is automatically active" — readers have to infer.

### SUGGESTION — Worker mode / multi-slot is invisible

The plugin contributes worker-mode fixtures (`sync`, slot-aware `fixture_config`, env-driven `_LITMUS_SLOT_ID`) and CLI flags (`--slot`, `--dut-serials`). None of this surfaces on the page that promises to be the map of "what pytest gives you natively and what the plugin layers on top." A test engineer reading this page would not learn that multi-DUT runs exist as a first-class pytest concern. Even a one-sentence pointer to a worker-mode reference would help.

### SUGGESTION — `pytest_assertion_pass` is unmentioned

The plugin registers `pytest_assertion_pass` (hooks.py imports, `__all__` line 119). Litmus uses it to record passing assertions in the event log — a real behavioral difference from vanilla pytest. Worth a one-line note in either Collection or a new "Reporting" section.

### SUGGESTION — `pytest_report_header` is unmentioned

The plugin contributes a `pytest_report_header` (hooks.py imports, `__all__` line 124) that adds the active station / product / fixture / profile to pytest's startup banner. That's the first thing a user sees in their terminal output and it's not documented here. A line in the "What the plugin adds" section would close the loop.

---

## Cross-links

### WARNING — `[CLI reference](cli.md)` link target lacks an anchor

Line 80: "Litmus adds the following flags (see [CLI reference](cli.md) for the full set)"

The CLI reference (`docs/reference/cli.md`) is a long page covering `litmus serve`, `litmus runs`, etc. — most of which are *not* pytest CLI flags. A reader clicking that link to find more pytest-flags will land on a page whose top sections (Installation, Commands) are unrelated. Either link to a specific anchor that lists pytest plugin flags, or add a "pytest flags" section to cli.md and anchor here.

(Verified: cli.md exists with sections Installation, Commands, Yield/Manufacturing Metrics, Data management, Daemon, MCP, Setup, Getting Started, Common Workflows, Test phase, Environment Variables, Exit Codes, See Also. None of those anchors is "pytest-plugin flags." This is a real linking gap.)

### SUGGESTION — "no-stacking rule" anchor verified

Line 49: `[no-stacking rule](litmus-markers.md#no-stacking-rule)` — verified resolves to `## No-stacking rule` in `docs/reference/litmus-markers.md:7`. ACCURATE.

### SUGGESTION — "Test configuration" anchor verified

Line 21 and line 115: `[Test configuration](configuration.md#test-configuration)` — verified resolves to `## Test Configuration` in `docs/reference/configuration.md:187`. ACCURATE.

### SUGGESTION — "Integrations" link goes to a top-level index, not specific runners

Line 3: "Other runner integrations (OpenHTF, LabVIEW / TestStand via the results API) live under [Integrations](../integration/index.md)."

The link lands on `docs/integration/index.md` which has three sections (Start with results, Move test execution onto Litmus, Hardware and data). For a reader following the breadcrumb specifically because they want OpenHTF or results-API, the index page is one hop too shallow. Consider linking directly to `integration/openhtf-adapter.md` and `integration/results-api.md` as parenthetical references, with the index as a roll-up at the end of the page.

---

# Notes on dispatch

The audit-coordinator agent was invoked but the harness exposed only Read / Write / Bash tools — the parallel Agent/Task dispatch path was not available. All six dimensions were performed inline against the page and against the implementing source. If the original parallel-agent flow is required for traceability, re-run with the Agent tool enabled — the source-code claims in the Accuracy section are the ones most worth a second pair of eyes, since the audit-accuracy agent's instructions presumably include a more thorough whole-codebase grep than was performed here.
