# Page audit: docs/concepts/step-manifest.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 1 | 3 | 2 |
| Audience | 0 | 3 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 3 |
| **Total** | **6** | **19** | **13** |

---

## Ordering

**WARNING — H1 title doesn't match the file's claim or the index entry.**
The page is filed at `concepts/step-manifest.md`, the index lists it as **"Step manifest — what each step records"**, and the parent task framing is "step manifest — what gets discovered vs run, not_started entries". But the H1 reads **"Step Results & StepsDiscovered"**, which surfaces the wire-event class as a co-equal subject. A Concepts page about the manifest should lead with the manifest concept; the event class is one implementation detail. Suggested H1: `# The Step Manifest — every planned step, with a row for the ones that never ran`.

**WARNING — "Storage" section interrupts the conceptual flow.**
The current order is: Problem → StepsDiscovered event → How it flows → **Storage** → "Never ran" rows → Querying. The "Storage" block is a quick reference table of step columns from the Parquet schema — it's mechanical, and inserting it between the data-flow diagram and the conceptual "never ran" payoff fragments the explanation. Better order for a Concepts page: Problem → Manifest concept (what it is) → "Never ran" rows (the payoff) → How it flows (event + reconciliation) → Where it lands (Storage) → Querying. The conceptual "what is this and why does it matter" should land before the implementation-detail column list.

**SUGGESTION — `## How it flows` and `## Storage` are both essentially "where the rows go" — consider merging or sequencing them together.**
The flowchart ends at the parquet file; the next section describes the parquet file. Either combine into a single `## From event to row` section, or rename one so the boundary is clearer (e.g., `## How the manifest is built` for the flow, `## Parquet layout` for the storage).

**SUGGESTION — Querying section mixes three query surfaces (DuckDB, RunStore, event store) without ordering rationale.**
Best-to-worst for the typical operator/test-engineer reader is probably: RunStore (the Python public API) → DuckDB (the analytics path) → event store (the diagnostic path). Today the page opens with raw SQL, which signals "analyst" not "test engineer". Reorder unless the page is explicitly aimed at analysts.

---

## Voice

**CRITICAL — Title and lead conflate "Step Results" with "Step Manifest" — and "Step Results" isn't a project term.**
The H1 says "Step Results & StepsDiscovered". Neither phrase appears as a defined concept in the codebase or other docs. The codebase term is **step manifest** (`build_step_manifest`, `_append_not_started`, "Build step manifest entries…" in `_row_helpers.py:768`); the index entry is "Step manifest"; the source-code docstring on `StepsDiscovered` says "build a complete step manifest". The page should commit to "step manifest" as the term-of-art and use it from the title through the body. Right now the page introduces "step results" (line 3), then the conceptual term never returns. This is a naming-discipline issue, not just style.

