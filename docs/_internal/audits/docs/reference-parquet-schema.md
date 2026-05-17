# Page audit: docs/reference/parquet-schema.md

**Quadrant:** Reference
**Audited:** 2026-05-17

> Note: The `Agent`/`Task` tool was not available in this environment, so the coordinator
> performed the six-dimension audit inline rather than dispatching subagents. Findings
> reflect direct Read/grep over the page and source code at the time of audit.

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 1 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 1 | 3 | 3 |
| Cross-links | 0 | 2 | 3 |
| **Total** | **4** | **13** | **15** |

---

## Ordering

A Reference page should be optimized for scanning and lookup, not narrative flow. Sections should be grouped by concern with the most-queried columns first.

### WARNING — "Discriminator" section appears before grain is summarised in scannable form

The opening prose explains the discriminator + grain in paragraph form (lines 3–10), then the "Discriminator" section (line 26) restates `record_type` as a one-row table. The intro paragraph already does the work; the dedicated table is a one-row table that contributes nothing new and breaks the "section per group" rhythm — readers expect every section to be a multi-column group. Either drop the section heading and inline the one-row table into the prose, or expand the section so it includes the per-record-type grain summary explicitly (which is currently buried in prose).

### WARNING — "Outcome values" and "Comparator values" tables live near the bottom, far from the columns that use them

`measurement_outcome` (line 203) and `limit_comparator` (line 212) are defined in the "Measurement core" and "Limits" sections respectively, but the legal value tables for both don't appear until lines 271–298 — separated from their column definitions by ~70 lines including spec traceability, signal-path, rollups, environment, and custom-metadata sections. A reader looking up "what does `errored` mean" has to scroll past unrelated material. Suggest either: (a) move "Outcome values" immediately after "Measurement core" / before "Limits"; or (b) inline a brief enumeration into the column row and keep the bottom tables as a deeper-dive cross-reference.

### SUGGESTION — "Rollup outcomes" precedes "Outcome values"

`step_outcome`/`vector_outcome`/`run_outcome` (lines 240–246) all use the same outcome vocabulary as `measurement_outcome`. The Outcomes table at line 271 covers all four columns, but the rollup section is placed in a separate group. Consider grouping all four `*_outcome` columns together (next to the Outcome values table) instead of stacking them between "Measurement signal path" and "Environment traceability".

### SUGGESTION — "Custom metadata" section is sandwiched between schema content and value tables

"Custom metadata" (line 256) introduces a new write API (`run_context.set`) in the middle of a column reference. It is the only section that has a runtime side effect (calling `set()` mutates a fixture). For a Reference page this jumps between "describe the columns" and "tell you how to write them" — separating it out below "File-level metadata" would keep the column list contiguous.

---

## Voice

Reference pages should be terse, present tense, declarative — column-centric, not narrative.

### WARNING — Casual second-person creeps into the discriminator section

Lines 32: "Every query starts here. To list steps: `WHERE record_type = 'step'`. … Both kinds: omit the filter." This is closer to How-to voice. A Reference page describes the column; the query advice belongs in a Querying section (which exists, lower down). Move or compress to a single example near the discriminator and keep prose declarative.

### SUGGESTION — Mixed use of "we"/"you" vs. third-person

The page is mostly third-person ("each row carries", "rows share grain"), which is right for Reference. But isolated lapses appear ("Every query starts here", "If you want…", and the imperative "Filter to the final execution with …" on line 310). Sweep through and normalise to declarative third-person ("Filtering to the final execution: `WHERE vector_retry = …`").

### SUGGESTION — Section subtitles use ad-hoc framing ("Who — operator", "What — DUT", "Where — station")

The W-prefix grouping ("Who/What/Where") is opinionated and not used elsewhere in the reference docs. It works as mental scaffolding but reads as informal. Either commit to it across the whole Reference suite (event-types, models) or drop the prefixes and keep the bare group names.

### SUGGESTION — Tone in column descriptions varies

