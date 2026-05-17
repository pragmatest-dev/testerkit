# Page audit: docs/reference/outputs.md

**Quadrant:** Reference (CLI/HTML/PDF/JSON/CSV report formats from `litmus show` + `litmus export`)
**Audited:** 2026-05-17

---

> **Coordinator note:** The harness for this run did not expose the sub-agent
> dispatch tool, so the coordinator performed all six dimensions inline
> (Read + Bash only). Findings are produced in the same template the per-dimension
> agents would have used, against the same source-code anchors.

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 1 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 1 |
| Accuracy | 3 | 3 | 2 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **5** | **14** | **10** |

---

## Ordering

Reference pages are read non-linearly — the table on this page IS the page, and it currently sits below two long prose sections. Format-lookup is the primary task; everything else is context.

### ⚠️ WARNING — Format table is buried below ~50 lines of prose

The "Available formats" matrix (lines 46–58) is the load-bearing artifact of a "what output formats does Litmus produce" reference. A reader hitting this page from `reference/index.md` ("what `litmus show -f <fmt>` and `litmus export` produce for HTML / PDF / JSON / CSV") needs the format/command/install lookup first, then the explanatory subsections. Today the page opens with a pipeline diagram, a "what's always on" architectural aside, and two command sub-sections before the table appears.

**Suggested order:** (1) one-line orientation; (2) format table; (3) `litmus show` details; (4) `litmus export` details; (5) "what's always on" / three stores aside; (6) cloud-destination escape hatch; (7) internal-mechanism aside. The pipeline diagram and the "subscribers — internal mechanism" section are both context, not lookup; they belong below the lookup, not framing it.

### 💡 SUGGESTION — Sub-section flow inside `litmus show` / `litmus export` is fine, but the two are framed as siblings without explaining when to reach for which

`show` is for human consumption (renders Parquet); `export` is for machine consumption (replays events). That distinction is one sentence and would let a reader stop reading after the right sub-section. Currently both `### litmus show` and `### litmus export` open with one-line descriptions that don't draw this contrast — a reader has to read both before they know which is theirs.

---

## Voice

Reference voice is "the manual" — neutral, specific, no marketing softeners and no first-person framing. This page is mostly there but has a handful of editorial slips.

### ⚠️ WARNING — "What's always on" reads as a feature pitch, not a reference

Line 5–14: heading "What's always on" + the closing line "These three stores are the platform. They're populated automatically by every test run; there's no configuration knob to disable them." That's a value claim, not a fact a reference page needs to assert. A reference can simply say "Litmus writes three on-disk stores: events/ (Arrow IPC + DuckDB index), runs/ (sealed per-run Parquet), channels/ (Arrow IPC time-series). See [Three Stores](../concepts/three-stores.md)." The "no configuration knob to disable them" line in particular is the kind of editorialising a concept page (`three-stores.md`) is for.

### 💡 SUGGESTION — Hedge in the cloud-destinations section

Line 64: "Litmus does not ship a built-in transport in the current release — design with real requirements is deferred to a future release." Reference pages should state what is, not narrate roadmap decisions. Tighten to: "Litmus does not ship a built-in cloud transport. The parquet files in `runs/` are the contract; consumers run their own pipeline."

### 💡 SUGGESTION — "Industry" column header is editorial, not factual

Line 48 column "Industry" classifies formats as "Universal / Semiconductor / Scientific / NI/LabVIEW / Automotive / Aerospace/defense". These are positioning labels; they're not properties of the format the same way "Library" and "Install" are. Either drop the column or rename it "Typical domain" so readers don't read it as a hard claim about format applicability (HDF5 is used far outside "scientific", for example).

---

## Audience

Reference audience for this page: someone who already knows a Litmus run exists and wants to get bytes out of it in a specific format. They are NOT being onboarded.

### ⚠️ WARNING — Page assumes reader already knows about events / parquet / "the runs daemon" without anchoring them

The opening paragraph mentions "events/", "runs/", "channels/", "Arrow IPC", "DuckDB index", and "durable WAL" before introducing what `litmus show` or `litmus export` does. A reader who arrived from `litmus --help` or from `cli.md` to look up "how do I get a PDF?" hits a wall of storage-architecture vocabulary before getting to the command syntax. Move the storage detail down and lead with the task (rendering and converting stored runs).

### ⚠️ WARNING — "Subscribers — internal mechanism" section's audience is unclear

Lines 71–73 talk about `EventSubscriber`, `ParquetSubscriber`, `LiveRunsSubscriber`, entry-point registration, and a public-vs-internal protocol distinction. The audience reading a reference page on output formats almost certainly does not care about subscriber registration internals. If this section exists to head off "can I write my own exporter via entry_points?", say so explicitly in one sentence — don't name internal classes a user can't see in their site-packages.