**WARNING — Tone slips into reference register in the "StepsDiscovered event" section.**
Lines 19-35 read like an API reference: a Pydantic class block plus a field-name table. Concepts pages should explain *why* the manifest looks like this, not enumerate fields. Move the class block + table to `reference/event-types.md` (if it isn't already there) and replace with a one-paragraph description: "When pytest finishes collection, the plugin captures every collected item as a `CollectedItem` and ships them inside a `StepsDiscovered` event. The carried payload is the pytest identity for each item — node id, file, module, class, function — enough to reconstruct a 'this was planned' row later." Keep this page's voice **explanatory**, not catalog.

**WARNING — "There is one parquet file per run" sentence is louder than the surrounding prose for no reason.**
Line 51 bolds **one parquet file per run** mid-sentence. Bolding is fine for the first introduction of a load-bearing fact, but the bolded chunk here is "one parquet file per run", which is just restating the file-naming convention from the diagram above and from `parquet-schema.md`. Either drop the bold or move this to a callout. Concepts pages should reserve emphasis for genuinely-novel claims (the "Never ran" semantic, for instance).

**WARNING — Inconsistent terminology: "step results", "step manifest", "step records", "planned step", "synthetic rows".**
Same artefact, five names in 75 lines:
- Line 3: "Step results give a complete view of every planned test step"
- Line 6: "Without explicit step records…"
- Line 47: "synthetic rows with `step_outcome IS NULL`"
- Line 67: "Never ran" rows
- Source code: "step manifest"

Pick one (recommended: **manifest entry** for the conceptual row, **`not_started` entry** for the never-ran subset — matching `_append_not_started` and the StepsDiscovered docstring), and use it consistently.

**SUGGESTION — Drop the inline "(early abort, `--maxfail`, skip markers)" parenthetical in the lead.**
Lead sentence is doing two jobs (define the artefact + list the trigger conditions). Split: "Every pytest-collected item lands in the manifest, even the ones that never executed. A row exists for each — with a real outcome for executed steps, and a NULL outcome for items the run skipped, aborted past, or short-circuited via `--maxfail`."

**SUGGESTION — "That matters for:" is a tired phrasing.**
Concepts pages should explain causation, not market-deck the value prop. Try: "Three things break without it: yield numbers (3/3 looks like a clean run, 3/10 doesn't), coverage tracking (which steps haven't run on this product lately?), and audit trails (regulators want the test plan, not just what passed)."

---

## Audience

**WARNING — Page assumes the reader already understands `record_type`, `step_path`, `vector_index`, `step_outcome IS NULL`, "claim-check", "Arrow IPC", `RunEnded`.**
For a Concepts page these terms either need a one-line gloss on first use or a link out. Today:
- `record_type` shows up at line 44 and 51 with no introduction — the reader has to click through to `parquet-schema.md`.
- `vector_index` appears at line 57 with no gloss — link to `step-hierarchy.md`.
- `RunEnded` (line 69) and `StepStarted` (line 47) are used as if known — link to `event-log.md` or `event-types.md`.
- "Arrow IPC" (in the docstring excerpt) is jargon.

A Concepts reader can be a test engineer 30 minutes into Litmus. Don't assume they've memorized the parquet column list.

**WARNING — Pydantic class snippet is for framework developers, not test engineers.**
Lines 20-23 show a `class StepsDiscovered(EventBase): ...` definition with `Literal[...]`, `Field(default_factory=...)`. A test engineer reading "what is the step manifest?" doesn't need to know the type of `event_type` or that the default for `items` is a factory. This belongs in `reference/event-types.md`. The Concepts page should describe the payload **shape**, not the Pydantic spelling.

**WARNING — DuckDB / `read_parquet('results/runs/**/*.parquet')` usage assumes analyst tooling familiarity.**
The Querying section dives straight into DuckDB SQL with no setup. For a Concepts page this is fine *only* if the reader already knows DuckDB; otherwise it's intimidating. At minimum: drop a sentence — "These examples use DuckDB; substitute any tool that reads Parquet (Polars, pandas, Spark). See [Parquet schema](../reference/parquet-schema.md) for the full column list." Better: lead with the RunStore example (Python — the project's recommended entry point) and demote DuckDB.

**SUGGESTION — The mermaid diagram uses internal class names (`ParquetSubscriber`, `EventLog`) without saying what they are.**
A reader who hasn't read `three-stores.md` first sees acronyms. Either expand the node labels ("Parquet writer", "Event log") or precede the diagram with one sentence linking to `three-stores.md`.

**SUGGESTION — "Compliance" bullet (line 13) over-promises for a test engineer audience.**
"Auditors need to know the full test plan" is true in regulated industries, but most Litmus users are bringup engineers. Move "Compliance" to a more measured framing — "Audit trails" or "Regulated environments" — and keep "Yield analysis" and "Coverage tracking" as the two universal motivations.

---

## Accuracy