Some entries are terse noun phrases ("Run UUID — primary key for the run"); others are full sentences with embedded prose ("Container path; empty for root steps. Enables tree reconstruction without joins.") The second style leaks design rationale into a column description. Pull rationale into a paragraph above/below the table when needed; keep the cell to one terse phrase.

---

## Audience

Target audience for a parquet schema reference: a test engineer or data engineer with SQL/Pandas literacy, looking up a column by name.

### WARNING — "ATML / IEEE 1671 alignment" section assumes ATML literacy

Lines 396–408 list ATML equivalents but never define ATML, IEEE 1671, or `UUT`/`uutPort`. A test engineer in semiconductors or aerospace may know these; a software-leaning user will not. Either link to a short concept page ("ATML, briefly") or add a one-line context sentence ("ATML / IEEE 1671 is the test-data interchange standard used by ATE platforms (TestStand, NI TestStand, Eggplant, …) for results portability.")

### SUGGESTION — `step_path` example assumes pytest class-based tests

Line 45 example: `TestPower/test_efficiency`. New users on bringup-tier tests typically write module-level functions only; the first example they see should reflect that ("`test_power_efficiency`" or "`tests/test_power.py::test_efficiency`"). Add the class form as a second example to show hierarchy.

### SUGGESTION — `step_markers` is described as "Marker payload summary" with no example

That description tells a user nothing about the column's actual content (JSON? CSV of marker names? full payload?). Either show a representative value or link to where the format is defined.

---

## Accuracy

Verified against `src/litmus/data/schemas.py`, `src/litmus/data/backends/_row_helpers.py`, `src/litmus/data/backends/parquet.py`, `src/litmus/data/models.py`.

### CRITICAL — `record_type` has THREE values, not two

The page (lines 3–6 and the "Discriminator" table on line 30) says `record_type` is one of `'step'` or `'measurement'`. The schema and writer disagree: `MeasurementRow.record_type: Literal["run", "step", "measurement"]` (`_row_helpers.py:117`), and `_build_run_row` writes `record_type="run"` (`_row_helpers.py:674`). The docstring on `MeasurementRow` explicitly says "Three row kinds" and documents the `run` row as "one row per run; carries run-level identity / DUT / station / fixture / environment context. Step and measurement columns are NULL." `ParquetBackend.save_test_run` always writes the run row first (`parquet.py:228`: "Always present, including for runs with no steps or measurements — the run row alone is the entire parquet").

Operator impact: every example SQL on the page that omits `WHERE record_type IN ('step','measurement')` will silently include the run row, which has NULL `step_*` and NULL `measurement_*` and will skew counts/averages. The "Yield by station" query on line 354 filters to `record_type = 'measurement'` so it's fine, but the missing third value means a user inheriting the docs will write `WHERE record_type = 'step'` and miss the run-level summary row entirely.

Fix: add a row for `record_type = 'run'` to the Discriminator table, expand the opening prose to say "one of three values", and document which columns are populated on the run row.

### CRITICAL — File-layout example uses a filename format that doesn't match the writer

Line 16: `{timestamp}_{serial}.parquet`. Line 326: `pq.ParquetFile("results/runs/2026-05-16/T143025Z_SN001.parquet")`.

The actual writer (`parquet.py:205`) builds the timestamp as `started_at.strftime("%Y%m%dT%H%M%SZ")` — i.e. `20260516T143025Z`, not `T143025Z`. The example file `T143025Z_SN001.parquet` has no date in the basename and would never be produced. Users copy-pasting will hit `FileNotFoundError`. Update both occurrences (line 326 and 339) to a realistic basename such as `20260516T143025Z_SN001.parquet`.

### CRITICAL — Doc claims `record_type = 'step'` for "planned but unrun" rows; code path is more nuanced

