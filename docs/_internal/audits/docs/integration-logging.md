# Page audit: docs/integration/logging.md

**Quadrant:** Integration (Explanation/Reference) — sending Litmus results to external systems
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 2 | 3 |
| Audience | 1 | 2 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 1 | 3 | 3 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **6** | **16** | **14** |

---

## Ordering

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration (Explanation/Reference) — logging from non-pytest code into Litmus

### ⚠️ WARNING — "Logging Approaches" mixes three orthogonal axes under one numbered list
Approach 1 ("Explicit Logging"), Approach 2 ("try / finally for cleanup on errors") and Approach 3 ("Decorator Pattern") are not three alternative ways to log. They are: (1) a minimal happy-path example, (2) a cross-cutting error-handling refinement that applies to all three, (3) a syntactic-sugar wrapper around (2). Numbering them as peers implies the reader picks one. The reader should adopt try/finally always; the decorator is then an optional packaging choice. Recommend re-ordering as: "Minimal example" → "Cleanup on errors (always do this)" → "Optional: decorator wrapper".

### ⚠️ WARNING — "Performance Considerations" is buried under repeat-sample guidance only
The H2 promises performance considerations broadly, but the only content is one paragraph about `allow_repeat=True` and a one-line pointer to the channel store. A reader looking for "is `client.start_run()` blocking? Is `save_test_run` synchronous? What's the per-measurement cost?" finds nothing. Either expand to actually cover write semantics, batching, and lock behaviour, or rename the section to "Streaming sample series" so the H2 doesn't over-promise.