**CRITICAL — The Pydantic snippet for `StepsDiscovered.items` is WRONG.**
Page (line 22):
```python
items: list[dict[str, str | None]] = Field(default_factory=list)
```
Actual source (`src/litmus/data/events.py:475`):
```python
items: list[dict[str, str | int | None]] = Field(default_factory=list)
```
The mixed string/int payload is load-bearing — `step_index`, `vector_index`, and `vector_count_planned` are **ints**, and the type annotation reflects that. The page's annotation misses `int`, contradicting the comment on the next line in the source ("Mixed string/int payload — strings for code identity… ints for the collection-time-assigned step_index, vector_index, vector_count_planned").

**CRITICAL — The per-item field table is INCOMPLETE.**
Page lists 6 fields (`node_id`, `name`, `file`, `module`, `class_name`, `function`). Actual `CollectedItem` model (`src/litmus/data/models.py:320-348`) declares **12** fields:
- `node_id`, `file`, `module`, `class_name`, `function` ✓
- `markers` — MISSING
- `step_path` — MISSING
- `parent_path` — MISSING
- `step_index` — MISSING
- `vector_index` — MISSING
- `vector_count_planned` — MISSING

There is **no `name` field** on `CollectedItem` — the page invents it (line 31). The actual key for the test function name is `function` (which is also in the page). Either drop `name` or re-derive what the page meant.

The omissions are not cosmetic: `step_path`, `parent_path`, `step_index`, `vector_index`, `vector_count_planned` are exactly the columns the manifest reconciliation depends on (`_event_accumulator.py:122-133` reads `markers` and `vector_count_planned` from each item; `_row_helpers.py:807` matches executed-vector keys against `(step_path, vector_index)`). The page underplays how much identity is carried at collection time.

**CRITICAL — "`ParquetSubscriber` caches the discovered items in memory" claim is misleading.**
Line 47: "ParquetSubscriber caches the discovered items in memory."
Actual: the canonical accumulator is `EventAccumulator` in `src/litmus/data/backends/_event_accumulator.py`, which is the **single projection** used by **both** `ParquetSubscriber` AND the runs daemon's in-flight overlay (see the class docstring at `_event_accumulator.py:54-87` and lines 73-77 explicitly: "the test runner's parquet writer (`ParquetSubscriber`) and the runs daemon's live overlay use this same projection so the finalized parquet and the in-flight overlay can never drift"). Attributing the cache to `ParquetSubscriber` only is wrong — it's also why the in-flight UI can show planned vs executed counts mid-run. This is a substantive architectural point the page erases.

**WARNING — "fires after instruments connect but before any steps execute" — true today, but worth verifying the timing claim isn't load-bearing.**
Source comment (`events.py:460`) does say "Emitted after instruments connect, before steps execute" and `pytest_plugin/__init__.py:259-260` orders the emits in `_emit_session_start_events` as `SessionStarted → RunStarted → InstrumentConnected → StepsDiscovered`. So the claim is accurate. However, the page should make it clear this is **per-run**, not per-session — the source docstring also says "One event per run". The page doesn't say that anywhere, and that's a useful invariant for anyone trying to write a subscriber.

**WARNING — Mermaid diagram label "{run}.parquet (record_type='step' + 'measurement' rows)" is OK but the flow itself drops nodes.**
The diagram shows `pytest collection → StepsDiscovered → EventLog → Arrow IPC AND ParquetSubscriber → Parquet`. In reality, every other event type (RunStarted, StepStarted, MeasurementRecorded, StepEnded, RunEnded) also flows through this same pipeline, and the Parquet write is triggered by **`RunEnded`**, not by `StepsDiscovered` arriving. The current diagram suggests StepsDiscovered → parquet is a direct path, which it isn't. Recommend: re-label the diagram "Manifest reconciliation at run end" and show that the StepsDiscovered payload sits in `EventAccumulator` until `RunEnded` triggers the write, which is when `_append_not_started` runs.

