# Meta-audit: Concepts section
**Date:** 2026-05-17
**Scope:** 17 pages

## Severity totals
| Page | ❌ | ⚠️ | 💡 |
|---|---|---|---|
| architecture | 6 | 20 | 15 |
| capabilities | 4 | 15 | 13 |
| capability-model | 5 | 15 | 16 |
| event-log | 3 | 15 | 14 |
| fixtures | 12 | 22 | 11 |
| flight-streaming | 5 | 16 | 14 |
| outcomes | 11 | 18 | 13 |
| platform-architecture | 4 | 18 | 21 |
| products | 4 | 19 | 13 |
| results-storage | 5 | 14 | 13 |
| sessions | 1 | 9 | 12 |
| stations | 5 | 18 | 14 |
| step-hierarchy | 3 | 16 | 14 |
| step-manifest | 6 | 19 | 13 |
| three-stores | 5 | 15 | 13 |
| why-event-sourcing | 2 | 12 | 14 |
| why-pytest | 2 | 9 | 13 |
| **Total** | **83** | **268** | **246** |

---

## Auditor accuracy check

Spot-checked the major recurring auditor claims against source:

### ✅ Confirmed correct

**`ParquetSubscriber` does not exist as a class.**
`grep -rn "class ParquetSubscriber" src/litmus/` → 0 hits. The real machinery: `materialize_run_to_parquet()` function (`src/litmus/data/backends/parquet.py:637`), called from `_runs_duckdb_daemon.py:1426` against an `EventAccumulator` from `AccumulatorPool`. There IS a stale internal docstring comment in `_event_accumulator.py:5` that still says `ParquetSubscriber` — that's a stale comment in source, not a real class. Audit hits on at least 4 concept pages (architecture, three-stores, why-event-sourcing, outcomes).

**`LiveRunsSubscriber` does not exist.**
No class by that name anywhere in `src/litmus/`. The runs daemon uses `AccumulatorPool` directly. Multiple concept pages claim this fictional class.

**`sessions/sessions.json` does not exist.**
No file or directory matches anywhere in source. Sessions are derived from events at query time. Both `results-storage.md` and `three-stores.md` claim it.

**`SpecBand` field is `when:`, not `conditions:`.**
`src/litmus/models/capability.py:197` — `when: dict[...]` on SpecBand. `conditions:` is the top-level field on `Capability` (line 449). Two different fields, the page conflated them.

**`pytest-mock` is not a Litmus dependency.**
Not in `pyproject.toml`. Mocks use `unittest.mock.patch.object` directly via `src/litmus/execution/mocks.py`.

**`litmus_retry` IS a Litmus marker (not "ecosystem only").**
`LITMUS_MARKER_NAMES` at `src/litmus/pytest_plugin/markers.py:30` contains `litmus_retry`. The page's self-contradiction (says retries are ecosystem, then lists `litmus_retry` as a Litmus marker) is real.

**Real product YAML uses `pin:` (singular) on a characteristic, not `pins:` (list).**
Verified `examples/05-product-spec/products/buck_3v3.yaml`:
- Line 9: `pins:` is the product's top-level pin list
- Lines 27, 35, 43: `pin: TP_VIN` on individual characteristics
The page leads with `pins: [VIN]` (list form on a characteristic) — wrong for the common case.

**Two `_internal/explorations/` link leaks from public docs.**
`results-storage.md:72` and `event-log.md:186` both link to `../_internal/explorations/api-stability-and-versioning.md`. Confirmed.

**`capabilities.md` and `capability-model.md` substantially overlap.**
367 + 319 lines, both cover the four-typed-collection model (signals/conditions/controls/attributes). Different naming, separate audiences, no cross-link in either direction. Auditor correctly flagged this as load-bearing.

---

## Cross-page patterns

### Pattern 1: Stale architecture names (4+ pages)
Multiple concept pages still describe the codebase before the daemon refactor:
- `ParquetSubscriber` class → it's now `materialize_run_to_parquet()` function called inside the runs daemon
- `LiveRunsSubscriber` → doesn't exist; runs daemon uses `AccumulatorPool` directly
- "In-process subscriber" → materialization now happens in the daemon, not the runner process

This is one cohesive drift: the docs describe a subscriber-based architecture that was refactored to a daemon + free-function model. The "crash safety" and "live updates" narratives on multiple pages are built on the wrong process model.

Affected: architecture.md, three-stores.md, why-event-sourcing.md, outcomes.md, results-storage.md, step-manifest.md, fixtures.md (probably others)

### Pattern 2: Fictional files in storage layouts
`sessions/sessions.json` is named in storage trees that depict the canonical results dir. No such file exists. The page authors copied this from each other and never verified against an actual `~/.local/share/litmus/data/` listing. Affected: results-storage.md, three-stores.md.

### Pattern 3: Field-name confusion across YAML schemas
- `SpecBand.when` vs `Capability.conditions` — page authors use them interchangeably in prose, breaking real-world YAML
- `pin:` singular vs `pins:` list on a characteristic — pages teach the wrong form
- `_base.yaml` vs `stations/types/<id>.yaml` for station types — pages teach a layout that doesn't load