### 💡 SUGGESTION — "Cloud destinations" section is for a different audience than the rest of the page

The rest of the page is for "I have a run, give me a file." The cloud-destinations section is for "I'm building a data pipeline / lakehouse ingest." That's a different reader. Either fold this into one line ("for lakehouse ingest, see [Lakehouse import](../integration/lakehouse-import.md)") or move it to a clearly-marked "Beyond `litmus export`" section at the end so format-lookup readers can stop scanning sooner.

---

## Accuracy

Code anchors verified against `src/litmus/cli.py` (`show` + `export` commands), `src/litmus/reports/core.py`, `src/litmus/data/exporters/*.py`, `src/litmus/data/subscribers/__init__.py`, `src/litmus/data/event_log.py`, and `pyproject.toml`.

### ❌ CRITICAL — `ParquetSubscriber` and `LiveRunsSubscriber` are not classes that exist in the codebase

Line 73: "The `EventSubscriber` class in `litmus.data.event_log` powers `ParquetSubscriber`, `LiveRunsSubscriber`, and the `litmus export` replay path."

`grep -rn "class ParquetSubscriber\|class LiveRunsSubscriber" /home/ryanf/repos/litmus` returns zero results. The names appear only in stale docstrings/comments (`src/litmus/data/events.py` line 5, `src/litmus/data/backends/_event_accumulator.py`, `src/litmus/data/backends/parquet.py` line 271, 561) and in older design documents under `docs/_internal/explorations/`.

The current architecture (`src/litmus/data/backends/parquet.py` line 555–563) is explicit:

> "Called by the runs daemon's event-dispatch loop when `RunEnded` lands. The daemon's `AccumulatorPool` already holds the run's `EventAccumulator`; the materializer writes its state to disk via `ParquetBackend`. **No subscriber class needed** — projection lives on the accumulator, writing lives here."

The actual `EventSubscriber` subclasses (`src/litmus/data/exporters/*.py`) are `CsvSubscriber`, `JsonSubscriber`, `AtmlSubscriber`, `StdfSubscriber`, `Hdf5Subscriber`, `TdmsSubscriber`, `Mdf4Subscriber` — i.e. exactly the seven `litmus export` formats, nothing else. Rewrite line 73 to reflect this: `EventSubscriber` powers the `litmus export` replay formats only; the runs daemon's parquet materializer does NOT inherit from it.