**WARNING — "After `RunEnded`, the subscriber compares the discovered items against actually-executed steps" — true but the comparison key is non-obvious.**
The page should mention that the reconciliation is keyed on `(step_path, vector_index)` (see `_row_helpers.py:804-807` and `_append_not_started` at line 910), not on `node_id` alone. This matters because parametrize variants share `node_id` parts but differ in `vector_index`, and the reconciliation has to identify unrun **vectors** of partially-run sweeps, not just unrun **items**. The page's current framing ("Missing steps get synthetic rows") suggests it's a node_id set-difference, which would over-count for partially-executed sweeps.

**WARNING — Python snippet at line 102-106 uses `step['step_outcome']` but `RunStore.get_steps()` returns rows with key `outcome`, not `step_outcome`.**
`src/litmus/data/run_store.py:234-245`:
```python
return self._flight_query(f"""
    SELECT step_index, step_name, step_path, outcome, ...
    FROM steps
    WHERE run_id LIKE '{_sql_escape(prefix)}%'
    ORDER BY step_index
""")
```
The returned dict has `outcome`, not `step_outcome`. Reader copying the snippet hits a `KeyError`. Fix: `print(f"{step['step_name']}: {step['outcome']}")`.

**SUGGESTION — `Outcome` enum reference: page says "there is no `not_started` literal" but the codebase actually uses `"never_ran"` as the display string in `cli.py:678-679`, and the model docstring at `data/models.py:93` says "there is no `Planned` value".**
The page introduces a third term — `not_started` — that's only used in the `events.py:463` docstring and the `_append_not_started` function name. Pick one operator-visible term for the never-ran state (the UI uses "Never Ran" — `ui/shared/components.py:202, 247`) and tell the reader: "the field-missingness IS the receipt; the display layer renders the row as **Never Ran**". Be explicit that `not_started` is an *internal* function-naming convention, not an outcome value.

**SUGGESTION — "EventLog" label in the mermaid diagram is the class name; users see `EventStore` more often.**
`EventLog` exists (`src/litmus/data/event_log.py`) but the public-facing concept in `three-stores.md` and the API is `EventStore`. Either link the abbreviation back to its definition or use `EventStore` to match the cross-doc vocabulary.

---

## Gaps

**CRITICAL — The page never explicitly names what a "manifest entry" looks like as a parquet row.**
The "Storage" section bullets the columns but doesn't show a concrete row. A Concepts page on the step manifest should show a 2-row sample — one for an executed step, one for a never-ran step — so the reader sees what NULL-outcome rows look like in context. Without this, the "Never ran" semantic stays abstract.

**WARNING — Doesn't explain why the manifest's NULL outcome is preferable to a `not_started` / `planned` enum value.**
This is the *interesting* design decision on the page, and `outcomes.md:73-80, 93-97` and `models.py:93-98` explicitly defend it. The Concepts page should explain the rationale in one paragraph — "Field-missingness IS the receipt — there's no ambiguity about whether the outcome was deliberately cleared or never set, and the cascade rules at `escalate_outcome` treat `None` as 'below severity 0' automatically." Today the page just says "there is no `not_started` literal" in passing on line 71.

**WARNING — Doesn't address partial-sweep reconciliation.**
The most complex case the manifest handles is a parametrize sweep that runs 2 of 3 variants before the run ends. The page covers "step never ran" but doesn't cover "step ran for some vectors but not others", which is what `_append_not_started`'s `executed_vectors` keying handles (`_row_helpers.py:910+`). For a parametrize-heavy reader, the unrun-vector case is the differentiator.

**WARNING — No mention of class containers and how the manifest treats them.**
`step-hierarchy.md` covers class containers extensively. The step manifest's reconciliation has to deal with class iterations as separate manifest entries (each with its own `vector_index`). The page is silent on this — a reader who's just absorbed `step-hierarchy.md` will be left wondering whether class containers appear in the manifest at all and whether the never-ran rule applies to them.

**WARNING — No mention of multi-DUT / slot-orchestrated runs.**
A multi-slot run has one StepsDiscovered per slot subprocess, not one for the whole orchestrator session. This is non-obvious from the page's "fires after instruments connect, before any steps execute" framing. For a Concepts page, name it: "In multi-slot runs each slot's subprocess emits its own `StepsDiscovered` against its own collected items — slot orchestration is a separate concern, see [Multi-DUT execution]."