### Pattern 4: Audience pitched at framework contributors, not test engineers
Several concept pages read like architecture notes for plugin developers — `_step_stack`, `callspec.params`, `_stamp_container_outcome`, "WAL HARD contract", "claim-check", "Arrow IPC" appear in user-facing prose without any test-engineer on-ramp. Worst offenders: flight-streaming, step-hierarchy, event-log. The test engineer who is the natural Concepts reader cannot use these pages.

### Pattern 5: First-use cold drops, especially `verify` / `context` / `logger`
The plugin's most-used fixtures appear in code examples on Concepts pages with no link to their definition. A reader landing on `platform-architecture.md` from a search engine has no path to `reference/litmus-fixtures.md`. Pattern repeats across architecture, platform-architecture, fixtures, step-hierarchy, stations.

### Pattern 6: Substantially duplicate sibling pages with no cross-links
- `capabilities.md` ↔ `capability-model.md` (substantially same territory)
- `results-storage.md` ↔ `three-stores.md` (overlapping; same fictional `sessions.json`)
- `event-log.md` ↔ `why-event-sourcing.md` (one motivates, one defines; no link from why → what despite reciprocal link from what → why)

Pattern matches the `connect.md` / `connect-api.md` / `capability-schema.md` / `capability-examples.md` smell from earlier reorg. Sibling pages with overlapping content and no clear contract about who owns what.

### Pattern 7: Public-docs links leaking into `_internal/`
Two confirmed leaks. The renderer doesn't bundle `_internal/` so these are dead links in any wheel-installed copy of the docs.

### Pattern 8: Outcome enum values + ladder ordering written inconsistently
`outcomes.md` alone has three different orderings of the seven Outcome values across the page; sibling pages have one-line glosses of DONE that contradict the canonical `outcomes.md` definition. The outcome ladder also reordered recently (`skipped < done < passed`, not `passed < skipped < done`) — drift hasn't fully landed across siblings.

### Pattern 9: Test phase / station YAML misinformation
`stations.md:149` says test_phase is station-level and gates mocks — both wrong. test_phase is per-run (CLI/env/profile), and mocks aren't phase-gated, they only demote the data stamp. Repeats the same drift that the tutorial section had on `from-mocks-to-hardware.md`.

### Pattern 10: Stale source-file line numbers
`outcomes.md` was particularly bad — 9 separate `file:line` references were stale (off by 470+ lines for hooks.py, files renamed, etc.). Source files moved; the doc references didn't.

---

## False positives in auditor reports

**`why-pytest.md`'s `litmus_retry` "self-contradiction" needs a careful read.**
The page says retries are pytest-ecosystem plugins, then lists `litmus_retry` as a Litmus marker. The auditor flagged this as a contradiction — but `litmus_retry` does translate to `pytest-rerunfailures` under the hood (`src/litmus/pytest_plugin/retry.py`). So the page is *technically* correct that the underlying retry MECHANISM is ecosystem, while the MARKER is Litmus's. The page wording is imprecise but not strictly wrong. The auditor's flag is fair — it reads as a contradiction even if it's not one — so the fix is rewording, not correction.

**None of the other CRITICALs appear to be false positives.** The big architecture drift (ParquetSubscriber, LiveRunsSubscriber, sessions.json) is real; the field-name claims (when vs conditions, pin vs pins) all checked out; the duplication patterns are visible in `wc -l` output.

---

## Recommended fix order (after all sections audited)

These fix in batches across all sections — like the tutorial meta-audit's Bug A pattern. Sweep + verify:

**Sweep 1: Architecture rename**
- `ParquetSubscriber` → `materialize_run_to_parquet` (function) + daemon
- `LiveRunsSubscriber` → `AccumulatorPool` (or just drop the name)
- "in-process subscriber" → "runs daemon"
- Multiple concept pages need their process-model narrative redrawn

**Sweep 2: Fictional files**
- Remove every `sessions/sessions.json` reference from storage trees
- Verify the storage tree on an actual install once; replace with that

**Sweep 3: YAML schema field names**
- `SpecBand.when:` (band-matching) vs `Capability.conditions:` (top-level) — never confuse them
- `pin:` (singular) on characteristic, `pins:` (list) on product
- `stations/types/<id>.yaml` (not `stations/_base.yaml`)

**Sweep 4: `_internal/` link leaks**
- 2 confirmed; sweep all sections

**Sweep 5: Cold first-use of `verify`/`context`/`logger`**
- Same pattern as tutorial; add links to `reference/litmus-fixtures.md`

**Per-page fixes (after sweeps):**
- Resolve sibling-page duplication (capabilities/capability-model, results-storage/three-stores)
- Audience pitch on framework-contributor pages (flight-streaming, step-hierarchy)
- outcomes.md stale line numbers + inconsistent orderings
- stations.md test_phase misinfo

---

## Consistency check (added per ongoing user request)

The Concepts section has internal contradictions:
- **DONE outcome:** `outcomes.md` defines it one way; `reference/models.md` defines it differently. Single source of truth needed.
- **Storage layout:** `results-storage.md` and `three-stores.md` both depict the canonical layout differently, and both include a phantom `sessions/` subdir.
- **`SpecBand.when:` vs `conditions:`:** prose in `capabilities.md` and `capability-model.md` uses them interchangeably; YAML examples vary.
- **Process model:** different pages describe the materializer as in-process (old) vs daemon-resident (current). Pick the current truth and propagate.