### 💡 SUGGESTION — "Data Storage" lands before the reader needs it
The page goes Quick Start → Approaches → Data Storage → Querying → Metadata → Patterns. A reader integrating with an existing system wants: (1) "how do I write?", (2) "where does it land?", (3) "how do I get it back out?". "Metadata" — which extends the write step — interrupts the read flow by sitting between Querying and Patterns. Consider moving Metadata up adjacent to the Approaches section (it's a write-side concern) and keeping Storage → Querying contiguous.

### 💡 SUGGESTION — "Best Practices" floats at the end with no anchor
The five-item best-practices list at the bottom restates guidance already implied in the examples (use metadata, abort on failure, etc.) and adds one new item ("Don't block on logging — Use async for high-speed tests") which is never elaborated anywhere on the page. Either fold the bullets back into the section they belong to (metadata → Metadata section, abort → Approach 2) or drop the list. Generic best-practices sections at page bottom tend to be unread.

---

## Voice

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration

### ⚠️ WARNING — Title case headings break the docs convention
Almost every H2 and H3 uses Title Case ("Quick Start", "Logging Approaches", "Explicit Logging", "Data Storage", "Default Location", "Custom Location", "Environment Variable", "Querying Results", "Run Metadata", "Custom Metadata", "Measurement Metadata", "Integration Patterns", "With Logging Framework", "With Database", "With Cloud Storage", "Performance Considerations", "Best Practices", "Next Steps"). Other pages in the same folder (e.g. `integration/index.md`) and the broader docs prefer sentence case. Convert to sentence case throughout.

### ⚠️ WARNING — "Approach 1 / 2 / 3" labelling sounds like a textbook chapter, not project docs
The numbered "Approach 1: Explicit Logging" / "Approach 2: try / finally for cleanup on errors" / "Approach 3: Decorator Pattern" framing reads as a survey of options rather than a directive. Litmus docs elsewhere lead with the recommended path and present alternatives only when they actually serve a distinct need. Drop the "Approach N" numbering and replace with intent-led headings ("Minimal example", "Always wrap in try / finally", "Decorator wrapper").

### 💡 SUGGESTION — "Litmus provides:" stock-list opener is generic
The Overview opens with "Litmus provides:" followed by a four-item bullet list (structured logging, run tracking, query API, export). Same opener pattern as a half-dozen other pages. Replace with one sentence that names what this page in particular adds over `results-api.md` ("the integration patterns here cover capturing measurements alongside test code that already exists — wrapping, decorating, or threading a client through your runner").

### 💡 SUGGESTION — "Just key measurements" / "high-speed tests" are vague
Best Practices item 1 says "Not every variable, just key measurements" without defining "key", and item 5 says "Use async for high-speed tests" without defining "high-speed" or pointing at any async API on `LitmusClient` (there isn't one — see Accuracy). Either give a concrete cut-off (e.g. "below a few hundred samples per step is comfortable; above that, stream as a sample series or push to the channel store") or remove.

### 💡 SUGGESTION — Code comment "# Your existing test code" is filler
In the Approach 1 snippet the comment `# Your existing test code` adds nothing the next two lines don't show. The comment `# Log to Litmus` is also redundant once `with run.step(...)` is on the next line. Trim both — the integration story is clearer when the snippet is just code.

---

## Audience

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration — readers running non-pytest code that wants to deposit results in Litmus

### ❌ CRITICAL — Page conflates "logging" with "results submission" and overlaps `results-api.md` without disambiguating
The page calls itself "Logging Integration" but every example uses `LitmusClient`, which is the results-submission API documented one click away at `integration/results-api.md`. The Overview, Quick Start, and three "Approaches" all overlap that page. A reader landing here from `integration/index.md` (which advertises this page as "patterns for capturing measurements alongside existing test code") cannot tell whether they should read this page, `results-api.md`, or both — and the two pages quote the same `LitmusClient` constructor and the same `start_run` signature. Either:
- (a) make this page strictly about patterns (decorator, error-handling wrapper, logging-framework bridge) and remove the duplicated API surface, deferring to `results-api.md`, or
- (b) merge into `results-api.md`.
As written, the reader's audience question ("which page is mine?") is never answered.

### ⚠️ WARNING — Title implies Python `logging` module, but only one sub-section actually addresses it
"Logging Integration" reads to a Python developer as "how to wire Litmus into `logging.getLogger()`". The bulk of the page is about manual measurement capture; only the "With Logging Framework" snippet (which itself only sketches a `LitmusHandler`) addresses Python's `logging`. Rename to "Capturing results from existing test code" or split the `logging.Handler` bridge into its own how-to.

### ⚠️ WARNING — "Custom Metadata" section assumes pytest knowledge in a non-pytest page
Under Custom Metadata: "For custom columns, use the `run_context` fixture inside pytest-native tests (see [Writing tests](../how-to/writing-tests.md))." This page's stated audience is people *not* on the pytest runner ("integrate Litmus logging without changing your test framework"). Telling them their only option is a pytest fixture closes the door without offering an alternative. Either give a non-pytest path (e.g. is there a `RunBuilder` method? Spoiler: no — see Accuracy and Gaps) or be explicit that custom columns are pytest-runner-only at this time.

### 💡 SUGGESTION — `pytest tests/ --data-dir=...` in a non-pytest page
The "Custom Location" example for a page about non-pytest integration is `pytest tests/ --data-dir=/path/to/results`. A LabVIEW or TestStand operator who arrived here will be confused by the pytest invocation. Show the equivalent `LitmusClient(data_dir=...)` constructor argument (which the code supports — see Accuracy) and demote the pytest-CLI example or point at the pytest-existing page.

### 💡 SUGGESTION — Skill-level shift mid-page (decorator pattern assumes Python intermediate; Quick Start is beginner)
Quick Start is appropriate for a first-timer. The decorator example uses `functools.wraps`, signature `(dut_serial, *args, **kwargs)` re-projection, and inner-closure mutation patterns that assume Python intermediate fluency. If the audience is "any test engineer", consider leading with the simpler patterns and gating the decorator section behind "For Python developers comfortable with decorators".

---

## Accuracy

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration

Source of truth: `src/litmus/client.py`, `src/litmus/data/models.py`, `src/litmus/data/schemas.py`, `src/litmus/data/backends/parquet.py`, `src/litmus/store.py`, `src/litmus/data/data_dir.py`.

### ❌ CRITICAL — `step.measure(... dut_pin=..., instrument_channel=...)` is shown but `StepBuilder.measure` does NOT accept those kwargs
Page line 222-231 shows:
```python
step.measure(
    name="voltage",
    value=3.31,
    units="V",
    low=3.0,
    high=3.6,
    spec_ref="SPEC-001",
    dut_pin="J1.3",
    instrument_channel="CH1",
)
```
`src/litmus/client.py:189-233` (`StepBuilder.measure`) and `src/litmus/client.py:75-133` (`VectorBuilder.measure`) accept exactly `name, value, units, low, high, nominal, comparator, spec_ref`. There is no `dut_pin` or `instrument_channel` kwarg. The fields exist on the underlying `Measurement` model and parquet schema, but `StepBuilder.measure` does not surface them. As written, this snippet raises `TypeError: measure() got an unexpected keyword argument 'dut_pin'`.

### ❌ CRITICAL — `RunBuilder.abort()` does not save, yet `run.abort(str(e))` is presented as the cleanup-on-error pattern
Page line 84 shows `run.abort(str(e))` in the Approach 2 try/finally. `src/litmus/client.py:329-340` shows `abort()` sets `outcome = ABORTED` and returns the run but **never calls `self._client._backend.save_test_run(...)`**. So the page's error-handling pattern silently discards any measurements recorded before the exception. That contradicts the surrounding prose ("ensure `finish()` runs even on error") and the bullet "Handle errors gracefully — Abort runs on failure" in Best Practices. Either: change the example to `run.finish()` (which records the ABORTED outcome AND persists), or fix `abort()` to persist, or document the discard behaviour loudly. As-is the page teaches the reader to lose data.

### ❌ CRITICAL — `allow_repeat=True` is not a kwarg of `LitmusClient`'s `step.measure`
Page line 313-318 shows:
```python
for i, value in enumerate(values):
    step.measure("voltage_sample", value, allow_repeat=True)
```
`allow_repeat` is a parameter on `litmus.execution.logger.MeasurementLogger.measure` (`src/litmus/execution/logger.py:948`). It is **not** on the `LitmusClient` `StepBuilder.measure` shown everywhere else on this page. As written, this snippet raises `TypeError`. The whole `LitmusClient` path also has no duplicate-name guard, so the framing ("so the duplicate-name guard doesn't fire") is itself inaccurate for this API.

### ⚠️ WARNING — "Custom Metadata" claim "Anything beyond those is rejected" is overstated
Page line 217: "`LitmusClient.start_run()` accepts the fields listed above. Anything beyond those is rejected — the surface is intentionally narrow." `src/litmus/client.py:368-405` shows `start_run` is a regular Python function — passing an unknown kwarg raises `TypeError: start_run() got an unexpected keyword argument 'foo'`. That's "Python rejects it", not "Litmus rejects it as policy". More importantly, `TestRun` (`src/litmus/data/models.py:385`) carries `custom_metadata: dict[str, Any]` — so custom fields *are* supported by the data model; `start_run` simply doesn't expose them. The page should say "`start_run` does not currently surface custom metadata; the data model supports a `custom_metadata` dict but the client doesn't pipe it through yet" — or just expose it.

### ⚠️ WARNING — Default storage layout / location claim is partially wrong
Page line 134-142 describes "Default Location" as `results/runs/{date}/...` and the "Custom Location" section as `LitmusClient(data_dir="results")` (implicit in the Quick Start) + `LITMUS_HOME` env var. The default for `LitmusClient(data_dir="results")` is shown literally on `src/litmus/client.py:360` — but that's a relative path used only when the client is constructed with no args. Across the rest of Litmus, the canonical project storage resolver (`src/litmus/data/data_dir.py`) walks `litmus.yaml` → `LITMUS_HOME` → `platformdirs.user_data_dir("litmus")`. So the answers to "where do my runs land?" differ depending on whether the user uses `LitmusClient()` (CWD-relative `results/`) or pytest-native or the harness (project resolver). The page does not warn about this inconsistency, and it explains `LITMUS_HOME` as a way to "set" the location for a `LitmusClient(data_dir=...)` call when in fact `LitmusClient` does not consult `LITMUS_HOME` at all (it only reads its constructor arg, defaulting to `"results"`).

### ⚠️ WARNING — Sample query column `in_vin` is shown without explanation, and `step_name` filter is hardcoded
Page line 170: `vout = df[df["step_name"] == "test_output_voltage"]` and `print(vout[["measurement_value", "measurement_outcome", "in_vin"]])`. Nothing earlier on this page produces a step named `test_output_voltage` or a vector parameter named `in_vin` (the page's examples use `step.measure(...)` directly, never `step.vector(in_vin=...)`). The reader cannot reproduce this query from any earlier snippet. Either extend an earlier example to record a `vin` vector param, or rewrite the query to use the columns the earlier snippets produce.

### ⚠️ WARNING — The pytest `--data-dir=` flag is shown in a `LitmusClient` page
Page line 148-150 shows `pytest tests/ --data-dir=/path/to/results` under "Custom Location" of a page whose entire premise is using `LitmusClient` from non-pytest code. The flag exists in the pytest plugin (`src/litmus/pytest_plugin/hooks.py:281, 934`) but is unrelated to `LitmusClient`. Replace with the constructor argument: `LitmusClient(data_dir="/path/to/results")`.

### 💡 SUGGESTION — `get_measurements` return type description in prose contradicts source
Page line 277-282 narrates "measurements is list[dict] keyed by parquet column names" and uses `m['measurement_name']`, `m['measurement_value']`. Source (`src/litmus/client.py:429-438`) does return `list[dict]`, so this is accurate today — but compare to the `run` value above where the page correctly says "RunSummary Pydantic model — use attribute access". The asymmetry (one model, one dict) is real but it's a sharp edge worth calling out for the reader rather than burying mid-snippet.

### 💡 SUGGESTION — `run.id` is a `UUID`, page does not say so
The page calls `client.get_run(run_id)` and `client.get_measurements(run_id)` with a string `run_id` parameter, but `RunBuilder.id` (`src/litmus/client.py:292-295`) returns a `uuid.UUID` object. Page never explains the conversion. A reader doing `client.get_run(run.id)` directly will trip over `str(uuid)` vs `uuid` (the backend accepts partial strings ≥ 8 chars per `client.py:422`).

---

## Gaps

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration

### ❌ CRITICAL — No mention of `step.vector(**params)` despite vectors being central to the parquet schema and to the query example
The page introduces `run`, `step`, `step.measure`, `step.fail`, `step.skip`, `run.finish`, `run.abort`, but never mentions `step.vector(**params)` (defined `src/litmus/client.py:165-187`). Vectors are how you create the `in_*` columns the page itself queries (`vout[["measurement_value", "measurement_outcome", "in_vin"]]`). Without `step.vector(in_vin=...)`, the reader cannot produce `in_vin` columns. This is the biggest single missing API on the page.

### ⚠️ WARNING — No guidance on multi-process / concurrent writes
A common integration scenario for "external test runner posts results" is two processes on the same bench writing simultaneously (e.g. parallel station threads, or a long-running daemon alongside a one-shot script). The page is silent on whether `LitmusClient` is safe for concurrent use, whether each process should construct its own `LitmusClient`, whether file locks exist, and what happens if two runs share a `data_dir`. Source side-steps this (the backend writes one parquet per run keyed by `run_id`, so collisions are unlikely), but the page never says so.

### ⚠️ WARNING — `run_context` fixture pointed at, but no equivalent non-pytest path
Under "Custom Metadata" the page sends non-pytest users to a pytest-only fixture. The page never explains:
- Whether `TestRun.custom_metadata` is reachable via `RunBuilder` (it isn't, today — see Accuracy).
- Whether a future `run.set_context(key, value)` is planned.
- Whether the workaround is to write directly to `run._test_run.custom_metadata` (private, brittle).

### ⚠️ WARNING — No coverage of what "abort" actually means for the operator UI
The page presents `run.abort(message)` as a normal flow, but never says whether the aborted run shows up in `litmus runs`, the operator UI, or downstream queries. Given that abort doesn't persist (see Accuracy), the answer is "no" — and that's a behavioural detail every integrator needs to know.

### 💡 SUGGESTION — Channel store / `out_*` reference pattern is name-dropped but not shown
"For very large samples (waveforms, scope captures), prefer the channel store and emit a single `out_*` reference" — but the page never shows how to emit an `out_*` reference. The pointer to `how-to/querying-channels.md` covers the read side. There's no write-side recipe ("how do I, from `LitmusClient`, push a waveform and get a reference column?"). Either show a 4-line example, or be explicit that this requires the pytest plugin / harness path.

### 💡 SUGGESTION — No worked LabVIEW / TestStand example
The page is the central "integrate Litmus into existing test infrastructure" page, but every snippet is pure Python. `integration/results-api.md` has a "From LabVIEW" section. This page could link to it or include a one-paragraph "language bridge" callout.

### 💡 SUGGESTION — No troubleshooting / verification step
Nothing on the page tells the integrator how to verify their first run landed. A single closing snippet — `client.list_runs(limit=1)` plus a CLI invocation — would close the loop and let a new user prove the integration before moving to volume.

---

## Cross-links

**Page:** `docs/integration/logging.md`
**Quadrant:** Integration

### ❌ CRITICAL — Page does not link to `integration/results-api.md` until the very last "Next Steps" line, despite enormous content overlap
`integration/index.md` (the parent index) lists `results-api.md` and this page as peers covering the same `LitmusClient` API surface. The reader landing on `logging.md` should be told up front "if you need the `LitmusClient` API reference, see [results-api](results-api.md); this page focuses on integration patterns". Right now the two pages share examples and constructors but neither references the other except as a "next step" footnote — which sends readers in a loop.

### ⚠️ WARNING — Forward link to `how-to/writing-tests.md` is the *only* way a reader learns about `run_context`
Under Custom Metadata the page says "use the `run_context` fixture inside pytest-native tests (see [Writing tests](../how-to/writing-tests.md))". That page (`docs/how-to/writing-tests.md`) exists, but the reader is on an Integration page specifically because they aren't using pytest. The cross-link is correct but the framing is wrong — see Audience finding.

### ⚠️ WARNING — `how-to/querying-channels.md` link is the only pointer to the channel-store concept, no concept page link
"prefer the channel store and emit a single `out_*` reference (see [Querying channels](../how-to/querying-channels.md))" points readers at the *query* side of channels, not at any explanation of what the channel store *is*. If there's a concept page on the channel store, link to it; if there isn't, the page should say one sentence about it.

### ⚠️ WARNING — No link to parquet schema reference for the column names the page mentions
The page mentions `step_name`, `measurement_value`, `measurement_outcome`, `in_*`, `out_*` columns and shows pandas / DuckDB queries against them. `docs/reference/parquet-schema.md` exists and is the authoritative column listing. No link to it from anywhere on this page. Add a single pointer near the Querying Results H2.

### 💡 SUGGESTION — Best Practices links nothing
The five-item Best Practices list says "Use consistent naming — Same measurement names across tests" without pointing at the naming-convention page (if there is one) or `litmus-markers.md` / `litmus-fixtures.md`. Each best-practice bullet that has a more concrete page behind it should link to that page.

### 💡 SUGGESTION — Next Steps could be more directional
The three Next Steps links (`results-api.md`, `harness.md`, `client.md`) are presented as equal options. Order them by likely need: most readers will want the formal API surface (`reference/client.md`), then the more idiomatic non-pytest entry (`harness.md`) before the overlapping `results-api.md`. Or split into "Reference" vs "Other integration paths".