(Same wording appears verbatim in `docs/concepts/event-log.md` line 132 and `docs/_internal/audits/public-api.md` line 57 — those need the same fix, but they're out of scope for this page audit.)

### ❌ CRITICAL — Storage-path prefix `data/runs/` contradicts the rest of the docs (`results/runs/`)

Line 66: "**DuckDB / Polars / Pandas:** read directly from `data/runs/{date}/*.parquet` with `record_type` filtering."

Every other docs page uses `results/runs/{date}/...`:
- `docs/concepts/three-stores.md` line 19, 29, 39 (`results/events/...`, `results/channels/...`, `results/runs/...`)
- `docs/integration/lakehouse-import.md` line 4 (`results/runs/{date}/{timestamp}_{serial}.parquet`)
- `docs/reference/parquet-schema.md` — uses the same `results/` convention

The "data" name is the *project-local* override used by `litmus-dev`'s own `litmus.yaml` (`data_dir: data`), not the default. The actual default resolved by `src/litmus/data/data_dir.py` is `~/.local/share/litmus/data` (Linux) — i.e. `data` is the leaf, but the parent is platform-dependent. The docs convention `results/{events,runs,channels}/...` is the published shape. Standardise on `results/runs/{date}/*.parquet` here too, or — if the intent is to be honest about path variability — write it as `<data-dir>/runs/{date}/*.parquet`.

### ❌ CRITICAL — Diagram on lines 8–12 is wrong about the events store

Lines 7–12:
```
test execution
    └→ events/   (Arrow IPC + DuckDB index — typed events, durable WAL)
    └→ runs/     (sealed per-run Parquet — analysis-ready, lakehouse-readable)
    └→ channels/ (Arrow IPC — time-series instrument samples)
```

Two problems:

1. **"durable WAL" is editorial framing, not what this is.** The Arrow IPC files at `events/{date}/{session_id}-{pid}.arrow` are the canonical typed-event log; calling them a "WAL" implies they get rolled up into something else and then truncated. They don't — the events store IS the source of truth (see `docs/concepts/three-stores.md` line 13 "EventStore — Source of Truth"). The DuckDB file is the *index*, not the database; the IPC files are the database.
2. **The tree-branch glyph is wrong.** All three stores receive the same write side-by-side; the diagram draws them as nested children of a parent. The current ASCII would parse as `events/` having children `runs/` and `channels/` to a casual reader. Use three parallel arrows or just bullet points.

### ⚠️ WARNING — `litmus export` `-o` path produces a doubled prefix

Page shows `litmus export abc123 -f stdf -o exports/stdf/` (lines 39–43). Following that command:
- `src/litmus/cli.py` line 824: if `output_dir is None`, default is `f"exports/{fmt}"`.
- `src/litmus/data/exporters/csv_exporter.py` line 64: `self._output_dir = output_dir / "exports" / "csv"`. Same pattern in `json_exporter.py:47`, `atml.py:166`, etc.

So passing `-o exports/stdf/` materialises files under `exports/stdf/exports/stdf/...`. The examples shown will surprise users. Either the page should pass `-o exports/` (and let the exporter add the `exports/<fmt>` suffix) or the `-o` value should be a meaningful application root like `-o out/`. Worth fact-checking whichever is intended.

### ⚠️ WARNING — `litmus show -f csv` example omits the `-o` flag, which the page's own subhead implies is required

Line 28: `litmus show abc123 -f csv`. By `src/litmus/reports/core.py` line 232–240, when no `-o` is given the output writes to a directory derived from the CWD ("."), producing `./report_<id>.csv`. That works, but every other example in the block shows an explicit `-o` (`-o out/`, `-o reports/`, `-o result.json`). Either show `-f csv` with an `-o` for consistency or, conversely, drop `-o` from `-f json` for the same reason. The current asymmetry implies CSV is somehow special.

### ⚠️ WARNING — JSON sub-bullet in cloud-destinations example uses `INSERT INTO ... SELECT ... WHERE record_type = ...` shape that conflicts with the lakehouse page's recommended `EXCLUDE` + `DISTINCT` pattern

Line 67: "Snowflake / Databricks / Trino-Iceberg: copy parquets to your storage layer and ingest with an `INSERT INTO ... SELECT ... WHERE record_type = ...` split."

`docs/integration/lakehouse-import.md` does NOT recommend a simple `WHERE record_type=...` split — it uses `INSERT INTO runs SELECT DISTINCT ...` (because run identity is denormalised onto every row, not present as a `record_type='run'`), with `SELECT * EXCLUDE (...)` for steps and measurements. The two pages will confuse anyone who reads both. Either delete the SQL hint here and point only to the lakehouse page, or align it with the canonical pattern.

### 💡 SUGGESTION — "Library" column lists `stdlib` for `csv` and `json`

Lines 52–53 say csv and json use `stdlib`. In `src/litmus/reports/core.py` that's true (`import csv`, `import json`). But `litmus export -f json` uses `litmus/data/exporters/json_exporter.py`, which builds a structured event-mirroring document — same stdlib, but a different shape than `litmus show -f json` produces. The page conflates the two paths under one row. Consider splitting `litmus show` JSON (run summary) from `litmus export` JSON (event replay) — they produce different documents.

### 💡 SUGGESTION — `Semi-ATE-STDF` casing differs from `pip install litmus-test[stdf]`

Line 54 lists library "Semi-ATE-STDF" with install `litmus-test[stdf]`. `pyproject.toml` line 74 confirms the extra is `stdf` and the dependency is `"Semi-ATE-STDF>=0.1"`. Accuracy is fine; just note that "Semi-ATE-STDF" is the PyPI package name shown verbatim, while every other row shows a casual library name (`weasyprint`, `h5py`, `npTDMS`, `asammdf`). Use the PyPI name everywhere or the casual name everywhere — don't mix.

---

## Gaps

What a reader looking up "output formats" expects to find on this page that isn't there.

### ❌ CRITICAL — No mention of what a `-f json` document or `-f csv` file actually contains

The page tells you which commands and extras you need, but never describes the output shape. A reference page on output formats should at minimum tell you, for each format, what the top-level structure looks like and what one row / one element means. `litmus show -f csv` writes one row per measurement with columns `step_name, measurement_name, value, units, limit_low, limit_high, nominal, outcome, characteristic_id, dut_pin, instrument_name` (`src/litmus/reports/core.py:310–322`). `litmus show -f json` writes a single object with `run_id`, `dut`, `product`, `station`, `summary`, `measurements`, `instruments` keys (lines 258–301). None of that is on the page.

### ⚠️ WARNING — `litmus export` vs `litmus show` distinction is structural but never stated

These are not interchangeable: `show` reads the sealed Parquet and renders it; `export` replays the live event stream through an exporter. Practical consequences the page omits:
- `export` requires the events store; if events have been pruned (or never were on this machine), `export` returns "No events found for '<id>'." See `src/litmus/cli.py:828–830`.
- `show` works on the sealed parquet alone, after the events store is gone.
- `export` matches on a prefix that auto-detects `run_id` or `session_id`; `show` takes only `run_id`. See `src/litmus/cli.py:720–777` and `:799–800`.

That's the actual decision a reader has to make on this page; today they have to infer it from command names.

### ⚠️ WARNING — `--template` and template-resolution behaviour for HTML / PDF is undocumented here

`litmus show -f html` and `-f pdf` resolve templates from `reports/templates/{name}.html` (project) → `litmus/reports/templates/{name}.html` (built-in), and accept `-t <name>` to swap templates (`src/litmus/cli.py:610`, `src/litmus/reports/core.py:333–358`). The reference page on output formats never mentions this — the only reference to `-t` is on `cli.md`. For a reader trying to "customise the HTML report we ship to customers" this is the critical knob and it's missing.

### ⚠️ WARNING — No mention of `--env` for the `litmus show` (no `-f`) terminal path

The `show` block on lines 24–30 omits the terminal-display flags entirely. The `--env` flag in `src/litmus/cli.py:611` shows the captured environment snapshot. If the page is intentionally narrowing to "report formats only", it should say so and link to `cli.md` for the full surface. Today the omission reads as oversight.

### ⚠️ WARNING — Output filenames produced by each format / each command are not specified

Per source:
- `litmus show -f <fmt>` without `-o` writes to `./report_<run_id_short>.<ext>` (`reports/core.py:236–240`).
- `litmus export -f csv` writes `<output>/exports/csv/<run_id_short>.csv` (`csv_exporter.py:64,93`).
- `litmus export -f json` writes `<output>/exports/json/...` (`json_exporter.py:47`).
- `litmus export -f atml` writes `<output>/exports/atml/...` (`atml.py:166`).

Knowing where the bytes land is the entire point of a reference on output formats; the page never tells you.

### 💡 SUGGESTION — No mention of stability / versioning per format

Some of these formats are standardised (STDF V4, ATML/IEEE 1671) and some are Litmus-defined (the JSON / CSV shapes). Worth a sentence per format about whether the schema is industry-fixed or Litmus-fixed (and therefore subject to version bumps).

### 💡 SUGGESTION — No "how do I add a format?" answer

The "subscribers — internal mechanism" section says "do NOT register subscribers via entry points" but never tells the reader what they SHOULD do if they need a format Litmus doesn't ship (file an issue, fork, etc.). A reference page can punt this with a one-line "to request a new export format, open an issue at..." but right now it's just a closed door.

---

## Cross-links

### ❌ CRITICAL — `litmus export` is documented on this page but has no entry on `reference/cli.md`

`docs/reference/cli.md` documents `litmus show` (lines 119–179) but contains no section on `litmus export`. The CLI reference is supposed to be the exhaustive command catalogue (`reference/index.md`: "every `litmus <command>` and its flags"). A reader who comes to `cli.md` to look up `litmus export` will not find it. Either add a `### litmus export` section to `cli.md` and link this page over to it, or make `outputs.md` the canonical place for the `export` CLI surface and link from `cli.md`. The page-to-page contract needs to be explicit.

### ⚠️ WARNING — "Three Stores Architecture" link target uses outdated `ParquetSubscriber` terminology

Line 14 links to `../concepts/three-stores.md`. That target's content (e.g. line 11 "ParquetBackend ... `ParquetSubscriber` listens to events, builds rows, writes on RunEnded") matches the same stale naming flagged in the Accuracy section. A reader who follows the link to ground themselves in the architecture will land on terminology that contradicts the actual code. The cross-link is fine; the destination needs the same correction (out of scope for this audit, but worth noting because the link is load-bearing).

### ⚠️ WARNING — Lakehouse-import cross-link is one-way

Line 69 links out to `integration/lakehouse-import.md` ("Canonical recipes — see..."). That page does not link back to this one. The two pages partially overlap (both discuss "what's in the parquet" and "consumers run their own pipeline"); without the back-link a reader on `lakehouse-import.md` who wants to know about the canonical `litmus export` formats (CSV/JSON/STDF/...) won't discover them.

### ⚠️ WARNING — `litmus export`'s "event replay" mechanism is mentioned but no link is given to where events / event-types are documented

Line 34: "Converts a stored run to industry data formats by replaying its events through the format converter." The reader is owed a link to `reference/event-types.md` (which exists and is exhaustively complete) and `concepts/event-log.md`. Without it, "replaying its events" is jargon.

### 💡 SUGGESTION — `reports/templates/{name}.html` template resolution is documented in `cli.md` (line 179) but not linked from here

If you add the HTML-template gap (see Gaps section), link to the `cli.md` section that already covers template resolution rather than duplicating.

### 💡 SUGGESTION — `parquet-schema.md` link is missing where the page talks about "the parquet files in `runs/` are the contract"

Line 64 says "The parquet files in `runs/` are the contract" — that's literally `reference/parquet-schema.md`'s subject. Link it. Currently a reader gets the assertion without the schema definition that backs it up.