Line 5 says step rows include "one row per `(step_path, vector_index)` execution". The `_row_helpers.py:97-107` docstring is the more precise contract: "one per `(step_path, vector_index)` execution (or planned-but-unrun vector)." The page only mentions retries (line 8) but never tells readers that planned-unrun vectors are represented at all, nor how to distinguish "ran but no measurements" from "never ran". This matters because `outcome IS NULL` is the signal (per `Outcome` docstring, "Note on the 'never ran' case: there is no `Planned` value. … field-missingness IS the receipt"). Document this explicitly — it's a load-bearing contract for analytics consumers.

### WARNING — `schema_version` value is asserted as `"1.0"` "at time of writing" but the page is the canonical reference

Line 320: "`schema_version` | Schema version (`"1.0"` at time of writing — see `SCHEMA_VERSION` in `src/litmus/data/schemas.py`)". The hedge "at time of writing" is unusual for Reference — either link to the constant and don't quote a value, or quote the current value (verified: `SCHEMA_VERSION = "1.0"` in `schemas.py:25`) and update the page when it changes. Pick one stance.

### WARNING — Sibling `_steps.parquet` is not documented in the File layout

Line 14–22 shows the runs directory with only the unified parquet and the `_ref/` directory. The current writer also produces `{stem}_steps.parquet` (a sibling file containing the step manifest) — `read_step_results` (`parquet.py:763–778`) explicitly checks for it first ("new format") and falls back to JSON-in-file-metadata only for legacy files. New runs do produce a `_steps.parquet`. The Reference page should show it in the layout block.

### WARNING — Reference URI scheme is `file://_ref/…`, the page shows the legacy bare form

Lines 182–186 ("Storage format" column) show `_ref/{id}_scope_waveform.npz` as the column value. The current writer emits `file://_ref/...` URIs (`parquet.py:678–696`: "file:// URI or legacy _ref/ path"; the loader also handles `channel://` for streaming channel refs). The bare `_ref/` form is documented as "legacy" in the code. New runs will store `file://_ref/...`. Update the example column values and document that `is_file_reference` accepts both for backward compatibility.

### WARNING — `test_phase` is described as if it were a closed enum

Line 139: "`test_phase` | string | `production` / `characterization` / `development`". In the code (`_row_helpers.py:152`, `client.py:276`, `logger.py:397`) `test_phase` is `str | None` with no enum constraint; the three values are conventions, not validated. Either mark them explicitly as "conventional values (free string)" or convert to an enum and have the page reflect that.

### SUGGESTION — `measurement_value` field comment hedges scalar-only

Line 201: "Measured value (scalar; non-scalar payloads go to `_ref/` via `out_*`)". This is accurate but the framing implies you must split a measurement across two columns. In practice, large/structured measurement payloads ARE written through `out_*` plus a scalar `measurement_value` (the headline number). A short sentence clarifying "the `out_*` reference carries the full structure; `measurement_value` carries the scalar summary that gets judged against limits" would prevent confusion.

### SUGGESTION — "Daemon's `runs` view" mention is correct but vague

Line 310 mentions "the daemon's `runs` view, which already rolls `retry_count`". The view is real (`_runs_duckdb_daemon.py:1194`, `CREATE OR REPLACE VIEW runs`), and the rollup column `retry_count` exists on `runs_materialized` (`:380`, `:911`). But there's no cross-link to where this view is documented or how to query it. Either link to the relevant client/CLI page or add a one-line "via `litmus.client.LitmusClient` or `duckdb.connect(...)` against the daemon" pointer.

---

## Gaps

What a reader of a Reference page would expect to find but doesn't.

### CRITICAL — `record_type = 'run'` row not documented at all (see Accuracy)

This is the single largest gap. The page presents step + measurement as the universe; an entire row kind is invisible. Anyone querying without `record_type` filtering will pick it up and not know why their grouped query has an extra NULL row per run.

### WARNING — `profile_facets_json` file-level metadata key is not documented