**SUGGESTION — Page never addresses the operator UI surfacing.**
"Never Ran" is a UI label (`ui/shared/components.py:202, 247`). A test engineer reading this page wants to know: "Where will I *see* these rows?" Answer: the Steps tab on the run-detail page renders them with a `bg-slate-100` chip. One sentence linking to a screenshot or a UI page would close the loop.

**SUGGESTION — Doesn't say what export formats (STDF, ATML, etc.) do with manifest rows.**
The exporters in `src/litmus/data/exporters/` consume the same rows. `atml.py:40` references "(never ran) — handled at the call site since it's a None check". Worth one line: "Exporters (STDF, ATML, CSV, …) propagate the NULL outcome — STDF emits `OPT_FLAG` bit unset; ATML emits an empty `OutcomeValue`; CSV writes blank." Or link to an explicit page.

---

## Cross-links

**CRITICAL — Missing link to the sibling Concepts page `step-hierarchy.md`.**
That page covers `step_path` / `parent_path` / `vector_index` and explicitly says (line 3): "Pair it with… [Step Manifest](step-manifest.md) for the planned-vs-executed reconciliation." The link is one-way today — `step-hierarchy.md` points to this page, but this page doesn't link back. Every reference to `vector_index`, `step_path`, `parent_path`, or class containers on this page should resolve to `step-hierarchy.md`.

**WARNING — Doesn't link to `concepts/outcomes.md` for the `None`-as-receipt semantic, despite the inline reference.**
Line 71 says "see `concepts/outcomes.md`" as raw text — it should be a real markdown link. Also, `outcomes.md` has the canonical defense of why there's no `Planned` value (`data/models.py:93-97` plus the outcomes-page commentary at lines 73-80). That's the place to anchor the rationale; today the rationale is duplicated weakly here.

**WARNING — Missing link to `reference/event-types.md`.**
The page reproduces the `StepsDiscovered` class definition and field table — that's exactly the territory `event-types.md` (referenced in `event-log.md`) should own. Add a link: "Full payload reference: [Event types reference](../reference/event-types.md#stepsdiscovered)".

**WARNING — Missing link to `reference/models.md`.**
The `CollectedItem` model has 12 fields including the manifest-critical `step_path`, `parent_path`, `step_index`, `vector_index`, `vector_count_planned`, `markers`. The page should link a reader who wants the full field list to `reference/models.md` (the Pydantic model index). Without it, a reader filling in the gaps from the page's incomplete 6-field table has to grep the source.

**SUGGESTION — "See also" list is correct but thin.**
Today (lines 117-119) it links to `event-log.md`, `parquet-schema.md`, `three-stores.md`. Add:
- `concepts/step-hierarchy.md` (the structural sibling — see above)
- `concepts/outcomes.md` (the verdict-semantics sibling — already cited inline)
- `concepts/why-event-sourcing.md` (why the manifest is reconstructed from events, not stored separately)
- `reference/event-types.md` (the payload reference)

**SUGGESTION — Two of the three "See also" link descriptions are off-target.**
- "Event log — how events get to Parquet" undersells `event-log.md`; that page is about the event log itself, not the parquet path. Try: "Event log — the durable typed-event stream this manifest rides on."
- "Three stores — EventStore, ChannelStore, ParquetBackend" is accurate but doesn't tell the reader why they'd click. Try: "Three stores — where the manifest lives (EventStore) and where it materializes (ParquetBackend)."

**SUGGESTION — Inline `record_type='step'` and `record_type='measurement'` references could link to the relevant section of `parquet-schema.md`.**
The page anchors `record_type` once on line 51 (good). The same anchor would be useful in the mermaid diagram label and in the Querying SQL — but those don't need to be linked, just visually consistent. Minor.
