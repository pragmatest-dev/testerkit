# Post-0.2.2 work plan (cross-session)

**Status:** living plan (2026-06-27). The durable index of open work after 0.2.2 merged to
main (`35b7572f`). The harness task list mirrors this within a session; **this file is the
cross-session source of truth.** Each unit points at its design doc.

## 0.3.0 — at-rest reshape (BREAKING; one `schema_version` 2.0→3.0 bump)
Branch `feat/0.3.0-at-rest-reshape`. All sub-changes touch the same core files
(`schemas.py`, `_row_helpers`, `_event_accumulator`, `_accumulator_pool`,
`_runs_duckdb_daemon`, `parquet.py`, `steps_query`) → land as ONE coordinated reshape, not
serial. Migration: regen pre-release (`rm -rf data/`); read-time-adapt later. See
`0.3.0-at-rest-reshape.md` + `schema-versioning-migration.md`.

- **De-fuse: one row per execution + source `step_retry`** (task #7) — `runs-execution-model.md`
  (de-fuse section). Producer (stamp `StepStarted.retry` from pytest `item.execution_count` —
  verified NOT sourced today) + accumulator (key `(step_path, step_retry, vector_index,
  vector_retry)`) + schema (add `step_retry`; drop rollups `retry_count`/`has_measurements`/
  `vector_count`; remove `retry_aware_rollup`). FPY becomes real. **First/in-progress unit.**
- **C5: instruments → `list<struct>` + materialized table** (task #3) — `0.3.0-at-rest-reshape.md`.
- **C4: `uut_serial` → `uut_serial_number`** (task #4) — `0.3.0-at-rest-reshape.md`.
- **Drop `parent_path`** (derive from `step_path − step_name`) (task #8).
- **File-metadata cruft** (task #10): drop `profile_facets_json` (dead), `step_results`
  redundancy, `litmus_version`/`schema_version` dups in the blob.
- **C3: `schema_version` as migration key + version-aware ingest** (task #5) —
  `schema-versioning-migration.md`. Stamp per store; structure ingest to branch on version
  (additive-later is fine; don't foreclose it).

## 0.4.0 — deeper analytics (read/query layer, non-breaking)
`0.4.0-analytics-metrics.md`. True Cpk/Cp (within-σ), per-measurement SPC control charts,
yield cross-tab by station/fixture/operator/shift, generic `pareto(by=…)`, refinements. Hard
rule: every metric ships with refreshed screenshots + a curated demo dataset. (Jupyter not
scoped — natural fit over the Query API if wanted.)

## Instrument access model (unscheduled; separate subsystem)
`instrument-access-model.md`. Step-grain reservation ("lock around the step yield"),
connection-vs-reservation split, recursive locks + timeout (`-1`=wait-forever, separate
liveness watchdog for dead holders).
- **Reservation events** (task #11) — `instrument.reserved`/`released` → event-sourced
  utilization (superset of run inventory; closest asset-utilization number).
- **Read-only observe mode** (task #12) — writers lease, readers subscribe to channels (no lock).

## Reference (durable)
- Working principles — `development-axioms.md`
- Runs data model map — `runs-architecture-map.md` (`run→step→vector→measurement`)
- Migration strategy — `schema-versioning-migration.md`
- Precedence when sources disagree: **source code > repo design doc > agent memory.**