`parquet.py:124` writes `metadata[b"profile_facets_json"] = json.dumps(profile_facets).encode("utf-8")` when profile facets are present. The page's "File-level metadata" table (lines 316–320) lists only `environment_json`, `litmus_version`, and `schema_version`. Add `profile_facets_json` so users of the profiles feature can read out which facets the run was tagged with.

### WARNING — `step_results` / `_steps.parquet` step-manifest store not documented

Beyond the column list, the per-run manifest (step results JSON in metadata or sibling `_steps.parquet`) is what enables planned-vs-actual reporting. Mention it in File-level metadata or in a dedicated "Step manifest" subsection.

### WARNING — `custom_*` column behaviour vs. `in_*`/`out_*` is under-explained

Line 268: "Those become Parquet columns prefixed `custom_*` with inferred types." But: (a) `set()` on `run_context` is run-scoped, not per-step; what value lands on a step row vs. a measurement row? (b) what happens if two tests `set()` the same key with different types — does the row helper still infer from "first non-None", and does that produce mixed-type Arrow errors? The `_build_write_schema` and `table_from_rows` paths surface the latter as `pa.ArrowInvalid`, but the page doesn't warn the user.

### SUGGESTION — No "Null semantics" subsection

A Reference page should answer "when is column X NULL?" as a first-class question. Some answers are sprinkled (`slot_id` NULL for single-DUT; `step_class` NULL for module-level functions; `limit_low` NULL if no lower limit) but many are not (`session_id` — ever NULL?; `dut_serial` — required by `MeasurementRow` so non-NULL; `run_outcome` on planned-unrun rows — NULL?). A short table or a per-section "Nullable when …" note would help analytics consumers.

### SUGGESTION — Type hints don't show nullability

Every "Type" column shows the Arrow type (`string`, `int64`, `timestamp[us, UTC]`) but never indicates whether the column is nullable. Per the canonical schema, every field has a default-null type. Either add a "Nullable" column or annotate the few non-null fields explicitly.

### SUGGESTION — No "Reading from URLs" / "Reading remote parquet" guidance

DuckDB/Polars/Pandas all support reading from s3:// and http://. A single sentence ("All queries here use a local glob; the same SQL works against `s3://bucket/runs/**/*.parquet` once your DuckDB has `httpfs` loaded") would buy a lot of operator goodwill. Optional, but a common follow-up question.

---

## Cross-links

### WARNING — Mention of `RUN_ROW_SCHEMA` does not link to source

Line 10: "The canonical schema lives at `src/litmus/data/schemas.py` (`RUN_ROW_SCHEMA`); this page is a human-readable mirror of it." File path is plain text — a `[link](../../src/litmus/data/schemas.py)` (or a doc-renderer-supported source link) would make verification one click.

### WARNING — No back-link to the "Step manifest" concept

`docs/concepts/step-manifest.md` links INTO this page (line 51 and 60, 118), but this page has no outbound link to that concept. The page's intro establishes step + measurement grain — concepts/step-manifest.md is exactly the deeper context. Add it to "See also".

### SUGGESTION — `step_outcome`/`run_outcome` cascade reference missing

`escalate_outcome` is referenced in `Outcome.severity` (`models.py:114`) and matters for understanding why a step row's `step_outcome` can differ from a child measurement's `measurement_outcome`. A short pointer ("Rollup is worst-wins; see `Outcome.severity` and `escalate_outcome` in `litmus.data.models`") would help.

### SUGGESTION — Profiles page link missing

`profile_facets_json` is filed under profile-tagged runs (`docs/how-to/profiles.md` exists). When the metadata gap above is fixed, a link to profiles.md would close the loop.

### SUGGESTION — `litmus.client.LitmusClient` not referenced

The page's whole "Querying examples" section uses raw DuckDB/Pandas. The official client (`docs/reference/client.md`) wraps the same queries and exposes `RunsQuery` / `StepsQuery` / `MeasurementsQuery`. A "See also: [`client`](client.md) for the typed Python API over these queries" line in the See also section ties this back into the supported surface.
